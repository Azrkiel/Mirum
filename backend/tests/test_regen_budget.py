"""Small allocation-boundary regressions; no generated STL or adversarial fixtures."""
import numpy as np
import pytest
from types import SimpleNamespace
from scipy.spatial import cKDTree

from engineering import regen_geometry as rg
from engineering import regen_mesh as rm
from tests.test_regen_spec import make


class CountedTree:
    def __init__(self, points):
        self.tree = cKDTree(points)
        self.calls = []
        self.counted_rows = 0
        self.count_queries = []

    def query_ball_point(self, *args, **kwargs):
        counting = kwargs.get('return_length', False)
        points = np.atleast_2d(args[0])
        rows = len(points)
        if counting:
            self.counted_rows += rows
            self.count_queries.append(rows)
        if not counting:
            assert self.counted_rows >= rows
            self.counted_rows -= rows
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


@pytest.mark.parametrize('stage', ['_Ctx', '_build_leaves', '_fidelity', '_coverage', '_thickness'])
def test_memory_error_becomes_failed_checks(monkeypatch, stage):
    def oom(*args, **kwargs):
        raise MemoryError
    if stage in ('_fidelity', '_coverage', '_thickness'):
        monkeypatch.setattr(rm, '_build_leaves', lambda *a: {})
        monkeypatch.setattr(rm, '_fidelity', lambda *a: {'state': 'pass'})
    if stage == '_thickness':
        monkeypatch.setattr(rm, '_coverage', lambda *a: {'state': 'pass'})
    monkeypatch.setattr(rm, stage, oom)
    checks, failures, _ = rm.whole_part_checks(make(), np.zeros((3, 3)), np.array([[0, 1, 2]]))
    assert len(checks) == 3 and all(c['state'] == 'fail' for c in checks.values())
    assert failures and all('memory_budget' in f for f in failures)


def small_budget(**limits):
    b = rm._Budget()
    b.live = 0
    b.limits.update(limits)
    return b


def test_candidate_mapping_is_guarded_before_conversion():
    p = np.zeros((1, 3))
    tree = CountedTree(p)
    b = small_budget()
    b.memory_bytes = 20_000
    with pytest.raises(rm._Exhausted, match='memory_budget'):
        list(rm._candidates(p, [0], tree, range(100_000), 1., b))
    assert not tree.calls


def test_reference_plan_holds_storage_during_consumer_allocations():
    b = rg.Budget(memory_bytes=1 << 30)
    before = b.live
    chunks = rg._plan_chunks(np.array([3, 2]), b)
    next(chunks)
    assert b.live >= before + 6 * 512
    b.memory_bytes = b.live
    with pytest.raises(rm._Exhausted, match='memory_budget'):
        b.memory(1)
    chunks.close()
    assert b.live == before


@pytest.mark.parametrize('method', ['iter_coolant_segments', 'centerline_samples'])
def test_reference_generators_charge_live_yields(method):
    b = rg.Budget()
    g = rg.RegenGeometry(make(), b)
    before = b.live
    gen = getattr(g, method)(batch=1)
    next(gen)
    assert b.live > before
    gen.close()
    assert b.live == before


def leaf_context(monkeypatch, b, target=1.):
    def conform(ctx, c, r):
        return dict(conform=np.ones(len(c), dtype=bool), reason=np.zeros(len(c), dtype=np.int8),
                    label=np.zeros(len(c), dtype=np.int8), cert=np.zeros(len(c), dtype=bool))
    monkeypatch.setattr(rm, '_classify', conform)
    return SimpleNamespace(budget=b, rho_target=target)


def triangle():
    return np.array([[0., 0, 0], [.1, 0, 0], [0, .1, 0]]), np.array([[0, 1, 2]])


def test_leaf_initial_queue_checked_before_triangle_allocation(monkeypatch):
    b = small_budget(queue_budget=0)
    ctx = leaf_context(monkeypatch, b)
    monkeypatch.setattr(rm, '_tri_normals', lambda *a: pytest.fail('allocated initial triangles'))
    with pytest.raises(rm._Exhausted, match='queue_budget'):
        rm._build_leaves(ctx, *triangle())


