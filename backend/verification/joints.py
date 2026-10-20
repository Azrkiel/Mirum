"""Fail-closed joint verification for a delivered mate-joint assembly.

Binds the evidence to the exact manifest and the exact accepted STEP bytes.
Native work runs in an owned bounded subprocess (``joint_worker.py``).
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile

import asyncio

# One global cap across requests: each worker may use 1.5 GB and up to 30 s.
_WORKER_SLOTS = asyncio.Semaphore(2)


def joint_spec(manifest) -> dict:
    """Declared joints in the generated-assembly namespace (trusted input)."""
    if __package__ and __package__.startswith("backend."):
        from ..schemas import sanitize_part_id
    else:
        from schemas import sanitize_part_id
    joints = []
    for rule in manifest.mating_rules:
        if rule.mate_type is None or rule.source_part_id == rule.target_part_id:
            continue
        joints.append({
            "source": sanitize_part_id(rule.source_part_id),
            "source_anchor": rule.source_anchor,
            "target": sanitize_part_id(rule.target_part_id),
            "target_anchor": rule.target_anchor,
            "joint": getattr(rule.mate_type, "value", rule.mate_type),
            "clearance_mm": float(rule.clearance),
        })
    return {"parts": [sanitize_part_id(p.part_id) for p in manifest.parts], "joints": joints}


def manifest_digest(manifest) -> str:
    canonical = json.dumps(manifest.model_dump(mode="json"), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def verify_assembly_joints(step_bytes: bytes, manifest) -> dict:
    """Re-measure every declared joint on the delivered STEP; never raises.

    Returns a report whose ``passed`` is True only when every joint relation
    holds on the reimported transformed bodies, the measured freedoms match
    the declared contract, and no delivered bodies interpenetrate.
    """
    if __package__ and __package__.startswith("backend."):
        from ..compiler import _WORK_DIR
        from ..process_runner import ProcessRunner
    else:
        from compiler import _WORK_DIR
        from process_runner import ProcessRunner
    spec = joint_spec(manifest)
    bindings = {"manifest_sha256": manifest_digest(manifest),
                "step_sha256": hashlib.sha256(step_bytes).hexdigest()}
    if not spec["joints"]:
        return {"passed": True, "joints": [], "bindings": bindings, "engineering_certified": False,
                "method": "no mate joints declared"}
    _WORK_DIR.mkdir(parents=True, exist_ok=True)
    report: dict
    with tempfile.TemporaryDirectory(prefix="joints-", dir=_WORK_DIR) as temporary:
        stage = Path(temporary)
        step_path, spec_path, report_path = stage / "assembly.step", stage / "spec.json", stage / "report.json"
        step_path.write_bytes(step_bytes)
        spec_path.write_text(json.dumps(spec), encoding="utf-8")
        env = {name: os.environ[name] for name in ("PATH", "HOME", "LANG", "SYSTEMROOT",
               "WINDIR", "USERPROFILE", "TEMP", "TMP") if name in os.environ}
        env.update(OPENBLAS_NUM_THREADS="1", OMP_NUM_THREADS="1", PYTHONDONTWRITEBYTECODE="1")
        runner = ProcessRunner(timeout=30, output_limit=1048576, memory_mb=1536)
        async with _WORKER_SLOTS:
            try:
                native = await runner.run([sys.executable, str(Path(__file__).with_name("joint_worker.py")),
                                           str(step_path), str(spec_path), str(report_path)],
                                          cwd=str(stage), env=env)
            finally:
                await runner.close()
        try:
            report = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            report = {"passed": False, "failures": ["joint verification evidence is unavailable"]}
        if native.get("code") or native.get("returncode") != 0:
            report = {"passed": False, "failures": ["joint verification worker failed or exceeded its budget"]}
        # Native error text may echo the private stage path; never return it.
        if isinstance(report, dict) and isinstance(report.get("failures"), list):
            report["failures"] = [str(f).replace(str(stage), "<work>").replace(str(_WORK_DIR), "<work>")
                                  for f in report["failures"]]
    if not isinstance(report, dict):
        report = {"passed": False, "failures": ["joint verification evidence is malformed"]}
    report["passed"] = report.get("passed") is True
    report["bindings"] = bindings
    report["engineering_certified"] = False
    return report
