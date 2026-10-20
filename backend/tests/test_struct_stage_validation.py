"""Defect E: _run_struct_stage / StructSubagent map invalid input to 'skipped'."""
import asyncio
import math
import os
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-dummy-key-not-used")
_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_BACKEND.parent))

import pytest  # noqa: E402


def _req(**box_over):
    box = dict(chord_root_m=1.7881, thickness_to_chord=0.12, cap_area_m2=0.000497,
               spar_web_thickness_m=0.0008, n_spars=2)
    box.update(box_over)
    return SimpleNamespace(
        inferred_domain="D", material_class="AL2024-T3",
        aircraft_spec=SimpleNamespace(mtow_kg=1200.0, cruise_alt_m=3000.0,
                                      cruise_speed_ms=60.0, category="normal",
                                      cl_max=1.5, lift_curve_slope=None,
                                      gust_velocity_ms=None, vne_factor=None),
        wing_spec=SimpleNamespace(area_m2=14.39, span_m=10.7284, mean_chord_m=None),
        airfoil_spec=None, wing_box_spec=SimpleNamespace(**box),
    )


def _stage(req):
    from main import _run_struct_stage
    return asyncio.run(_run_struct_stage(req))


def _inline(req):
    from subagents.struct import StructSubagent
    return asyncio.run(StructSubagent._run_struct_inline("D", req))


@pytest.mark.parametrize("runner", [_stage, _inline])
@pytest.mark.parametrize("bad", [-1.0, math.nan, math.inf, 1e-320])
def test_invalid_box_is_skipped(runner, bad):
    report, msg = runner(_req(cap_area_m2=bad))
    assert report is None
    assert msg.startswith("invalid structural input")


@pytest.mark.parametrize("runner", [_stage, _inline])
def test_invalid_n_spars_is_skipped(runner):
    report, msg = runner(_req(n_spars=2.5))
    assert report is None and msg.startswith("invalid structural input")


@pytest.mark.parametrize("runner", [_stage, _inline])
def test_valid_report_has_new_fields(runner):
    report, _ = runner(_req())
    assert report["bending_status"] == "evaluated"
    assert report["shear_status"] == "evaluated"
    assert report["governing_load_case"] == "positive_ultimate"
    assert report["method"] == "analytic_sizing_estimate"
    assert report["engineering_certified"] is False
    assert any("buckling" in w for w in report["warnings"])


def test_zero_load_serialises_null_with_status(monkeypatch):
    import analysis.structures as s
    real = s.analyze_wing_structure

    def zero(spec, wing, box, **kw):
        return real(spec, wing, box, root_bending_moment_nm=0.0,
                    root_shear_force_n=0.0, **kw)

    monkeypatch.setattr(s, "analyze_wing_structure", zero)
    for runner in (_stage, _inline):
        report, _ = runner(_req())
        assert report["bending_margin_of_safety"] is None
        assert report["bending_status"] == "zero_load"
        assert report["shear_status"] == "zero_load"
        assert report["passed"] is True