def test_leaf_final_concat_has_no_unaccounted_scratch(monkeypatch):
    b = small_budget()
    ctx = leaf_context(monkeypatch, b)
    concat = rm.np.concatenate
    def checked(parts, *a, **kw):
        # Final arrays are charged alongside every retained leaf block.
        assert b.live >= 2128
        return concat(parts, *a, **kw)
    monkeypatch.setattr(rm.np, 'concatenate', checked)
    L = rm._build_leaves(ctx, *triangle())
    assert b.live == 2128
    assert len(L['T']) == 1


def pair_leaves(scale=1.):
    t = np.array([[[0., 0, 0], [scale, 0, 0], [0, scale, 0]]])
    return dict(T=np.concatenate([t, t + [0, 0, .01]]).astype(np.float32),
                nrm=np.array([[0., 0, -1], [0, 0, 1]], dtype=np.float32),
                label=np.array([rm.L_HOT, rm.L_CH], dtype=np.int8))


def pair_state():
    return dict(evaluated=0, violations=rm._Findings(), marginal=rm._Findings(), too_deep=0)


def test_zero_refinement_queue_names_queue_budget():
    with pytest.raises(rm._Exhausted, match='queue_budget'):
        rm._run_pairs(pair_leaves(), np.array([0]), np.array([1]), 1., .01, None,
                      pair_state(), small_budget(queue_budget=0))


def test_refinement_depth_is_named_exhaustion(monkeypatch):
    monkeypatch.setattr(rm, 'WP_PAIR_MAX_SPLITS', 0)
    with pytest.raises(rm._Exhausted, match='refinement_depth_budget'):
        rm._run_pairs(pair_leaves(), np.array([0]), np.array([1]), 1., .01, None,
                      pair_state(), small_budget())


def test_refinement_scratch_stays_charged_while_children_allocate(monkeypatch):
    b = small_budget()
    split = rm._split4
    seen = []
    def checked(t):
        seen.append(len(t))
        assert b.live >= 8192 + 4096 + 4 * len(t) * 4096
        return split(t)
    monkeypatch.setattr(rm, '_split4', checked)
    rm._run_pairs(pair_leaves(.01), np.array([0]), np.array([1]), 1., .01, None,
                  pair_state(), b)
    assert seen


def test_leaf_scratch_is_released_before_final_concat(monkeypatch):
    import weakref
    b = small_budget()
    ctx = leaf_context(monkeypatch, b)
    refs = []
    stats, concat = rm._tri_stats, rm.np.concatenate
    def observed(t):
        refs.append(weakref.ref(t))
        return stats(t)
    def checked(parts, *a, **kw):
        assert all(ref() is None for ref in refs)
        return concat(parts, *a, **kw)
    monkeypatch.setattr(rm, '_tri_stats', observed)
    monkeypatch.setattr(rm.np, 'concatenate', checked)
    rm._build_leaves(ctx, *triangle())


@pytest.mark.parametrize('kind', ['candidate', 'coverage'])
@pytest.mark.parametrize('limit', ['batch', 'work', 'memory'])
def test_dense_count_rejects_before_materialization(kind, limit):
    p = np.zeros((5, 3))
    tree = CountedTree(p)
    b = small_budget()
    if limit == 'batch':
        b.limits[kind + '_batch_budget'] = 4
    elif limit == 'work':
        b.limits[kind + '_budget'] = 4
    else:
        # Allow mapping/carry storage, but not the counted neighbor list.
        b.memory_bytes = 4096 + 5 * 8 + 3 * rm.WP_NEIGHBOR_BUDGET * 16 + 128
    name = 'memory_budget' if limit == 'memory' else kind + ('_batch_budget' if limit == 'batch' else '_budget')
    with pytest.raises(rm._Exhausted, match=name):
        list(rm._candidates(p, [0], tree, np.arange(5), 1., b, kind=kind))
    assert tree.calls == [True]


