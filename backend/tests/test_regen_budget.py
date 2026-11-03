"""Small allocation-boundary regressions; no generated STL or adversarial fixtures."""
import numpy as np
import pytest
from scipy.spatial import cKDTree

from engineering import regen_geometry as rg
from engineering import regen_mesh as rm
from test_regen_spec import make


class CountedTree:
    def __init__(self, points):
        self.tree = cKDTree(points)
        self.calls = []

    def query_ball_point(self, *args, **kwargs):
        counting = kwargs.get('return_length', False)
        if not counting:
            assert self.calls and self.calls[-1] is True
        self.calls.append(counting)
        return self.tree.query_ball_point(*args, **kwargs)


def test_candidates_count_before_materializing_and_complete(monkeypatch):
    p = np.array([[0., 0, 0], [.1, 0, 0], [.2, 0, 0]])
    tree = CountedTree(p)
    monkeypatch.setattr(rm, 'WP_BLOCK', 2)
    batches = list(rm._candidates(p, np.arange(3), tree, np.arange(3), .15, rm._Budget()))
    got = [(int(a), int(b)) for ii, jj in batches for a, b in zip(ii, jj)]
    expected = [(i, j) for i in range(3) for j in range(3) if np.linalg.norm(p[i] - p[j]) <= .15]
    assert got == expected
    assert all(len(ii) <= 2 for ii, _ in batches)


def test_dense_candidate_row_rejected_before_list(monkeypatch):
    p = np.zeros((4, 3))
    tree = CountedTree(p)
    monkeypatch.setattr(rm, 'WP_NEIGHBOR_BUDGET', 3)
    with pytest.raises(rm._Exhausted, match='candidate_batch_budget'):
        list(rm._candidates(p, np.arange(4), tree, np.arange(4), 1., rm._Budget()))
    assert tree.calls == [True]


def test_reference_single_item_over_budget():
    with pytest.raises(rm._Exhausted, match='reference_batch_budget'):
        list(rg._plan_chunks(np.array([rg._ELEMENT_BUDGET + 1])))


@pytest.mark.parametrize('stage', ['_Ctx', '_build_leaves', '_coverage', '_thickness'])
def test_memory_error_becomes_failed_checks(monkeypatch, stage):
    def oom(*args, **kwargs):
        raise MemoryError
    if stage in ('_coverage', '_thickness'):
        monkeypatch.setattr(rm, '_build_leaves', lambda *a: {})
        monkeypatch.setattr(rm, '_fidelity', lambda *a: {'state': 'pass'})
    if stage == '_thickness':
        monkeypatch.setattr(rm, '_coverage', lambda *a: {'state': 'pass'})
    monkeypatch.setattr(rm, stage, oom)
    checks, failures, _ = rm.whole_part_checks(make(), np.zeros((3, 3)), np.array([[0, 1, 2]]))
    assert len(checks) == 3 and all(c['state'] == 'fail' for c in checks.values())
    assert failures and all('memory_budget' in f for f in failures)
