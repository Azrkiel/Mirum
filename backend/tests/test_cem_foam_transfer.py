"""Force/moment reference for measured polygonal bonded-interface screening."""
import math

import numpy as np
import pytest

from cem.glider.foam_transfer import traction_resultants, analyze, _support_match
from backend.tests.test_cem_attachment import case


@pytest.mark.parametrize('count', [16, 32, 64, 128])
@pytest.mark.parametrize('force,torque', [(2., .01), (-3., -.02), (0., .04)])
def test_nominal_traction_conserves_force_and_torque(count, force, torque):
    theta = np.linspace(0, 2*math.pi, count+1)
    contour = .003*np.column_stack([np.cos(theta), np.sin(theta)])
    r = traction_resultants(contour, force, torque)
    assert r == pytest.approx(dict(fx_n_m=0., fz_n_m=force, my_nm_m=torque), abs=1e-12)
    reverse = traction_resultants(contour[::-1], force, torque)
    assert reverse == pytest.approx(r, abs=1e-12)
    translated = traction_resultants(contour+[.042, .01], force, torque)
    assert translated == pytest.approx(r, abs=1e-12)


def interfaces():
    return [dict(name=f'foam_{side}_{i}', bond_name='adhesive', side=side,
        bounds_y_m=[side*i*.125, side*(i+1)*.125],
        perimeter_m=2*math.pi*.003, bore_area_m2=math.pi*.003**2)
        for side in (-1, 1) for i in range(4)]


def test_nominal_stress_reference_and_overload_rejection():
    e = interfaces()
    r = analyze(case(2., .01), case(2., .01), e)
    assert r['state'] == 'incomplete' and r['nominal_checks_state'] == 'passed'
    first = r['interfaces'][0]
    assert first['nominal_normal_pa'] == pytest.approx(2/(2*math.pi*.003))
    assert first['nominal_shear_pa'] == pytest.approx(2/(2*math.pi*.003)+.01/(2*math.pi*.003**2))
    assert analyze(case(100000., 100.), case(100000., 100.), e)['state'] == 'failed'
    with pytest.raises(ValueError, match='Missing foam'):
        analyze(case(), case(), e[:-1])


def test_polygon_interface_certificate_accepts_subdivision_and_rejects_gap():
    theta = np.linspace(0, 2*math.pi, 65)
    contour = np.column_stack([.003*np.cos(theta), np.zeros(65), .003*np.sin(theta)])
    subdivided = []
    for a, b in zip(contour, contour[1:]): subdivided.extend([a, (a+b)/2])
    subdivided.append(contour[-1]); subdivided = np.asarray(subdivided)
    _support_match(contour, subdivided)
    with pytest.raises(ValueError, match='gap'):
        _support_match(contour, subdivided*1.01)
