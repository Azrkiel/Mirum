"""
backend/verification/report.py — unified verification report for the critic loop.

One structure that carries every per-attempt verification signal (compile
result, STEP export, geometry certificate, dimension-vs-spec gate, Domain C
continuity/curvature gates, assembly solver status) so the retry loop can:

  1. decide whether the attempt passed,
  2. feed measured failures back into the Machinist retry prompt, and
  3. log a flat summary into telemetry.

The report deliberately has no OCC/LLM dependencies — callers run the checks
and record the outcomes here.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Failure classes contributed by verification checks. These join the
# operation classes from failure_analysis.classify_failure as keys into
# OP_TO_RULE_HINT and retrieval._FAILURE_TO_TECHNIQUE.
_CHECK_FAILURE_CLASS: dict[str, str] = {
    "compile": "unknown",           # overridden by data["failure_op"]
    "export": "export",
    "cert": "geometry_cert",
    "dimensions": "dimension_mismatch",
    "g2": "continuity",
    "curvature": "gaussian_curvature",
    "solver": "solver",
}


@dataclass
class CheckResult:
    """Outcome of a single verification check on one attempt."""

    name: str                       # "compile" | "export" | "cert" | "dimensions" | "g2" | "curvature" | "solver"
    passed: bool
    skipped: bool = False           # check could not run (no OCC, no spec, ...)
    failures: list[str] = field(default_factory=list)
    data: dict = field(default_factory=dict)


@dataclass
class CheckReport:
    """All verification signal for one critic-loop attempt (or the assembly)."""

    part_id: str
    attempt: int = 0
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    def get(self, name: str) -> CheckResult | None:
        for c in self.checks:
            if c.name == name:
                return c
        return None

    @property
    def passed(self) -> bool:
        """True when every non-skipped check passed."""
        return all(c.passed for c in self.checks if not c.skipped)

    @property
    def hard_failed(self) -> bool:
        """True when the script did not even compile — nothing deliverable."""
        compile_check = self.get("compile")
        return compile_check is not None and not compile_check.passed

    @property
    def degraded(self) -> bool:
        """Compiled, but at least one verification check failed."""
        return not self.hard_failed and not self.passed

    @property
    def geometry_cert(self) -> dict | None:
        cert_check = self.get("cert")
        if cert_check is None:
            return None
        return cert_check.data.get("cert")

    def failure_class(self) -> str:
        """Failure class of the first failing check, for hint/retrieval lookup."""
        for c in self.checks:
            if not c.passed and not c.skipped:
                if c.name == "compile":
                    return c.data.get("failure_op") or "unknown"
                return _CHECK_FAILURE_CLASS.get(c.name, "unknown")
        return "unknown"

    def failure_messages(self) -> list[str]:
        msgs: list[str] = []
        for c in self.checks:
            if not c.passed and not c.skipped:
                for f in c.failures:
                    msgs.append(f"[{c.name}] {f}")
        return msgs

    def to_prompt_context(self) -> str:
        """Verification failures in measured terms, for the retry prompt."""
        msgs = self.failure_messages()
        if not msgs:
            return ""
        return (
            "The script COMPILED but the produced geometry failed verification:\n"
            + "\n".join(f"- {m}" for m in msgs)
        )

    def summary_line(self) -> str:
        """One-line digest for the cross-attempt history."""
        if self.passed:
            return "all checks passed"
        failing = [
            (c.data.get("failure_op") or c.name) if c.name == "compile" else c.name
            for c in self.checks
            if not c.passed and not c.skipped
        ]
        return "failed " + ", ".join(failing) if failing else "no checks ran"

    def to_telemetry(self) -> dict:
        """Flat, JSONL-safe summary for telemetry.log_attempt."""
        return {
            "passed": self.passed,
            "degraded": self.degraded,
            "failure_class": None if self.passed else self.failure_class(),
            "checks": {
                c.name: ("skipped" if c.skipped else ("pass" if c.passed else "fail"))
                for c in self.checks
            },
            "failures": self.failure_messages()[:10],
        }

    def to_payload(self) -> dict:
        """Full JSON-serialisable form for SSE / API payloads."""
        return {
            "part_id": self.part_id,
            "attempt": self.attempt,
            "passed": self.passed,
            "degraded": self.degraded,
            "checks": [
                {
                    "name": c.name,
                    "passed": c.passed,
                    "skipped": c.skipped,
                    "failures": c.failures,
                }
                for c in self.checks
            ],
        }


# ---------------------------------------------------------------------------
# Dimension-vs-spec gate
# ---------------------------------------------------------------------------

# Names that describe the part's overall envelope — the only dimensions a
# bounding box can positively attest. Feature dimensions (hole diameters,
# fillet radii, wall thicknesses) are checked for impossibility only.
_ENVELOPE_TOKENS = (
    "length", "width", "height", "depth", "span", "total", "overall",
    "chord", "outer_diameter", "od",
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _name_tokens(name: str) -> set[str]:
    return set(_TOKEN_RE.findall(name.lower()))


def _is_envelope_dim(name: str) -> bool:
    tokens = _name_tokens(name)
    return any(tok in tokens for tok in _ENVELOPE_TOKENS)


def check_dimensions(
    key_dimensions: dict[str, float] | None,
    bounding_box: dict[str, float] | None,
    part_id: str | None = None,
    rel_tol: float = 0.05,
    abs_floor_mm: float = 0.1,
) -> CheckResult:
    """Compare REA-extracted key_dimensions against the measured bounding box.

    Deliberately conservative — only asserts what a bounding box can attest:

    - ENVELOPE dimensions (name contains length/width/height/depth/span/
      total/overall/chord/outer_diameter/od) must match SOME bbox extent
      within tolerance, else FAIL with a measured-vs-expected message.
    - Every dimension must fit inside the part: an expected size larger than
      the largest extent (plus tolerance) is impossible and FAILS.
    - Feature dimensions (hole diameters, fillet radii, wall thicknesses)
      are otherwise unverifiable from a bbox and PASS with a note.

    When ``part_id`` is given (multi-part assemblies), only dimensions whose
    name mentions a part_id token are checked — never guess which part a
    global dimension belongs to.

    Tolerance per dimension: max(value * rel_tol, abs_floor_mm).
    """
    if not key_dimensions:
        return CheckResult(
            "dimensions", passed=True, skipped=True,
            data={"reason": "no key_dimensions extracted"},
        )
    if not bounding_box:
        return CheckResult(
            "dimensions", passed=True, skipped=True,
            data={"reason": "no bounding box measured"},
        )

    extents = [
        float(bounding_box.get("x_mm") or 0.0),
        float(bounding_box.get("y_mm") or 0.0),
        float(bounding_box.get("z_mm") or 0.0),
    ]
    max_extent = max(extents)

    part_tokens: set[str] = set()
    if part_id:
        part_tokens = {t for t in _name_tokens(part_id) if len(t) > 2}

    failures: list[str] = []
    detail: list[dict] = []
    checked_any = False

    for name, raw_value in key_dimensions.items():
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            continue
        if value <= 0:
            continue

        # Multi-part: only check dimensions that name this part.
        if part_tokens and not (part_tokens & _name_tokens(name)):
            continue

        tokens = _name_tokens(name)
        # A radius describes half an extent.
        expected_size = value * 2.0 if "radius" in tokens else value
        tol = max(expected_size * rel_tol, abs_floor_mm)
        checked_any = True

        entry: dict = {"name": name, "expected_mm": expected_size}

        # Impossibility: a dimension can never exceed the whole part.
        if expected_size > max_extent + tol:
            rel_err = abs(max_extent - expected_size) / expected_size
            failures.append(
                f"{name}: expected {expected_size:g}mm but the largest bbox "
                f"extent is {max_extent:.1f}mm ({rel_err:.0%} off, tol {rel_tol:.0%})"
            )
            entry.update({"measured_mm": max_extent, "passed": False})
            detail.append(entry)
            continue

        if _is_envelope_dim(name):
            closest = min(extents, key=lambda e: abs(e - expected_size))
            if abs(closest - expected_size) > tol:
                rel_err = abs(closest - expected_size) / expected_size
                failures.append(
                    f"{name}: expected {expected_size:g}mm, closest bbox extent "
                    f"{closest:.1f}mm ({rel_err:.0%} off, tol {rel_tol:.0%})"
                )
                entry.update({"measured_mm": closest, "passed": False})
            else:
                entry.update({"measured_mm": closest, "passed": True})
        else:
            entry.update({"passed": True, "note": "feature dim — bbox fit check only"})
        detail.append(entry)

    if not checked_any:
        return CheckResult(
            "dimensions", passed=True, skipped=True,
            data={"reason": "no dimensions applicable to this part"},
        )

    return CheckResult(
        "dimensions",
        passed=not failures,
        failures=failures,
        data={"dimensions": detail},
    )
