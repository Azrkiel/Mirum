"""Specific native whole-aircraft proof is separate from family availability."""
import copy
import re
import subprocess

import pytest

from cem.aero.qualification import reference_inputs, run_reference, validate_reference
from cem.aero.native import resolve, NativeFailure


def test_reference_inputs_are_frozen_and_countercase_has_its_own_bindings():
    spec,geometry,req,calibration=reference_inputs()
    again=reference_inputs()[1]
    assert geometry==again
    assert geometry.reference.mass_kg==.312
    assert geometry.reference.cg_m==(.259,0.,0.)
    assert req.constraints.max_speed_m_s==15.
    negative=reference_inputs(unstable=True)[1]
    assert geometry.surfaces==negative.surfaces and geometry.body==negative.body
    assert geometry.reference.mass_kg==negative.reference.mass_kg
    assert geometry.binding.geometry_sha256!=negative.binding.geometry_sha256
    assert geometry.binding.hardware_sha256!=negative.binding.hardware_sha256
    for name in ('mission_sha256','process_sha256','calibration_sha256','implementation_sha256'):
        assert getattr(geometry.binding,name)==getattr(negative.binding,name)


def test_missing_native_binary_records_a_failed_qualification(tmp_path,monkeypatch):
    monkeypatch.setenv('MIRUM_XFOIL_BIN','/missing/xfoil')
    value=run_reference(tmp_path)
    assert value['state']=='failed' and value['verified_gain'] is False
    assert value['families_enabled']==[]
    assert value['failure']['native_evidence']['state']=='unavailable'
    assert (tmp_path/'qualification.json').is_file()
    with pytest.raises(ValueError,match='successful'):
        validate_reference(value,tmp_path)


def test_qualification_preserves_existing_raw_evidence(tmp_path):
    (tmp_path/'previous.out').write_text('preserve')
    with pytest.raises(ValueError,match='empty directory'):
        run_reference(tmp_path)
    assert (tmp_path/'previous.out').read_text()=='preserve'


def test_native_frozen_aircraft_and_unstable_countercase_replay(tmp_path,monkeypatch):
    try:
        resolve('xfoil');resolve('avl')
    except NativeFailure:
        pytest.skip('Native XFOIL and AVL executables not selected')
    value=run_reference(tmp_path)
    assert value['state']=='passed',value.get('failure')
    assert value['verified_gain'] is False and value['families_enabled']==[]
    assert value['stability']['state']=='passed'
    assert value['limits']['static_margin_max']==.2
    assert .05<=value['stability']['static_margin']<=.2
    assert max(abs(x) for x in value['performance']['equilibrium_residual'])<1e-4
    assert value['performance']['numerical_solver']['method']=='covered-damped-Newton-v1'
    assert value['unstable_case']['state']=='rejected_as_expected'
    assert value['unstable_case']['avl'][-1]['derivatives']['Cma']>0
    assert 'pitch stability/static margin' in value['unstable_case']['reason']
    def forbidden(*args,**kwargs): pytest.fail('Replay must not execute a native solver')
    monkeypatch.setattr(subprocess,'run',forbidden)
    validate_reference(value,tmp_path)
    forged=copy.deepcopy(value);forged['verified_gain']=True
    with pytest.raises(ValueError,match='differs'):
        validate_reference(forged,tmp_path)
    output=tmp_path/'avl_high/forces.ft'
    output.write_text(re.sub(r'(CLtot\s*=\s*)\S+',r'\g<1>9.0',output.read_text()))
    with pytest.raises(ValueError,match='conservation|differs|disagreement'):
        validate_reference(value,tmp_path)
