"""Missing physics must never be promoted to a safe wing-analysis result."""
from analysis.structures import analyze_wing_structure
from analysis.tests.test_structures import REF_SPEC, REF_WING, REF_BOX


def strong_box():
    return dict(REF_BOX, cap_area_m2=0.01, spar_web_thickness_m=0.01)


def test_missing_span_and_no_explicit_moment_cannot_pass_bending():
    report = analyze_wing_structure(REF_SPEC, {'area_m2': 14.39}, strong_box())
    assert report['passed'] is False, 'Unknown root moment was treated as a zero-stress pass'
    assert report['bending']['passed'] is False


def test_impossible_cruise_condition_cannot_pass_overall_analysis():
    report = analyze_wing_structure(dict(REF_SPEC, cruise_speed_ms=10.0), REF_WING, strong_box())
    assert report['cl_cruise'] > REF_SPEC['cl_max']
    assert report['passed'] is False, 'CL above CLmax is not an acceptable operating condition'


def test_explicit_root_loads_remain_supported_without_span():
    report = analyze_wing_structure(REF_SPEC, {'area_m2': 14.39}, strong_box(),
                                    root_bending_moment_nm=100.0, root_shear_force_n=100.0)
    assert report['passed'] is True