@pytest.mark.parametrize('block', [1, 3, 100])
def test_candidates_match_brute_force_with_mapping_and_batches(monkeypatch, block):
    p = np.array([[0., 0, 0], [.2, 0, 0], [.4, 0, 0], [0, 2, 0]])
    targets = np.array([2, 0, 3])
    monkeypatch.setattr(rm, 'WP_BLOCK', block)
    batches = rm._candidates(p, [3, 0, 2, 1], cKDTree(p[targets]), targets, .3, small_budget())
    got = [(int(i), int(j)) for ii, jj in batches for i, j in zip(ii, jj)]
    expected = [(i, int(j)) for i in [3, 0, 2, 1] for j in targets
                if np.linalg.norm(p[i] - p[j]) <= .3]
    assert got == expected


def brute_components(q, r):
    labels = list(range(len(q)))
    for i in range(len(q)):
        for j in range(i):
            if np.linalg.norm(q[i] - q[j]) <= r[i] + r[j]:
                old, new = labels[j], labels[i]
                labels = [new if x == old else x for x in labels]
    return len(set(labels))


@pytest.mark.parametrize('block', [1, 7, 100])
def test_variable_radius_connectivity_includes_distant_neighbors(monkeypatch, block):
    # Both clouds have >64 nearer neighbours. Only the two large balls bridge them.
    q = np.zeros((132, 3))
    q[66:, 0] = 1.
    r = np.full(132, .001)
    r[[65, 131]] = .51
    monkeypatch.setattr(rm, 'WP_BLOCK', block)
    assert rm._connectivity(q, r, small_budget()) == brute_components(q, r) == 1
    r[131] = .001
    assert rm._connectivity(q, r, small_budget()) == brute_components(q, r) == 2


def test_diagnostic_records_capped_counts_and_minimum_exact():
    records = []
    for order in (range(40), reversed(range(40))):
        findings = rm._Findings()
        for i in order:
            findings.add([i, 0, 0], {'i': i}, value=40 - i)
            assert len(findings.rows) <= rm.WP_REPORT_LOCATIONS
        assert findings.count == 40 and findings.minimum == 1
        records.append(findings.records())
    assert records[0] == records[1]


@pytest.mark.parametrize('limit,amount', [('queue_budget', 3), ('scratch_budget', 4096),
                                        ('refinement_budget', 4), ('memory_budget', 28_000)])
def test_refinement_rejects_before_child_allocation(monkeypatch, limit, amount):
    b = small_budget()
    if limit == 'memory_budget':
        b.memory_bytes = amount
    else:
        b.limits[limit] = amount
    monkeypatch.setattr(rm, '_split4', lambda *a: pytest.fail('allocated children'))
    with pytest.raises(rm._Exhausted, match=limit):
        rm._run_pairs(pair_leaves(), np.array([0]), np.array([1]), 1., .01, None,
                      pair_state(), b)


@pytest.mark.parametrize('limit,amount', [('queue_budget', 3), ('leaf_budget', 4),
                                        ('leaf_batch_budget', 3), ('memory_budget', 5_000)])
def test_leaf_split_rejects_before_child_allocation(monkeypatch, limit, amount):
    b = small_budget()
    if limit == 'memory_budget':
        b.memory_bytes = amount
    else:
        b.limits[limit] = amount
    ctx = leaf_context(monkeypatch, b, target=.001)
    monkeypatch.setattr(rm, '_split4', lambda *a: pytest.fail('allocated children'))
    with pytest.raises(rm._Exhausted, match=limit):
        rm._build_leaves(ctx, *triangle())


def test_leaf_retained_storage_rejected_before_selection(monkeypatch):
    b = small_budget()
    ctx = leaf_context(monkeypatch, b)
    b.memory_bytes = 5000
    with pytest.raises(rm._Exhausted, match='memory_budget'):
        rm._build_leaves(ctx, *triangle())
    assert b.live == 0


