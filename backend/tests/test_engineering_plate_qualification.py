"""Q1 numerical qualification of the rectangular plate across the signed envelope."""

import json
from functools import lru_cache
from itertools import product

import pytest

from engineering.plate_fea import solve_levels
from engineering.plate_spec import EDGE_CONDITIONS, PLATE_NUMERICAL_V1 as POLICY
from engineering.plate_verify import _default_reference, _finite, _relative_change


B, H, Q, E = 1000.0, 10.0, 0.01, 200000.0
RATIOS = (
    POLICY["aspect_ratio_min"], 1.05, 1.1, 1.2, 1.3, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0,
    POLICY["aspect_ratio_max"],
)
NUS = (POLICY["poissons_ratio_min"], POLICY["r3_nu"], POLICY["poissons_ratio_max"])
POINTS = tuple(product(RATIOS, EDGE_CONDITIONS, NUS))
CHECKS = (
    "mesh_convergence_deflection", "mesh_convergence_stress", "equilibrium",
    "benchmark_deflection", "benchmark_stress",
)


@lru_cache(maxsize=None)
def _measure(ratio, edge, nu):
    a = ratio * B
    levels = solve_levels(length_a_mm=a, width_b_mm=B, thickness_mm=H, pressure_mpa=Q,
                          youngs_modulus_mpa=E, poissons_ratio=nu, edge_condition=edge)
    mesh = tuple(level["n_b"] for level in levels)
    if mesh != POLICY["mesh_levels_n_b"]:
        raise ValueError(f"Unexpected FE mesh levels {mesh}")
    for level in levels:
        for key in ("w_max", "vm_max_mpa", "equilibrium_relative_error"):
            _finite(key, level[key])
    previous, finest = levels[-2:]
    w, vm = finest["w_max"], finest["vm_max_mpa"]
    ref = _default_reference(a, B, H, Q, E, nu, edge)
    return {
        "mesh_convergence_deflection": _relative_change(w, previous["w_max"]),
        "mesh_convergence_stress": _relative_change(vm, previous["vm_max_mpa"]),
        "equilibrium": max(level["equilibrium_relative_error"] for level in levels),
        "benchmark_deflection": abs(w / _finite("reference w_max", ref["w_max"]) - 1),
        "benchmark_stress": abs(vm / _finite("reference vm_max", ref["vm_max"]) - 1),
    }


def _summary(check, points, measured):
    limit = POLICY[check]
    available = [point for point in points if point in measured]
    worst_point = max(available, key=lambda p: measured[p][check], default=None)
    worst = measured[worst_point][check] if worst_point is not None else None
    return {
        "limit": limit, "worst": worst, "worst_margin": 1 - worst / limit if worst is not None else None,
        "worst_point": worst_point, "passed": sum(measured[p][check] <= limit for p in available),
    }


def _row(label, stats, total):
    worst = stats["worst"]
    margin = stats["worst_margin"]
    values = f"{worst:.8g} {margin:.3%}" if worst is not None else "n/a n/a"
    return f"{label} {stats['limit']:g} {values} {stats['worst_point']} {stats['passed']}/{total}"


