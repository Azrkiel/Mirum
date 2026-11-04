"""Bounded MAP-Elites over the existing qualified layouts and parameter search.

Unsupported families are explicit capabilities, never ordinary conventional
aircraft relabelled as novel. Independent solver rechecks are not implied.
"""
from collections import Counter
from dataclasses import asdict
import random

from gliders.search import sample, finite_json
from gliders.design_spec import preset, placement
from .model import configuration, as_sized, evaluate_candidate, SUPPORTED_FAMILIES
from .processes import PROCESSES
from .shapes import sample_shape

ALL_FAMILIES = ['conventional', 't_tail', 'v_tail', 'tailless_swept', 'flying_plank',
                'canard', 'tandem', 'box', 'blended_body']


def cell(result):
    key = (result['tail_kind'], int(result['aspect_ratio'] // 2),
           int(result['wing_loading_g_dm2'] // 2), int(result.get('sweep_qc_deg', 0) // 3))
    factors = result.get('chord_shape_factors')
    return key + tuple(round((f-1)/.05) for f in factors[1:-1]) if factors else key


def discover(req, calibration, progress=lambda value: None):
    processes = list(PROCESSES) if req.process == 'auto' else [req.process]
    per_process, candidates, records = {}, [], []
    for process in processes:
        request, cfg = configuration(req, calibration, process)
        rng = random.Random(req.discovery.seed)
        archive, rejected, baseline = {}, Counter(), None
        for i in range(req.discovery.budget_evals):
            parent = None
            # A reproducible conventional comparator is always evaluated within
            # budget. Random/local resampling remains the existing Mirum search.
            if i == 0:
                gl = preset(request).build()
                gl.cfg = cfg
                # A buildable, larger-chord comparator reduces low-Re artefacts
                # without relaxing any admission threshold. These remain ordinary
                # search candidates, never presumed feasible or a bound on optimum.
                gl.wing.root_chord, gl.wing.taper = .18, .75
                gl.wing.span = min(1.25, req.constraints.max_span_m)
                if gl.tail.kind != 'V':
                    gl.tail.h_root_chord, gl.tail.h_span = .10, .30
                    gl.tail.v_root_chord, gl.tail.v_height, gl.tail.v_taper = .11, .12, .9
            else:
                parent = rng.choice([v[1] for _, v in sorted(archive.items())]) if archive and i % 4 else None
                gl = sample(rng, cfg, i + 1, parent, radius=max(.05, .25*(1-i/req.discovery.budget_evals)))
            # Retain one unmodified comparator and periodic template exploration.
            # The remaining budget explores real interior chord distributions.
            if req.discovery.wing_shape == 'multi_station' and i % 4:
                gl.wing = sample_shape(gl.wing, rng, parent.wing if parent else None,
                                       radius=max(.03, .15*(1-i/req.discovery.budget_evals)))
            gl = as_sized(gl)
            result = evaluate_candidate(gl, request, req, calibration, process)
            if req.discovery.wing_shape == 'multi_station':
                result['wing_shape'] = 'multi_station' if hasattr(gl.wing, 'chord_factors') else 'baseline'
                if hasattr(gl.wing, 'chord_factors'):
                    result['chord_shape_factors'] = list(gl.wing.chord_factors)
            result['sweep_qc_deg'] = gl.wing.sweep_qc_deg
            if i == 0:
                baseline = result
            records.append(dict(process=process, evaluation=i, result=finite_json(result)))
            if result['valid']:
                key = cell(result)
                if key not in archive or result['objective_value'] > archive[key][0]['objective_value']:
                    archive[key] = (result, gl)
            else:
                rejected.update(filter(None, result['fail_reasons'].split('; ')))
            progress(dict(process=process, evaluated=i+1, budget=req.discovery.budget_evals,
                          occupied_cells=len(archive)))
        elites = []
        for key, (result, gl) in sorted(archive.items()):
            from cem.core import digest
            parameters = dict(wing=asdict(gl.wing), fuselage=asdict(gl.fuse), tail=asdict(gl.tail))
            revision = digest(dict(process=process, parameters=parameters, placement=placement(gl)))[:16]
            value = finite_json(dict(elite_id=revision, process=process, cell=list(key), result=result,
                parameters=parameters, placement=placement(gl),
                verification_state='screened_existing_layout', novelty_state='not_claimed',
                independent_recheck=dict(state='not_evaluated', missing=['XFOIL station polars', 'AVL trim and modes'])))
            elites.append(value)
        elites.sort(key=lambda x: (-x['result']['objective_value'], x['elite_id']))
        per_process[process] = dict(best=elites[0] if elites else None, archive=elites,
            rejections=dict(sorted(rejected.items())), baseline=finite_json(baseline),
            reason_if_no_elite=None if elites else 'No design met the declared physics/model/process envelope')
        candidates.extend(elites[:req.discovery.top_k])
    selected = ALL_FAMILIES if req.discovery.families == 'all' else req.discovery.families
    return dict(status='completed', per_process=per_process, candidates=candidates, evaluations=records,
        search='seeded MAP-Elites over conventional/T/V layouts; bounded chord-shape and local resampling, no Opti refinement',
        capabilities=dict(supported_families=list(SUPPORTED_FAMILIES),
            unavailable_families=[f for f in selected if f not in SUPPORTED_FAMILIES],
            novel_shape_discovery=False, picogk_builder=False,
            parametric_wing_shape_discovery=True,
            advanced_environments=False, physical_calibration=calibration['state']))