def test_leaf_tight_memory_rejects_before_final_arrays(monkeypatch):
    b = small_budget()
    b.memory_bytes = 215_000
    ctx = leaf_context(monkeypatch, b)
    monkeypatch.setattr(rm, 'WP_LEAF_BATCH', 1)
    monkeypatch.setattr(rm.np, 'concatenate', lambda *a, **kw: pytest.fail('allocated final copy'))
    v, f = triangle()
    with pytest.raises(rm._Exhausted, match='memory_budget'):
        rm._build_leaves(ctx, v, np.repeat(f, 100, axis=0))


@pytest.mark.parametrize('stage', ['_Ctx', '_build_leaves', '_fidelity', '_coverage', '_thickness'])
def test_named_exhaustion_fails_all_checks_deterministically(monkeypatch, stage):
    for earlier in ['_build_leaves', '_fidelity', '_coverage', '_thickness']:
        if earlier == stage:
            break
        monkeypatch.setattr(rm, earlier, lambda *a: {'state': 'pass'})
    def exhausted(*args, **kwargs):
        raise rg.BudgetExceeded('test_work_budget exhausted')
    monkeypatch.setattr(rm, stage, exhausted)
    v, f = triangle()
    a = rm.whole_part_checks(make(), v, f)
    assert a == rm.whole_part_checks(make(), v, f)
    assert all(c['state'] == 'fail' and 'test_work_budget' in c['detail'] for c in a[0].values())
    assert len(a[1]) == 3


def test_whole_part_accounts_for_input_storage(monkeypatch):
    b = small_budget()
    b.memory_bytes = 4100
    monkeypatch.setattr(rm, '_Ctx', lambda *a: SimpleNamespace(budget=b))
    monkeypatch.setattr(rm, '_build_leaves', lambda *a: pytest.fail('input storage not guarded'))
    checks, failures, _ = rm.whole_part_checks(make(), *triangle())
    assert all(c['state'] == 'fail' for c in checks.values())
    assert all('memory_budget' in f for f in failures)


@pytest.mark.parametrize('stage', ['_Ctx', '_build_leaves', '_fidelity', '_coverage', '_thickness'])
@pytest.mark.parametrize('error,budget_name', [(MemoryError, 'memory_budget'),
                                             (rg.BudgetExceeded, 'injected_work_budget')])
def test_report_rejects_incomplete_checks(monkeypatch, stage, error, budget_name):
    from tests.test_regen_mesh import box
    for earlier in ['_build_leaves', '_fidelity', '_coverage', '_thickness']:
        if earlier == stage:
            break
        monkeypatch.setattr(rm, earlier, lambda *a: {'state': 'pass'})
    def exhausted(*a, **kw):
        raise error('injected_work_budget exhausted')
    monkeypatch.setattr(rm, stage, exhausted)
    # Isolate whole-part failures from the legacy geometry gates.
    monkeypatch.setattr(rm, 'check_bbox', lambda *a: {'state': 'pass'})
    monkeypatch.setattr(rm, 'check_volume', lambda *a: {'state': 'pass'})
    monkeypatch.setattr(rm, 'station_zs', lambda *a: [])
    a, _ = rm.verify(make(), box())
    b, _ = rm.verify(make(), box())
    assert a == b
    assert a['passed'] is False and a['design_intent']['state'] == 'geometry_rejected'
    assert a['engineering_certified'] is False
    assert a['physics']['state'] == a['standards']['state'] == 'not_evaluated'
    for name in ['circuit_fidelity', 'circuit_coverage', 'wall_thickness']:
        assert a['checks'][name]['state'] == 'fail'
        assert budget_name in a['checks'][name]['detail']
    assert all(budget_name in f for f in a['failures'])
    assert all(c['state'] == 'pass' for n, c in a['checks'].items()
               if n not in ['circuit_fidelity', 'circuit_coverage', 'wall_thickness'])


def tiny_spec():
    return make(chamber_radius_mm=5., throat_radius_mm=4., exit_radius_mm=5.,
                chamber_length_mm=10., convergent_length_mm=10., divergent_length_mm=20.,
                channel_count=4)


