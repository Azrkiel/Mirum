"""Build the R4 run's evidence.json artifact."""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from tempfile import NamedTemporaryFile

_REPO_ROOT = Path(__file__).resolve().parents[2]
_OUTCOMES = frozenset({"passed", "failed", "skipped", "error"})


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Evidence:
    """Collect run metadata and write it as an atomic JSON artifact."""

    def __init__(
        self,
        out_dir: Path,
        *,
        clock: Callable[[], datetime] = _utc_now,
        redact: Sequence[str] = (),
    ) -> None:
        self._out_dir = out_dir
        self._clock = clock
        self._redact = tuple(value for value in redact if value)
        self._source: dict[str, str | None] = {}
        self._image_ids: dict[str, str] = {}
        self._base_digests: dict[str, str] = {}
        self._lock_hashes: dict[str, str] = {}
        self._pip_freeze: dict[str, str] = {}
        self._tests: list[tuple[str, str, float]] = []
        self._screenshots: list[tuple[str, str, str, int]] = []
        self._notes: dict[str, str] = {}

    def set_source(self, sha: str, ref: str | None = None) -> None:
        self._source = {"sha": sha, "ref": ref}

    def set_images(
        self, image_ids: Mapping[str, str], base_digests: Mapping[str, str] | None = None
    ) -> None:
        self._image_ids = dict(image_ids)
        self._base_digests = dict(base_digests) if base_digests is not None else {}

    def add_lock_hash(self, path: Path) -> None:
        """Hash a lockfile and key it by its path from the checkout root."""
        lock_path = (_REPO_ROOT / path).resolve()
        relative_path = lock_path.relative_to(_REPO_ROOT).as_posix()
        self._lock_hashes[relative_path] = hashlib.sha256(lock_path.read_bytes()).hexdigest()

    def set_pip_freeze(self, service: str, text: str) -> None:
        self._pip_freeze[service] = text

    def record_test(self, nodeid: str, outcome: str, duration_s: float) -> None:
        if outcome not in _OUTCOMES:
            raise ValueError("Unknown test outcome")
        self._tests.append((nodeid, outcome, duration_s))

    def add_screenshot(self, path: Path, *, flow: str) -> None:
        """Record a screenshot contained in the artifact directory."""
        screenshot = path.resolve()
        relative_path = screenshot.relative_to(self._out_dir.resolve()).as_posix()
        contents = screenshot.read_bytes()
        self._screenshots.append(
            (relative_path, flow, hashlib.sha256(contents).hexdigest(), len(contents))
        )

    def add_note(self, key: str, value: str) -> None:
        self._notes[key] = value

    def to_dict(self) -> dict:
        """Return a snapshot with UTC time and ordered records."""
        generated_at = self._clock()
        if generated_at.utcoffset() is None:
            generated_at = generated_at.replace(tzinfo=timezone.utc)
        return {
            "schema_version": "mirum_e2e_evidence_v1",
            "generated_at": generated_at.astimezone(timezone.utc).isoformat(),
            "source": dict(self._source),
            "image_ids": dict(self._image_ids),
            "base_digests": dict(self._base_digests),
            "lock_hashes": dict(self._lock_hashes),
            "pip_freeze": dict(self._pip_freeze),
            "tests": [
                {"nodeid": nodeid, "outcome": outcome, "duration_s": duration_s}
                for nodeid, outcome, duration_s in sorted(self._tests, key=lambda test: test[0])
            ],
            "screenshots": [
                {"path": path, "flow": flow, "sha256": sha256, "bytes": size}
                for path, flow, sha256, size in sorted(
                    self._screenshots, key=lambda screenshot: screenshot[0]
                )
            ],
            "notes": dict(self._notes),
        }

    def write(self) -> Path:
        """Reject redacted values and replace evidence.json with complete JSON."""
        serialized = json.dumps(
            self.to_dict(), ensure_ascii=False, indent=2, sort_keys=True, allow_nan=False
        ) + "\n"
        if any(
            value in serialized or json.dumps(value, ensure_ascii=False)[1:-1] in serialized
            for value in self._redact
        ):
            message = "Evidence contains a redacted value"
            if any(value in message for value in self._redact):
                message = ""
            raise ValueError(message)

        payload = serialized.encode("utf-8")
        self._out_dir.mkdir(parents=True, exist_ok=True)
        path = self._out_dir / "evidence.json"
        temp_path: Path | None = None
        try:
            with NamedTemporaryFile(
                dir=self._out_dir, prefix=".evidence-", suffix=".tmp", delete=False
            ) as temp_file:
                temp_path = Path(temp_file.name)
                temp_file.write(payload)
            os.replace(temp_path, path)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)
        return path