@pytest.mark.parametrize("points", [pytest.param(POINTS, id="Q1")])
def test_plate_qualification(points, capsys):
    assert len(set(points)) == len(points) == 72
    measured = {}
    failures = []
    for point in points:
        try:
            values = _measure(*point)
            for check, value in values.items():
                _finite(check, value)
        except Exception as exc:
            # A missing solve must fail qualification and leave the other points observable.
            failures.append(f"{point}: {type(exc).__name__}: {exc}")
            continue
        measured[point] = values
        for check, value in values.items():
            if value > POLICY[check]:
                failures.append(f"{point}: {check}={value!r} exceeds {POLICY[check]!r}")

    checks = {check: _summary(check, points, measured) for check in CHECKS}
    report = {"points": len(points), "checks": checks, "all_passed": not failures}
    with capsys.disabled():
        print("\nE03-Q1-TABLE-BEGIN")
        print("check limit worst worst_margin (a/b, edge, nu) passed/points")
        for check, stats in checks.items():
            print(_row(check, stats, len(points)))
        for edge in EDGE_CONDITIONS:
            subset = [point for point in points if point[1] == edge]
            check = "mesh_convergence_stress"
            print(_row(f"{check}[{edge}]", _summary(check, subset, measured), len(subset)))
        print("E03-Q1-JSON " + json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
        print("E03-Q1-TABLE-END")

    assert not failures, "\n".join(["Q1 qualification failures (a/b, edge, nu)", *failures])


# Q2-Q4 exercise the same reviewed brief, adapter and search used by a plate job.
import asyncio
import hashlib
import os
import re
import time
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock

from pydantic import ValidationError

from backend.tests.test_engineering_generator_contracts import brief_for, content
from backend.tests.test_engineering_plate_adapter import brief_spec, fake_stage
from engineering.capabilities import PLATE
from engineering.plate_adapter import PlateAdapter
from engineering.plate_spec import GUARD_IDS, PlateBrief
from engineering.search import search
from engineering.service import EngineeringUnavailable


def _reviewed_plate(thicknesses=(H,), **requirements):
    raw = brief_spec(thicknesses, **requirements)
    plate = PlateBrief.model_validate_json(json.dumps(raw))
    return brief_for(content(PLATE.id, plate.model_dump(mode="json")))


def _require_native_plate():
    try:
        import cadquery, OCP  # noqa: F401
    except ImportError:
        pytest.skip('cadquery/OCP unavailable')


def _adversarial_step(spec, path, feature):
    import cadquery as cq
    from engineering.plate_geometry import build_plate

    shape = build_plate(spec)
    a, b, h = spec.length_a_mm, spec.width_b_mm, spec.thickness_mm
    if feature == "hole":
        bore = cq.Workplane("XY").workplane(offset=-1).center(a / 2, b / 2).circle(5).extrude(h + 2)
        shape = shape.cut(bore)
    elif feature == "fillet":
        shape = shape.edges("|Z").fillet(2)
    elif feature == "second_solid":
        other = shape.translate((2 * a, 0, 0))
        shape = cq.Compound.makeCompound([shape.val(), other.val()])
    else:
        raise ValueError(feature)
    cq.exporters.export(shape, str(path))


def _verifier_hooks(gate):
    if gate.startswith("mesh_convergence_"):
        quantity = "w_max" if gate.endswith("deflection") else "vm_max_mpa"

        def unconverged(**kwargs):
            levels = solve_levels(**kwargs)
            # Change only the previous level. The final solution and its reference stay valid.
            levels[-2] = {**levels[-2], quantity: levels[-1][quantity] * (1 + 2 * POLICY[gate])}
            return levels

        return {"fe_solver": unconverged}
    if gate.startswith("benchmark_"):
        quantity = "w_max" if gate.endswith("deflection") else "vm_max"

        def mismatch(*args):
            values = _default_reference(*args)
            return {**values, quantity: values[quantity] / (1 + 4 * POLICY[gate])}

        return {"reference": mismatch}
    return {}


def _plate_evidence(spec, path, *, gate="", feature=None):
    from engineering.plate_geometry import export_step
    from engineering.plate_verify import verify_plate_step

    if feature:
        _adversarial_step(spec, path, feature)
    else:
        export_step(spec, path)
    return verify_plate_step(path, spec, **_verifier_hooks(gate)), path.read_bytes()


@pytest.mark.parametrize("gate,thicknesses,requirements,feature", [
    pytest.param("thin_ratio", (25.0,), {}, None, id="thin-ratio"),
    pytest.param("small_deflection", (2.0,), {"pressure_mpa": 0.1}, None, id="small-deflection"),
    pytest.param("stress_limit", (8.0, 10.0, 12.0), {"allowable_stress_mpa": 0.1}, None,
                 id="three-over-stress-limit"),
    *[pytest.param(gate, (H,), {}, None, id=gate) for gate in (
        "mesh_convergence_deflection", "mesh_convergence_stress", "benchmark_deflection", "benchmark_stress",
    )],
    *[pytest.param("geometry", (H,), {}, feature, id=feature) for feature in ("hole", "fillet", "second_solid")],
])
def test_q2_rejected_plate(tmp_path, gate, thicknesses, requirements, feature):
    _require_native_plate()

    async def stage(spec):
        path = tmp_path / f"plate-{spec.thickness_mm}.step"
        return _plate_evidence(spec, path, gate=gate, feature=feature)

    brief = _reviewed_plate(thicknesses, **requirements)
    result = asyncio.run(search(brief, adapter=PlateAdapter(stage=stage)))
    assert result.state == "rejected", result.reasons
    (evaluation,) = result.evaluations
    attempts = evaluation["report"]["attempts"]
    assert [attempt["thickness_mm"] for attempt in attempts] == list(thicknesses)
    for attempt in attempts:
        assert attempt["passed"] is False
        assert attempt["verify"]["rejection"]["id"] == gate
    assert result.attempts[0].failures[0].startswith(f"{gate}:")
    # The job-level reason is search's fixed text; the named guard/check is in the attempt record.
    assert result.reasons == ('No candidate in the reviewed set passed',), result.reasons


@pytest.mark.parametrize("missing", ["stage_unavailable", "spec_digest_mismatch"])
def test_q2_missing_evidence_is_unavailable(missing, tmp_path):
    brief = _reviewed_plate()
    spy = AsyncMock(side_effect=EngineeringUnavailable("Missing plate evidence"))
    if missing == "spec_digest_mismatch":
        _require_native_plate()
        (spec,) = PLATE.parse_spec(brief.content.specification).candidates()
        # Build evidence outside search so an unrelated native failure cannot satisfy this test.
        report, step = _plate_evidence(spec, tmp_path / "plate.step")
        assert report["passed"] is True and report["spec_sha256"] != "0" * 64
        spy = AsyncMock(return_value=({**report, "spec_sha256": "0" * 64}, step))
    result = asyncio.run(search(brief, adapter=PlateAdapter(stage=spy)))
    spy.assert_awaited_once()
    assert result.state == "unavailable", result.reasons
    assert result.selected is None and not result.attempts


@pytest.mark.parametrize("requirements", [
    pytest.param({"length_a_mm": 399.0}, id="a-less-than-b"),
    pytest.param({"length_a_mm": 400.0 * POLICY["aspect_ratio_max"] + 1}, id="aspect-ratio-too-large"),
    pytest.param({"pressure_mpa": float("nan")}, id="nonfinite-pressure"),
    pytest.param({"edge_condition": "free"}, id="free-edge"),
    pytest.param({"poissons_ratio": 0.05}, id="nu-too-low"),
    pytest.param({"poissons_ratio": 0.5}, id="nu-too-high"),
    pytest.param({"point_load_n": 1.0}, id="unknown-field"),
])
def test_q2_invalid_brief_never_reaches_native_stage(requirements):
    spy = AsyncMock()
    adapter = PlateAdapter(stage=spy)
    with pytest.raises(ValidationError):
        asyncio.run(search(_reviewed_plate(**requirements), adapter=adapter))
    spy.assert_not_called()


def test_q3_native_stage_wall_times(tmp_path, monkeypatch, capsys):
    _require_native_plate()
    if os.name != 'posix':
        pytest.skip('Linux process groups and rlimits')
    monkeypatch.setenv("MIRUM_WORK_DIR", str(tmp_path))
    # compiler may already be imported by another test before MIRUM_WORK_DIR is set.
    monkeypatch.setattr("compiler._WORK_DIR", tmp_path)
    adapter = PlateAdapter()
    timeout = next(limit.value for limit in PLATE.runtime_limits if limit.name == "ProcessRunner.timeout_s")
    timings = {}
    cases = (
        ("clamped_5_to_1", "clamped", 500.0 * POLICY["aspect_ratio_max"], 500.0, H),
        # At 10 mm the SS square exceeds the small-deflection guard. Time a full accepted solve.
        ("ss_square", "simply_supported", B, B, 12.0),
    )
    for case, edge, a, b, h in cases:
        raw = brief_spec((h,), length_a_mm=a, width_b_mm=b, edge_condition=edge, pressure_mpa=Q,
                         youngs_modulus_mpa=E, poissons_ratio=POLICY["r3_nu"],
                         allowable_stress_mpa=POLICY["allowable_stress_mpa_le"],
                         max_deflection_mm=b * POLICY["max_deflection_width_ratio"])
        (spec,) = PlateBrief.model_validate_json(json.dumps(raw)).candidates()
        started = time.perf_counter()
        report, step = asyncio.run(adapter._native_stage(spec))
        timings[case] = time.perf_counter() - started
        with capsys.disabled():
            print(f"\nE03-Q3 {case} {timings[case]:.6f}")
        assert report["passed"] is True and step, report["rejection"]
    with capsys.disabled():
        print("E03-Q3-JSON " + json.dumps(timings, sort_keys=True, allow_nan=False))
    assert all(seconds < timeout for seconds in timings.values()), timings


@pytest.mark.skipif(os.name != 'posix', reason='the engineering store and publisher need POSIX')
@pytest.mark.parametrize("edge,method", [("simply_supported", "navier_ss_r1"), ("clamped", "fd_plate_r2_extrapolated")])
def test_q4_accepted_plate_package(tmp_path, fake_stage, edge, method):
    from engineering import plate_spec
    from engineering.bundles import BundlePublisher
    from engineering.store import SQLiteStore

    brief = _reviewed_plate(edge_condition=edge)
    result = asyncio.run(search(brief, adapter=PlateAdapter(stage=fake_stage["stage"])))
    assert result.state == "selected", result.reasons
    job_id = "a" * 32
    store = SQLiteStore(tmp_path / "store")
    try:
        publisher = BundlePublisher(store)
        bundle = asyncio.run(publisher.publish(job_id, brief, result))
        publisher.validate(job_id, bundle)
        with publisher.lease(job_id, "zip", envelope=bundle) as download, zipfile.ZipFile(download.file) as archive:
            payloads = {name: archive.read(name) for name in archive.namelist()}
    finally:
        store.close()

    # Every member of this plate package (including STEP) is text.
    filesystem_path = re.compile(
        r"[A-Za-z]:[\\/]|\\\\|/tmp/|(?<![\w/])/(?:[\w.-]+)"
        r"|(?:^|[^\w.])(?:~|\.{1,2})[/\\]|[\w.-]+[/\\][\w.-]+(?:[/\\][\w.-]+|\.[A-Za-z0-9]+)"
    )
    for name, data in payloads.items():
        for text in (name, data.decode("utf-8")):
            assert str(tmp_path) not in text and "engineering-plate-" not in text, name
            assert not filesystem_path.search(text), name

    primary = PLATE.primary_artifact
    assert primary.endswith(".step")
    step = payloads[primary]
    assert step.startswith(b"ISO-10303-21;") and step == result.selected["artifacts"][primary]
    report = json.loads(payloads["generator-report.json"])
    assert report["step_sha256"] == hashlib.sha256(step).hexdigest()
    assert report["passed"] is True and report["method"] == "plate_verify.verify_plate_step"
    (attempt,) = report["attempts"]
    verify = attempt["verify"]
    levels = verify["mesh_history"]
    assert tuple(level["n_b"] for level in levels) == POLICY["mesh_levels_n_b"]
    assert all(level["w_max"] > 0 and level["vm_max_mpa"] > 0 for level in levels)
    refs = verify["references"]
    assert refs["method"] == method and refs["w_max"] > 0 and refs["vm_max"] > 0
    for guard in GUARD_IDS:
        row = verify["guards"][guard]
        assert row["state"] == "passed" and row["quantity"] is not None and row["limit"] is not None, guard
    if edge == "simply_supported":
        forces = report["corner_forces"]
        assert forces == verify["corner_forces"]
        assert forces["per_level_n"] == [level["corner_force_n"] for level in levels]
        assert forces["finest_n"] == forces["per_level_n"][-1] > 0
        assert forces["twist_force_n"] > 0
    else:
        assert report.get("corner_forces") is None and verify.get("corner_forces") is None
    hashes = report["implementation_sha256"]
    assert hashes == verify["implementation_sha256"]
    for source in ("plate_spec.py", "plate_geometry.py", "plate_verify.py", "plate_fea.py", "plate_reference.py"):
        assert hashes[source] == hashlib.sha256(Path(plate_spec.__file__).with_name(source).read_bytes()).hexdigest()