def test_reference_distances_match_all_segments_across_batches(monkeypatch):
    g = rg.RegenGeometry(tiny_spec())
    points = np.array([[8.5, 0, 8.], [0, 8.5, 8.], [13.0, 0, 8.], [0, 0, 20.]])
    md = 2.
    expected = {f: np.full(len(points), md) for f in ('channels', 'rings', 'ports')}
    names = dict(channel='channels', ring='rings', port='ports')
    for fam, a, b, r in g.iter_coolant_segments(batch=17):
        dist = rg._seg_dist(points[:, None, :], a[None, :, :], b[None, :, :]).min(axis=1) - r
        expected[names[fam]] = np.minimum(expected[names[fam]], dist)
    for block in (128, 512, 2048):
        monkeypatch.setattr(rg, '_ELEMENT_BUDGET', block)
        actual = rg.RegenGeometry(tiny_spec()).coolant_sdf_parts(points, md)
        for fam in expected:
            np.testing.assert_allclose(actual[fam], expected[fam], rtol=0, atol=1e-12)


def test_centerline_samples_identical_across_segment_batches():
    outputs = []
    for batch in (1, 7, 128):
        g = rg.RegenGeometry(tiny_spec())
        outputs.append([(f, p.tolist(), r) for f, p, r in g.centerline_samples(batch=batch)])
    assert outputs[0] == outputs[1] == outputs[2]


def test_leaf_fidelity_preserves_results_across_batches(monkeypatch):
    verts = np.array([[5., 0, 4.], [5., .4, 4.], [5., 0, 4.4]])
    faces = np.array([[0, 1, 2], [0, 1, 2]])
    results = []
    for batch in (4, 8, 16):
        monkeypatch.setattr(rm, 'WP_LEAF_BATCH', batch)
        ctx = rm._Ctx(tiny_spec())
        L = rm._build_leaves(ctx, verts, faces)
        results.append(rm._fidelity(ctx, L))
        assert results[-1]['state'] == 'pass'
        assert len(L['T']) == 8
        assert ctx.budget.used['leaf_budget'] == 10
    assert results[0] == results[1] == results[2]


def test_refinement_results_identical_across_batches_and_split_sides(monkeypatch):
    results = []
    for block in (1, 2, 8):
        monkeypatch.setattr(rm, 'WP_BLOCK', block)
        L = pair_leaves(.01)
        L['T'][1, :, :2] *= 2  # Force side B to be split before side A.
        state = pair_state()
        rm._run_pairs(L, np.zeros(3, dtype=int), np.ones(3, dtype=int), 1., .01, None,
                      state, small_budget())
        assert state['evaluated'] > 3
        assert state['violations'].count > 0
        assert state['violations'].minimum == pytest.approx(.01)
        results.append((state['evaluated'], state['violations'].count,
                        state['violations'].records(), state['marginal'].records(), state['too_deep']))
    assert results[0] == results[1] == results[2]


def test_coverage_connectivity_uses_counting_without_query_pairs(monkeypatch):
    p = np.zeros((5, 3))
    tree = CountedTree(p)
    monkeypatch.setattr(rm, 'cKDTree', lambda *a: tree)
    b = small_budget(coverage_batch_budget=4)
    with pytest.raises(rm._Exhausted, match='coverage_batch_budget'):
        rm._connectivity(p, np.ones(5), b)
    assert tree.calls == [True]
    assert b.live == 0


def test_reference_single_item_checks_memory_and_work_before_nodes(monkeypatch):
    g = rg.RegenGeometry(tiny_spec())
    g.budget.limits['reference_budget'] = 0
    monkeypatch.setattr(g, '_nodes', lambda *a: pytest.fail('allocated nodes'))
    with pytest.raises(rm._Exhausted, match='reference_budget'):
        next(g.iter_coolant_segments(batch=1))


