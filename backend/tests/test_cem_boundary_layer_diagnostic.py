"""Parser countercases: wake columns and transition-side eligibility."""
import importlib.util
from pathlib import Path

import numpy as np
import pytest

spec = importlib.util.spec_from_file_location('bl_diagnostic', Path(__file__).resolve().parents[2]/'scripts/cem/diagnose_coordinate_boundary_layer.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_bl_gradients_exclude_wake_and_untransitioned_surface(tmp_path):
    # Top is stored TE -> LE, bottom LE -> TE. The wake has fewer columns
    # and extreme values; neither wake nor lower laminar pairs count.
    rows = [[0,1,.01,1,0,0,0,2], [1,.8,.02,1.02,0,0,0,4],
            [2,.6,.03,1.04,0,0,0,6], [3,0,0,0,0,0,0,1],
            [4,.5,-.01,-1,0,0,0,50], [5,1,0,-1,0,0,0,1]]
    p=tmp_path/'bl.dat'
    p.write_text('\n'.join(' '.join(map(str,r))+ ' 0 0 0 0' for r in rows)+'\n6 1.2 0 9 0 0 0 99\n')
    polar=np.array([6,.6,.05,.04,-.05,.6,.9999,0,0])
    top,bottom=module.gradients(p,6,polar)
    assert top['transition_x']==.6
    assert top['eligible_pairs']==2
    assert top['max_hk_drop']==2
    assert top['max_ue_change']==pytest.approx(.02)
    assert len(top['violations'])==2
    assert bottom['eligible_pairs']==0
    assert bottom['max_hk_drop'] is None
