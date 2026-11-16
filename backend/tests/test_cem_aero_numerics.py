"""Covered-domain numerical solves must distinguish rejected trials from evidence."""
import math

import numpy as np
import pytest


def test_covered_newton_recovers_after_outside_domain_trial():
    from cem.aero.numerics import bounded_newton
    from cem.aero.polars import PolarCoverageError

    def residual(x):
        if not -.9 < x[0] < .9:
            raise PolarCoverageError('Outside qualified domain')
        return np.array([math.log(1-x[0])-math.log(.2)])

    result=bounded_newton(residual,np.array([-.8]))
    assert result['value'][0]==pytest.approx(.8,abs=1e-8)
    assert result['residual_norm']<1e-8
    assert result['rejected_domain_trials']>0


def test_covered_newton_rejects_a_root_outside_the_qualified_domain():
    from cem.aero.numerics import bounded_newton, NumericalConvergenceError
    from cem.aero.polars import PolarCoverageError

    def residual(x):
        if not -.9 < x[0] < .9:
            raise PolarCoverageError('Outside qualified domain')
        return np.array([x[0]-2.])

    with pytest.raises(NumericalConvergenceError):
        bounded_newton(residual,np.array([0.]))


def test_covered_newton_rejects_nonfinite_residuals():
    from cem.aero.numerics import bounded_newton, NumericalConvergenceError
    with pytest.raises(NumericalConvergenceError,match='Nonfinite'):
        bounded_newton(lambda x:np.array([float('nan')]),np.array([0.]))


def test_avl_force_axes_and_raw_operating_conditions_must_close():
    from cem.aero.avl import check_total_axes
    from cem.aero.contracts import OperatingPoint
    op=OperatingPoint(velocity_m_s=8.,alpha_deg=6.,density_kg_m3=1.225,viscosity_pa_s=1.8e-5)
    angle=math.radians(6.)
    values=dict(Alpha=6.,Beta=0.,Mach=0.,CLtot=.6,CDtot=.02,CDind=.02,CDvis=0.,
        CXtot=-(.02*math.cos(angle)-.6*math.sin(angle)),CZtot=-(.02*math.sin(angle)+.6*math.cos(angle)),
        **{'pb/2V':0.,'qc/2V':0.,'rb/2V':0.})
    check_total_axes(values,op)
    with pytest.raises(ValueError,match='conservation'):
        check_total_axes(dict(values,CLtot=.7),op)
    with pytest.raises(ValueError,match='operating point'):
        check_total_axes(dict(values,Alpha=7.),op)