def test_reference_sample_limit_rejected_before_grid(monkeypatch):
    g = rg.RegenGeometry(tiny_spec())
    monkeypatch.setattr(g, 'iter_coolant_segments', lambda *a: iter([('channel', np.zeros((1, 3)),
                                                                    np.ones((1, 3)), 1.)]))
    g.budget.limits['sample_batch_budget'] = 1
    monkeypatch.setattr(rg.np, 'arange', lambda *a, **kw: pytest.fail('allocated sample grid'))
    with pytest.raises(rm._Exhausted, match='sample_batch_budget'):
        next(g.centerline_samples())


def test_generator_accounting_released_on_consumer_failure():
    b = small_budget()
    p = np.zeros((2, 3))
    batches = rm._candidates(p, [0], cKDTree(p), np.arange(2), 1., b)
    next(batches)
    assert b.live > 0
    batches.close()
    assert b.live == 0


def test_reference_plan_applies_effective_single_item_limit(monkeypatch):
    b = rg.Budget(limits={'reference_batch_budget': 10})
    monkeypatch.setattr(rg, '_ELEMENT_BUDGET', 3)
    monkeypatch.setattr(rg.np, 'argsort', lambda *a, **kw: pytest.fail('allocated order'))
    with pytest.raises(rm._Exhausted, match='reference_batch_budget'):
        next(rg._plan_chunks(np.array([4]), b))


def test_candidate_row_keeps_every_neighbor_beyond_64(monkeypatch):
    p = np.zeros((70, 3))
    monkeypatch.setattr(rm, 'WP_BLOCK', 7)
    batches = list(rm._candidates(p, [0], CountedTree(p), np.arange(70), 1., small_budget()))
    assert [int(j) for _, jj in batches for j in jj] == list(range(70))
    assert all(len(ii) == len(jj) == 7 for ii, jj in batches)


def test_refinement_scratch_limit_covers_parent_and_children(monkeypatch):
    b = small_budget(scratch_budget=4 * 4096)
    monkeypatch.setattr(rm, '_split4', lambda *a: pytest.fail('allocated children above combined scratch limit'))
    with pytest.raises(rm._Exhausted, match='scratch_budget'):
        rm._run_pairs(pair_leaves(), np.array([0]), np.array([1]), 1., .01, None,
                      pair_state(), b)


@pytest.mark.parametrize('kind', ['candidate', 'coverage'])
def test_neighbor_work_is_cumulative_across_rows(kind):
    p = np.zeros((4, 3))
    tree = CountedTree(p)
    b = small_budget(**{kind + '_budget': 7})
    with pytest.raises(rm._Exhausted, match=kind + '_budget'):
        list(rm._candidates(p, [0, 1], tree, np.arange(4), 1., b, kind=kind))
    assert tree.count_queries == [2]
    assert tree.calls == [True, False]
    assert b.used[kind + '_budget'] == 4


def test_reference_classification_skips_mandatory_split_parents(monkeypatch):
    verts = np.array([[5., 0, 4.], [5., .4, 4.], [5., 0, 4.4]])
    faces = np.array([[0, 1, 2]])
    ctx = rm._Ctx(tiny_spec())
    radii = []
    classify = rm._classify

    def track(context, points, patch_radii):
        radii.extend(patch_radii.tolist())
        return classify(context, points, patch_radii)

    monkeypatch.setattr(rm, '_classify', track)
    leaves = rm._build_leaves(ctx, verts, faces)

    assert rm._fidelity(ctx, leaves)['state'] == 'pass'
    assert len(leaves['T']) == 4
    assert len(radii) == len(leaves['T'])
    assert max(radii) <= ctx.rho_target


