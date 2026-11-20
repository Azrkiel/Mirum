"""Submit reviewed engineering jobs and record storage case evidence."""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

import httpx

from harness.api import Api, TOKEN_HEADER

_BEAM_EXAMPLE = Path(__file__).resolve().parents[2] / "examples/engineering/cantilever.json"
_BEAM = "rectangular_cantilever"
_REGEN = "regen_chamber"

# Mirror backend/engineering/regen_presets.py so the offline harness can use
# the reference preset without importing the backend's Pydantic models.
_REGEN_REFERENCE = {
    "family": _REGEN,
    "units": "mm",
    "assumptions": "geometry_only_no_gas_dynamics_thermal_or_structural_analysis",
    "chamber_radius_mm": 35.0,
    "chamber_length_mm": 40.0,
    "convergent_length_mm": 50.0,
    "throat_radius_mm": 20.0,
    "divergent_length_mm": 80.0,
    "exit_radius_mm": 45.0,
    "wall_thickness_mm": 7.0,
    "channel_count": 32,
    "channel_radius_mm": 1.0,
    "channel_twist_rad_per_mm": 0.012,
    "manifold_radius_mm": 1.5,
    "end_margin_mm": 8.0,
    "voxel_size_mm": 0.4,
}


def _redact_response(text: str, response: httpx.Response) -> str:
    token = response.request.headers.get(TOKEN_HEADER)
    if token:
        encoded = json.dumps(token, ensure_ascii=False)[1:-1]
        text = text.replace(encoded, "***").replace(token, "***")
    return text


def _expect_status(response: httpx.Response, status: int, operation: str) -> None:
    if response.status_code != status:
        excerpt = _redact_response(response.text, response)[:160]
        message = f"{operation} returned HTTP {response.status_code}: {excerpt}"
        raise AssertionError(_redact_response(message, response))


def _capability(api: Api, capability_id: str) -> dict:
    # Api.capabilities() returns parsed JSON and discards the error body.
    response = api.get("/engineering/v1/capabilities")
    _expect_status(response, 200, "Read capabilities")
    for entry in response.json()["capabilities"]:
        if entry["id"] == capability_id:
            return entry
    raise AssertionError(f"Expected capability {capability_id} is missing")


def _leaves(payload: dict, prefix: str) -> list[str]:
    # Match backend/engineering/capabilities.py:186 and the release smoke.
    names = []
    for key in sorted(payload):
        value = payload[key]
        name = prefix + key
        if isinstance(value, dict) and value:
            names.extend(_leaves(value, name + "."))
        else:
            names.append(name)
    return names


def _review_brief(api: Api, capability: dict, inputs: dict) -> dict:
    made = api.create_draft({"capability": capability["id"], "input": inputs})
    _expect_status(made, 201, "Create draft")
    current = api.get_draft(made.json()["draft"]["draft_id"])
    _expect_status(current, 200, "Read draft")
    record = current.json()
    draft = record["draft"]
    fields = list(capability["review_fields"])
    prefix = capability.get("review_fields_prefix")
    if prefix:
        fields.extend(_leaves(draft["content"]["specification"], prefix))
    finalized = api.finalize({
        "draft_id": draft["draft_id"],
        "draft_revision": draft["revision"],
        "brief_sha256": record["brief_sha256"],
        "acknowledged_fields": list(dict.fromkeys(fields)),
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
        "reviewed_by": "storage-e2e",
    })
    _expect_status(finalized, 201, "Finalize brief")
    return finalized.json()


def beam_brief(api: Api, variant: str = "reference") -> dict:
    """Finalize the cantilever example or its low-allowable variant."""
    if variant not in {"reference", "low_allowable"}:
        raise ValueError("Unknown beam variant")
    # The beam draft takes the brief-content shape (briefs.BriefContent), not the legacy
    # /engineering/generate body in examples/engineering/cantilever.json.
    capability = _capability(api, _BEAM)
    example = json.loads(_BEAM_EXAMPLE.read_text(encoding="utf-8"))
    spec = example["specification"]
    inputs = {
        "schema_version": "engineering_brief_v1",
        "capability": {
            "id": capability["id"],
            "version": capability["version"],
            "numerical_policy_version": capability["numerical"]["version"],
            "resource_policy_version": capability["resources"]["version"],
        },
        "requirements": {
            "units": spec["units"],
            "length_mm": spec["length_mm"],
            "width_mm": spec["width_mm"],
            "dimensional_tolerance_mm": spec["dimensional_tolerance_mm"],
            "tip_force_n": spec["tip_force_n"],
            "max_tip_deflection_mm": spec["max_tip_deflection_mm"],
            "material": {
                "youngs_modulus_mpa": spec["youngs_modulus_mpa"],
                "allowable_stress_mpa": spec["allowable_stress_mpa"],
                "source_kind": "illustrative",
                "reference": "User-reviewed illustrative aluminium inputs; not certified stock properties",
            },
            "assumptions": spec["assumptions"],
        },
        "mode": "bounded_thickness",
        "candidate_heights_mm": example["candidate_heights_mm"],
    }
    if variant == "low_allowable":
        inputs["requirements"]["material"]["allowable_stress_mpa"] = 1.0
    return _review_brief(api, capability, inputs)


