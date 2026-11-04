"""A qualified numerical section interval cannot authorize installed flight."""
import copy
import subprocess

import pytest

from cem.aero.native import resolve,NativeFailure
from cem.aero.section_qualification import section_inputs,run_section_reference,validate_section_reference
from cem.aero.qualification import reference_inputs


def test_section_reference_is_narrow_and_not_bound_to_an_installed_aircraft():
    spec,binding=section_inputs()
    assert spec['naca']=='0006'
    assert spec['reynolds']==[30000.,40000.,50000.,60000.]
    assert spec['alpha_deg'][0]==-3. and spec['alpha_deg'][-1]==3.
    assert binding!=reference_inputs()[1].binding
    assert binding==section_inputs()[1]


def test_unavailable_native_tool_leaves_section_reference_failed(tmp_path,monkeypatch):
    monkeypatch.setenv('MIRUM_XFOIL_BIN','/missing/xfoil')
    result=run_section_reference(tmp_path)
    assert result['state']=='failed'
    assert result['aircraft_admission'] is False and result['verified_gain'] is False
    validate_section_reference(result,tmp_path)


def test_section_reference_preserves_previous_evidence(tmp_path):
    (tmp_path/'previous.out').write_text('preserve')
    with pytest.raises(ValueError,match='empty directory'):
        run_section_reference(tmp_path)
    assert (tmp_path/'previous.out').read_text()=='preserve'


def test_native_narrow_section_rejects_missing_midpoint_and_replays_failure(tmp_path,monkeypatch):
    try: resolve('xfoil')
    except NativeFailure: pytest.skip('Native XFOIL executable not selected')
    result=run_section_reference(tmp_path)
    assert result['state']=='failed',result
    assert result['stage']=='interpolation_coverage'
    coverage=result['failure']['native_evidence']['coverage']
    assert coverage['missing_alpha_deg']==[2.125]
    assert coverage['reynolds']==pytest.approx((30000*40000)**.5)
    assert result['aircraft_admission'] is False and result['verified_gain'] is False
    assert result['families_enabled']==[]
    assert all(v['all_sequences_complete'] for v in result['initialization_audits'])
    assert all(v['repeatable'] for v in result['initialization_audits'])
    assert result['panel_differences']['cl']<.025
    def forbidden(*args,**kwargs): pytest.fail('Section proof replay must not execute native tools')
    monkeypatch.setattr(subprocess,'run',forbidden)
    validate_section_reference(result,tmp_path)
    forged=copy.deepcopy(result);forged['aircraft_admission']=True
    with pytest.raises(ValueError,match='differs'):
        validate_section_reference(forged,tmp_path)
    forged=copy.deepcopy(result);forged['state']='passed'
    with pytest.raises(ValueError,match='differs'):
        validate_section_reference(forged,tmp_path)
    native=next((tmp_path/'interpolation_30000_40000').glob('*.out'))
    native.write_text(native.read_text().replace('\n   2.000', '\n   2.125'))
    with pytest.raises(ValueError,match='differs'):
        validate_section_reference(result,tmp_path)