def test_channel_queries_reuse_reference_nodes(monkeypatch):
    g = rg.RegenGeometry(tiny_spec())
    p = g._nodes(np.asarray(0), np.asarray(g.steps // 2))[None, :]
    expected = g.coolant_sdf(p)
    monkeypatch.setattr(g, '_nodes', lambda *a: pytest.fail('rebuilt fixed channel geometry'))
    np.testing.assert_array_equal(g.coolant_sdf(p), expected)


def test_channel_queries_fit_budget_using_nearby_capsules():
    g = rg.RegenGeometry(tiny_spec())
    p = g._nodes(np.asarray(0), np.asarray(g.steps // 2))[None, :]
    g.budget.limits['reference_budget'] = g.budget.used.get('reference_budget', 0) + 3000
    distances = g.coolant_sdf(np.repeat(p, 200, axis=0))
    np.testing.assert_allclose(distances, -g.rc, rtol=0, atol=1e-12)


def test_reference_queries_match_full_capsule_union_near_boundaries():
    spec = tiny_spec()
    rng = np.random.default_rng(94731)
    points = rng.uniform([-14., -14., -3.], [14., 14., 43.], size=(120, 3))
    g = rg.RegenGeometry(spec)
    expected = {f: np.full(len(points), 2.) for f in ('channels', 'rings', 'ports')}
    names = dict(channel='channels', ring='rings', port='ports')
    for family, a, b, radius in g.iter_coolant_segments(batch=13):
        distances = rg._seg_dist(points[:, None, :], a[None, :, :], b[None, :, :]).min(axis=1) - radius
        expected[names[family]] = np.minimum(expected[names[family]], distances)
    actual = rg.RegenGeometry(spec).coolant_sdf_parts(points, 2.)
    for family in expected:
        np.testing.assert_allclose(actual[family], expected[family], rtol=0, atol=1e-12)


def test_leaf_finalization_fits_one_field_copy_budget(monkeypatch):
    b = small_budget()
    b.memory_bytes = 220_000
    ctx = leaf_context(monkeypatch, b)
    monkeypatch.setattr(rm, 'WP_LEAF_BATCH', 1)
    verts, faces = triangle()
    leaves = rm._build_leaves(ctx, verts, np.repeat(faces, 100, axis=0))
    assert len(leaves['T']) == 100
    assert b.live == 100 * 80 + 2048


def test_coverage_reuses_contiguous_leaf_points_with_bounded_tree_storage(monkeypatch):
    ctx = rm._Ctx(tiny_spec())
    ctx.budget = small_budget(coverage_batch_budget=4)
    ctx.budget.memory_bytes = 60_000
    monkeypatch.setattr(ctx.g, 'centerline_samples', lambda *a: iter([
        ('channel', np.array([[0., 0, 10.]]), ctx.g.rc)]))
    leaves = dict(cen=np.zeros((300, 3)), nrm=np.tile([0., 0, 1.], (300, 1)),
                  rho=np.full(300, .01))
    result = rm._coverage(ctx, leaves)
    assert result['samples'] == 1
    assert ctx.budget.live == 0


def test_thickness_keeps_only_one_target_tree_live(monkeypatch):
    import weakref
    ctx = rm._Ctx(tiny_spec())
    real_tree = rm.cKDTree
    inputs = []

    def counted(points, *args, **kwargs):
        inputs[:] = [ref for ref in inputs if ref() is not None]
        # The port-rim tree may coexist with only the current target tree.
        assert len(inputs) < 2
        tree = real_tree(points, *args, **kwargs)
        inputs.append(weakref.ref(tree.data))
        return tree

    monkeypatch.setattr(rm, 'cKDTree', counted)
    monkeypatch.setattr(rm, '_candidates', lambda *a, **kw: iter(()))
    leaves = pair_leaves(.1)
    leaves['T'] = np.tile(leaves['T'], (4, 1, 1))
    leaves['cen'] = leaves['T'].mean(axis=1).astype(np.float64)
    leaves['nrm'] = np.tile(leaves['nrm'], (4, 1))
    leaves['rho'] = np.full(8, .1)
    leaves['label'] = np.array([rm.L_HOT, rm.L_COLD, rm.L_CAP0, rm.L_CAPL,
                                rm.L_CH, rm.L_CH, rm.L_RING, rm.L_PORT], dtype=np.int8)
    assert rm._thickness(ctx, leaves)['state'] == 'pass'


def test_candidates_pack_sparse_rows_into_bounded_batches(monkeypatch):
    points = np.column_stack([np.arange(40) * 2., np.zeros((40, 2))])
    monkeypatch.setattr(rm, 'WP_BLOCK', 16)
    batches = list(rm._candidates(points, range(40), cKDTree(points), np.arange(40), .1,
                                  small_budget()))
    assert [len(ii) for ii, jj in batches] == [16, 16, 8]
    np.testing.assert_array_equal(np.concatenate([ii for ii, jj in batches]), np.arange(40))
    np.testing.assert_array_equal(np.concatenate([jj for ii, jj in batches]), np.arange(40))


def test_pair_refinement_reserves_scratch_for_children():
    state = pair_state()
    rm._run_pairs(pair_leaves(.01), np.zeros(2000, dtype=np.int64),
                  np.ones(2000, dtype=np.int64), 1., .01, None, state, small_budget())
    assert state['evaluated'] > 2000
    assert state['violations'].count > 0


@pytest.mark.parametrize('coolant_label', [rm.L_RING, rm.L_PORT])
@pytest.mark.parametrize('at_opening', [False, True])
def test_port_opening_collar_handles_ring_port_overlap(coolant_label, at_opening):
    leaves = pair_leaves(.01)
    leaves['label'] = np.array([coolant_label, rm.L_COLD], dtype=np.int8)
    center = np.array([[0., 0, 0.]]) if at_opening else np.array([[10., 10, 10.]])
    state = pair_state()
    rm._run_pairs(leaves, np.array([0]), np.array([1]), 1., .1,
                  (cKDTree(center), .1), state, small_budget())
    assert (state['violations'].count == 0) == at_opening


@pytest.mark.parametrize('certified', [False, True])
def test_certified_fidelity_failure_stops_further_geometry_work(monkeypatch, certified):
    finding = dict(state='fail', nonconforming_patches=1,
                   uncertified_nonconforming_patches=0 if certified else 1,
                   by_reason={'surface_not_on_specified_geometry': 1},
                   locations=[{'at_mm': [0., 0., 0.]}], unsplit_over_radius_target=0)
    monkeypatch.setattr(rm, '_build_leaves', lambda *a: {'stats': {}})
    monkeypatch.setattr(rm, '_fidelity', lambda *a: finding)
    called = []

    def phase(name):
        def run(*a):
            if certified:
                pytest.fail('ran expensive checks after a certified geometry rejection')
            called.append(name)
            return dict(state='pass', classes={})
        return run

    monkeypatch.setattr(rm, '_coverage', phase('coverage'))
    monkeypatch.setattr(rm, '_thickness', phase('thickness'))
    checks, failures, _ = rm.whole_part_checks(tiny_spec(), *triangle())
    assert checks['circuit_fidelity']['state'] == 'fail'
    assert failures and failures[0].startswith('circuit_fidelity:')
    for name in ('circuit_coverage', 'wall_thickness'):
        assert checks[name]['state'] == ('not_evaluated' if certified else 'pass')
    assert called == ([] if certified else ['coverage', 'thickness'])


def test_fidelity_reports_certified_examples_first():
    leaves = dict(cen=np.array([[0., 0, 0.], [1., 0, 1.]]), rho=np.array([.01, .01]),
                  conform=np.array([False, False]), cert=np.array([False, True]),
                  reason=np.array([rm.R_OFF, rm.R_OFF]), stats={'unsplit_over_target': 0})
    result = rm._fidelity(rm._Ctx(tiny_spec()), leaves)
    assert result['certified_nonconforming_patches'] == 1
    assert result['locations'][0]['certified_deviation'] is True


def test_candidate_count_queries_are_batched_without_losing_rows():
    points = np.column_stack([np.arange(40) * 2., np.zeros((40, 2))])
    tree = CountedTree(points)
    batches = list(rm._candidates(points, range(40), tree, np.arange(40), .1, small_budget()))
    assert tree.count_queries == [40]
    np.testing.assert_array_equal(np.concatenate([ii for ii, jj in batches]), np.arange(40))
    np.testing.assert_array_equal(np.concatenate([jj for ii, jj in batches]), np.arange(40))