def regen_brief(api: Api, variant: str = "reference") -> dict:
    """Finalize the release smoke's regen reference."""
    if variant != "reference":
        raise ValueError("Unknown regen variant")
    capability = _capability(api, _REGEN)
    ref = {
        "id": capability["id"],
        "version": capability["version"],
        "numerical_policy_version": capability["numerical"]["version"],
        "resource_policy_version": capability["resources"]["version"],
    }
    return _review_brief(api, capability, {"capability": ref, "specification": _REGEN_REFERENCE})


def submit(api: Api, brief_id: str, key: str) -> httpx.Response:
    """Attempt admission once and return the response unchanged."""
    return api.start_job({"brief_id": brief_id}, idempotency_key=key)


def classify_429(response: httpx.Response) -> str:
    """Classify the admission or rate-limit information exposed by a 429."""
    if response.status_code != 429:
        return "unknown"
    try:
        body = response.json()
    except ValueError:
        body = response.text
    if isinstance(body, dict):
        body = body.get("detail", body.get("error"))
    if not isinstance(body, str):
        return "unknown"
    message = body.strip().lower()
    # backend/main.py:199 registers SlowAPI's default {"error": ...} handler.
    if message.startswith("rate limit exceeded"):
        return "rate_limit"
    # These details originate in backend/engineering/jobs.py:147 and
    # backend/engineering/admission.py:57,66,89.
    if message.startswith("engineering storage capacity exhausted"):
        return "storage_exhausted"
    if message in {
        "engineering job quota reached for this client",
        "another client is waiting for engineering capacity",
    }:
        return "client_quota"
    if message.startswith("engineering capacity busy"):
        # backend/engineering/api_v1.py:120-121 masks all AdmissionBusy details.
        # Slot and storage refusals both use 5 (admission.py:9). Quota can also
        # use 5. A different positive delay identifies a job fairness refusal.
        try:
            retry_after = int(response.headers.get("Retry-After", ""))
        except ValueError:
            retry_after = 0
        return "client_quota" if retry_after > 0 and retry_after != 5 else "admission_busy"
    return "unknown"


def wait_for(
    api: Api, job_id: str, lifecycle: str, timeout_s: float, poll_s: float = 1.0,
    clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
) -> dict:
    """Poll until the requested lifecycle is observed or the deadline expires."""
    deadline = clock() + timeout_s
    last_seen = "<none>"
    while clock() < deadline:
        response = api.get_job(job_id)
        _expect_status(response, 200, "Read job")
        job = response.json()
        state = job["lifecycle"]
        if state == lifecycle:
            return job
        last_seen = _redact_response(str(state), response)
        remaining = deadline - clock()
        if remaining > 0:
            sleep(min(remaining, poll_s))
    raise TimeoutError(f"Timed out waiting for job lifecycle (last seen: {last_seen})")


def artifact_digests(api: Api, job_id: str, formats: Iterable[str]) -> dict[str, str]:
    """Hash each artifact body and verify its advertised checksum when present."""
    digests = {}
    for fmt in formats:
        response = api.artifact(job_id, fmt)
        _expect_status(response, 200, "Download artifact")
        digest = hashlib.sha256(response.content).hexdigest()
        header = response.headers.get("X-Content-SHA256", digest)
        if header != digest:
            raise AssertionError(_redact_response(f"X-Content-SHA256 mismatch for {fmt}", response))
        digests[fmt] = header
    return digests


_API_KEY = re.compile(r"AIza[0-9A-Za-z_-]{35}")
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASS", re.IGNORECASE)
_MIN_SECRET_LEN = 8  # Shorter env values ("1", "true") would redact ordinary text.
STATUSES = ("passed", "failed", "error", "skipped", "not exercised")


