"""Convergence diagnostics retain gaps and never become performance evidence."""
import math
import subprocess
import sys

import pytest

from cem.aero.contracts import Transition
from cem.aero.native import NativeFailure,resolve
from cem.aero.polars import query,script
from cem.aero.qualification import reference_inputs
from cem.aero.polar_audit import run_section_audit,validate_section_audit
from cem.core import canonical_json


@pytest.mark.parametrize('nonfinite',[False,True])
def test_native_polar_gaps_and_nonfinite_output_have_distinct_evidence(tmp_path,monkeypatch,nonfinite):
    tool=tmp_path/'xfoil-double'
    row='0 nan .01 0 0\n' if nonfinite else ''
    source='''import pathlib,sys
text=sys.stdin.read().splitlines()
output=text[text.index('PACC')+1]
pathlib.Path(output).write_text(%r)
''' % ('alpha CL CD CDp Cm\n------\n-1 -.1 .01 0 0\n'+row+'1 .1 .01 0 0\n')
    tool.write_text('#!'+sys.executable+'\n'+source);tool.chmod(0o755)
    monkeypatch.setenv('MIRUM_XFOIL_BIN',str(tool))
    binding=reference_inputs()[1].binding
    with pytest.raises(NativeFailure) as caught:
        query(binding,'0012',100000.,[-1.,0.,1.],tmp_path/'run')
    evidence=caught.value.evidence
    canonical_json(evidence)
    assert len(evidence['artifacts'])==3
    if nonfinite:
        assert evidence['state']=='invalid_output'
    else:
        assert evidence['state']=='nonconvergence'
        assert evidence['coverage']['missing_alpha_deg']==[0.]
        assert evidence['coverage']['transition']==Transition().model_dump(mode='json')


def test_nonfinite_requested_angles_are_rejected_before_native_execution():
    with pytest.raises(ValueError,match='angle envelope'):
        script('0012',100000.,[-1.,float('nan'),1.],Transition(),'polar.out')


def test_unavailable_solver_cannot_make_a_section_audit_admissible(tmp_path,monkeypatch):
    monkeypatch.setenv('MIRUM_XFOIL_BIN','/missing/xfoil')
    value=run_section_audit(tmp_path,'0006',30000.,[-1.,0.,1.])
    assert value['state']=='diagnostic_failed'
    assert value['production_admissible'] is False and value['verified_gain'] is False
    assert value['repeatable'] is None
    assert all(s['state']=='failed' for s in value['sequences'])


def test_native_initialization_audit_replays_without_running_tools(tmp_path,monkeypatch):
    try: resolve('xfoil')
    except NativeFailure: pytest.skip('Native XFOIL executable not selected')
    report=run_section_audit(tmp_path,'0006',30000.,[i*.5 for i in range(-8,17)])
    assert report['state']=='diagnostic_completed'
    assert report['production_admissible'] is False
    assert report['verified_gain'] is False
    assert report['repeatable'] is True
    assert len(report['sequences'])==4
    assert all(s['rows']+len(s['missing_alpha_deg'])==25 for s in report['sequences'])
    def forbidden(*args,**kwargs): pytest.fail('Section replay must not execute native tools')
    monkeypatch.setattr(subprocess,'run',forbidden)
    validate_section_audit(report,tmp_path)
    log=tmp_path/'ascending/audit.log';log.write_text(log.read_text()+'\ntampered\n')
    with pytest.raises(ValueError,match='differs'):
        validate_section_audit(report,tmp_path)
