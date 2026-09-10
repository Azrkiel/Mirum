"""Unit tests for backend/verification/report.py — CheckReport + dimension gate.

Pure stdlib + the module under test. Runs in any env (no pytest needed):
    python backend/tests/test_verification_report.py
"""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from verification.report import CheckReport, CheckResult, check_dimensions  # noqa: E402


# ---------------------------------------------------------------------------
# CheckReport aggregation
# ---------------------------------------------------------------------------

def test_empty_report_passes():
    r = CheckReport(part_id="p")
    assert r.passed
    assert not r.hard_failed
    assert not r.degraded


def test_compile_failure_is_hard_fail():
    r = CheckReport(part_id="p", attempt=1)
    r.add(CheckResult("compile", passed=False,
                      failures=["boom"], data={"failure_op": "fillet"}))
    assert not r.passed
    assert r.hard_failed
    assert not r.degraded
    assert r.failure_class() == "fillet"


def test_verification_failure_is_degraded_not_hard():
    r = CheckReport(part_id="p", attempt=2)
    r.add(CheckResult("compile", passed=True))
    r.add(CheckResult("cert", passed=False, failures=["Volume is 0.0 mm³"]))
    assert not r.passed
    assert not r.hard_failed
    assert r.degraded
    assert r.failure_class() == "geometry_cert"


def test_skipped_checks_do_not_fail_report():
    r = CheckReport(part_id="p")
    r.add(CheckResult("compile", passed=True))
    r.add(CheckResult("cert", passed=False, skipped=True))
    r.add(CheckResult("dimensions", passed=True, skipped=True))
    assert r.passed


def test_dimension_failure_class_and_prompt_context():
    r = CheckReport(part_id="p")
    r.add(CheckResult("compile", passed=True))
    r.add(CheckResult("dimensions", passed=False,
                      failures=["total_length_mm: expected 150mm, closest 92.3mm"]))
    assert r.failure_class() == "dimension_mismatch"
    ctx = r.to_prompt_context()
    assert "COMPILED" in ctx
    assert "total_length_mm" in ctx
    assert "[dimensions]" in ctx


def test_telemetry_shape_is_flat_and_jsonable():
    import json
    r = CheckReport(part_id="p", attempt=1)
    r.add(CheckResult("compile", passed=True))
    r.add(CheckResult("cert", passed=False, failures=["bad brep"]))
    t = r.to_telemetry()
    json.dumps(t)  # must not raise
    assert t["passed"] is False
    assert t["degraded"] is True
    assert t["failure_class"] == "geometry_cert"
    assert t["checks"]["compile"] == "pass"
    assert t["checks"]["cert"] == "fail"


def test_summary_line_names_failing_checks():
    r = CheckReport(part_id="p")
    r.add(CheckResult("compile", passed=True))
    r.add(CheckResult("cert", passed=False, failures=["x"]))
    r.add(CheckResult("dimensions", passed=False, failures=["y"]))
    line = r.summary_line()
    assert "cert" in line and "dimensions" in line


def test_solver_failure_class():
    r = CheckReport(part_id="asm")
    r.add(CheckResult("solver", passed=False, failures=["Constraint solver failed"]))
    assert r.failure_class() == "solver"


# ---------------------------------------------------------------------------
# check_dimensions — the dimension-vs-spec gate
# ---------------------------------------------------------------------------

_BBOX = {"x_mm": 100.0, "y_mm": 60.0, "z_mm": 5.0}


def test_dims_none_is_skipped():
    res = check_dimensions(None, _BBOX)
    assert res.skipped and res.passed


def test_bbox_none_is_skipped():
    res = check_dimensions({"total_length_mm": 100.0}, None)
    assert res.skipped and res.passed


def test_envelope_dims_within_tolerance_pass():
    dims = {"total_length_mm": 100.0, "width_mm": 60.0, "plate_height_mm": 5.0}
    res = check_dimensions(dims, _BBOX)
    assert res.passed and not res.skipped
    assert res.failures == []


def test_envelope_dim_out_of_tolerance_fails_with_measured_message():
    res = check_dimensions({"total_length_mm": 150.0}, _BBOX)
    assert not res.passed
    assert len(res.failures) == 1
    msg = res.failures[0]
    assert "total_length_mm" in msg
    assert "150" in msg
    assert "100.0" in msg  # closest extent named


def test_tolerance_boundary():
    # 5% of 100 = 5 → 104.9 passes, 106 fails
    assert check_dimensions({"length_mm": 104.9}, _BBOX).passed
    assert not check_dimensions({"length_mm": 106.0}, _BBOX).passed


def test_radius_is_doubled_before_matching():
    # outer radius 50 → 100mm extent
    res = check_dimensions({"outer_radius_mm": 50.0},
                           {"x_mm": 100.0, "y_mm": 100.0, "z_mm": 10.0})
    assert res.passed


def test_feature_dim_larger_than_part_is_impossible():
    res = check_dimensions({"bore_diameter_mm": 400.0}, _BBOX)
    assert not res.passed
    assert "largest bbox extent" in res.failures[0]


def test_feature_dim_that_fits_passes_without_verification():
    res = check_dimensions({"hole_diameter_mm": 5.0}, _BBOX)
    assert res.passed


def test_wall_thickness_is_not_hard_checked():
    # wall thickness of a shelled box ≠ any bbox extent — must not false-fail
    res = check_dimensions({"wall_thickness_mm": 2.0}, _BBOX)
    assert res.passed


def test_part_scoping_skips_unrelated_dims():
    dims = {"tabletop_length_mm": 999.0}  # would fail if checked
    res = check_dimensions(dims, _BBOX, part_id="leg_1")
    assert res.skipped and res.passed


def test_part_scoping_checks_matching_dims():
    dims = {"leg_height_mm": 5.0}
    res = check_dimensions(dims, _BBOX, part_id="leg_1")
    assert not res.skipped
    assert res.passed


def test_absolute_floor_protects_tiny_dims():
    # 0.5mm dim, extent 0.55mm: rel err 10% > 5%, but abs err 0.05 < 0.1 floor
    res = check_dimensions({"total_length_mm": 0.5},
                           {"x_mm": 0.55, "y_mm": 10.0, "z_mm": 10.0})
    assert res.passed


def _main() -> int:
    import traceback
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception:
            failed += 1
            print(f"FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(_main())