def environment_secrets(environ: Mapping[str, str] | None = None) -> tuple[str, ...]:
    """Values of every env var whose name suggests a credential."""
    source = os.environ if environ is None else environ
    return tuple(sorted({value for name, value in source.items()
                         if _SECRET_NAME.search(name) and len(value) >= _MIN_SECRET_LEN}))


def aggregate_status(items: Iterable[object]) -> str:
    """Case status from real pytest outcomes stored by conftest as item.rep_setup/rep_call/rep_teardown."""
    outcomes = []
    for item in items:
        setup, call, teardown = (getattr(item, f"rep_{name}", None) for name in ("setup", "call", "teardown"))
        if setup is None:
            outcomes.append("not exercised")
        elif setup.outcome == "failed":
            outcomes.append("error")
        elif setup.outcome == "skipped" or (call is not None and call.outcome == "skipped"):
            outcomes.append("skipped")
        elif call is None:
            outcomes.append("not exercised")
        elif call.outcome == "failed":
            outcomes.append("failed")
        elif teardown is not None and teardown.outcome == "failed":
            outcomes.append("error")
        else:
            outcomes.append("passed")
    if not outcomes:
        return "not exercised"
    for status in ("failed", "error"):
        if status in outcomes:
            return status
    if "not exercised" in outcomes:
        return "not exercised"
    return "skipped" if all(status == "skipped" for status in outcomes) else "passed"


class CaseLog:
    """Collect storage observations and atomically write the case log."""

    def __init__(
        self, out_dir: Path, source_sha: str, image_ids: Mapping[str, str],
        redact: Iterable[str] = (), environ: Mapping[str, str] | None = None,
    ) -> None:
        self._out_dir = Path(out_dir)
        self._source_sha = source_sha
        self._image_ids = dict(image_ids)
        self._redact = tuple(value for value in redact if value)
        self._env_secrets = environment_secrets(environ)
        self._cases: list[dict] = []

    def _scrub(self, value: object) -> object:
        """Replace API-key shapes and env credential values in observed strings."""
        if isinstance(value, str):
            for secret in self._env_secrets:
                value = value.replace(secret, "[redacted]")
            return _API_KEY.sub("[redacted]", value)
        if isinstance(value, Mapping):
            return {self._scrub(key): self._scrub(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._scrub(item) for item in value]
        return value

    def record(
        self, case_id: str, mechanism: str, kind: str, observations: object,
        expiry_accelerated: bool = False, status: str = "not exercised",
    ) -> None:
        """Record one real, fault-injected, or accelerated case.

        An "F" case is recorded as "not_injected" unless its observations say fault_injected is true.
        """
        if kind not in ("R", "F", "A"):
            raise ValueError("Unknown storage case kind")
        if status not in STATUSES:
            raise ValueError("Unknown storage case status")
        injected = isinstance(observations, Mapping) and observations.get("fault_injected") is True
        if kind == "F" and not injected:
            kind = "not_injected"
        self._cases.append({
            "case_id": case_id,
            "mechanism": mechanism,
            "kind": kind,
            "status": status,
            "expiry_accelerated": kind == "A" or expiry_accelerated,
            "observations": self._scrub(deepcopy(observations)),
        })

    def set_status(self, case_id: str, status: str) -> None:
        if status not in STATUSES:
            raise ValueError("Unknown storage case status")
        for case in self._cases:
            if case["case_id"] == case_id:
                case["status"] = status

    def write(self) -> Path:
        """Reject configured secrets before replacing the case log."""
        document = {
            "source_sha": self._source_sha,
            "image_ids": self._image_ids,
            "cases": self._cases,
        }
        serialized = json.dumps(
            document, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False,
        ) + "\n"
        if _API_KEY.search(serialized) or any(
            value in serialized or json.dumps(value, ensure_ascii=False)[1:-1] in serialized
            for value in (*self._redact, *self._env_secrets)
        ):
            message = "Storage case log contains a redacted value"
            if any(value in message for value in self._redact):
                message = ""
            raise ValueError(message)

        payload = serialized.encode("utf-8")
        self._out_dir.mkdir(parents=True, exist_ok=True)
        path = self._out_dir / "storage-cases.json"
        pending: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=self._out_dir, prefix=".storage-cases-", suffix=".tmp", delete=False,
            ) as output:
                pending = Path(output.name)
                output.write(payload)
            os.replace(pending, path)
        finally:
            if pending is not None:
                pending.unlink(missing_ok=True)
        return path
