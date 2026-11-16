"""Reproducible native family development runs; no production admission.

Search samples actual geometry with a fixed body/equipment/material inventory.
Comparison reconstructs and solves both arms at fixed controls and conditions.
No L/D or performance gain can be derived from this inviscid equilibrium model.
"""
from copy import deepcopy
from pathlib import Path
import random

from cem.core import canonical_json
from . import archive, build, export_screening, reconstruct
from .diagnostics import Conditions, diagnose

SCOPE = 'native_inviscid_family_development'


def _conditions(conditions):
    return conditions if isinstance(conditions, Conditions) else Conditions.model_validate(conditions or {})


def _controls(record, controls):
    from .installed import freeze
    a = reconstruct(record)
    states = {c.name: 0. for c in a.controls} if controls is None else dict(controls)
    freeze(record, states)  # reject missing, nonfinite and combined saturation
    return states


def search(record, *, budget=8, seed=42, conditions=None, controls=None):
    base = reconstruct(record)
    if type(budget) is not int or not 1 <= budget <= 24:
        raise ValueError('Native development budget must be 1..24')
    if type(seed) is not int or not 0 <= seed <= 2**32-1:
        raise ValueError('Seed must be an unsigned 32-bit integer')
    c = _conditions(conditions)
    states = _controls(record, controls)
    rng = random.Random(seed)
    key = {'tailless_swept': 'wing', 'canard': 'main_wing', 'tandem': 'rear_wing'}[base.family]
    evaluations = []
    for i in range(budget):
        params = deepcopy(base.parameters)
        if i:
            params[key]['incidence_deg'] = rng.uniform(-2., 6.)
            params[key]['washout_deg'] = rng.uniform(0., 4.)
        geometry = archive(build(base.family, params))
        report = diagnose(geometry, c, control_deflections_deg=states)
        evaluations.append(dict(geometry=geometry, report=report))
    return dict(scope=SCOPE, base=deepcopy(record), budget=budget, seed=seed,
        conditions=c.model_dump(mode='json'), controls=states, evaluations=evaluations,
        diagnostic_candidates=[i for i, e in enumerate(evaluations)
            if e['report']['state'] == 'diagnostic_equilibrium'],
        production_search=False, independent_numerical_performance=False,
        physical_construction=False, verified_gain=False)


def compare(left, right, *, conditions=None, controls=None):
    a, b = (reconstruct(r) for r in (left, right))
    if a.family != b.family or a.parameters['inventory'] != b.parameters['inventory']:
        raise ValueError('Native comparison requires same family and fixed inventory')
    # Hinge and control topology are fixed hardware choices, not search knobs.
    from dataclasses import asdict
    if [asdict(c) for c in a.controls] != [asdict(c) for c in b.controls]:
        raise ValueError('Native comparison requires identical control hardware')
    c = _conditions(conditions)
    states = _controls(left, controls)
    _controls(right, states)
    arms = [diagnose(r, c, control_deflections_deg=states) for r in (left, right)]
    return dict(scope=SCOPE, geometry=[deepcopy(left), deepcopy(right)],
        conditions=c.model_dump(mode='json'), controls=states, arms=arms,
        state='diagnostic_pair' if all(r['state'] == 'diagnostic_equilibrium' for r in arms)
              else 'inconclusive', performance_delta=None, verified_gain=False,
        independent_numerical_performance=False, physical_construction=False)


def replay(report):
    """Parent-side native recomputation; a self-consistent hash is insufficient."""
    from . import control_response
    if report.get('scope') == control_response.SCOPE:
        return control_response.replay(report)
    if report.get('scope') != SCOPE:
        raise ValueError('Unknown native family report scope')
    if 'evaluations' in report:
        expected = search(report['base'], budget=report['budget'], seed=report['seed'],
            conditions=report['conditions'], controls=report['controls'])
    else:
        expected = compare(*report['geometry'], conditions=report['conditions'], controls=report['controls'])
    if canonical_json(report) != canonical_json(expected):
        raise ValueError('Native family report differs from reconstructed calculation')
    return False  # replay validates development evidence, never production admission


def export(record, directory, *, conditions=None, controls=None):
    """Fresh lifting OML and exact controlled-state meshes with diagnostic report."""
    from .installed import freeze, validate
    from cem.evidence import file_digest
    from gliders.model.export import write_stl
    path = Path(directory)
    if path.exists() and any(path.iterdir()):
        raise ValueError('Native family export requires an empty directory')
    c = _conditions(conditions)
    states = _controls(record, controls)
    installed = freeze(record, states)
    # Calculate before creating a bundle so a solver failure cannot leave an
    # apparently complete package. A rejected diagnostic remains downloadable
    # development evidence and is explicitly labelled in its report.
    report = diagnose(record, c, control_deflections_deg=states)
    export_screening(record, path)
    parts = []
    for i, wing in enumerate(validate(installed).wings):
        points, faces = wing.mesh_body(method='tri')
        target = path/f'controlled-{i}.stl'
        write_stl(str(target), points, faces)
        parts.append(dict(file=target.name, sha256=file_digest(target)))
    manifest = dict(scope=SCOPE, units='mm', installed=installed, report=report, controlled_parts=parts,
        geometry=deepcopy(record), manufacturing_ready=False, physical_construction=False,
        independent_numerical_performance=False, verified_gain=False)
    (path/'native-development.json').write_text(canonical_json(manifest))
    return manifest


def validate_export(directory):
    """Rebuild both geometry and evidence, not merely caller-provided hashes."""
    import json
    import tempfile
    from cem.evidence import file_digest
    path = Path(directory)
    if any(p.is_symlink() or not p.is_file() for p in path.iterdir()):
        raise ValueError('Unexpected native export entry')
    manifest = json.loads((path/'native-development.json').read_text())
    report = manifest['report']
    states = manifest['installed']['payload']['control_deflections_deg']
    with tempfile.TemporaryDirectory(prefix='mirum-family-replay-') as tmp:
        expected = export(manifest['geometry'], tmp, conditions=report['conditions'], controls=states)
        if canonical_json(manifest) != canonical_json(expected):
            raise ValueError('Native export evidence differs from reconstruction')
        expected_files = {p.name: file_digest(p) for p in Path(tmp).iterdir()}
        if {p.name: file_digest(p) for p in path.iterdir()} != expected_files:
            raise ValueError('Native export artifacts differ from reconstruction')
    return False  # faithful diagnostic bundle, not a qualified design


def main():
    import argparse
    import json
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='operation', required=True)
    p = sub.add_parser('control-response')
    p.add_argument('archive', type=Path)
    p.add_argument('--conditions', type=Path)
    p.add_argument('--output', type=Path, required=True)
    for name in ('search', 'compare', 'export'):
        p = sub.add_parser(name)
        p.add_argument('archive', type=Path)
        p.add_argument('--conditions', type=Path)
        p.add_argument('--controls', type=Path)
        p.add_argument('--output', type=Path, required=True)
        if name == 'compare':
            p.add_argument('right', type=Path)
        if name == 'search':
            p.add_argument('--budget', type=int, default=8)
            p.add_argument('--seed', type=int, default=42)
    args = parser.parse_args()
    load = lambda p: json.loads(p.read_text()) if p else None
    if args.operation == 'control-response':
        from .control_response import sweep
        if args.output.exists():
            parser.error('Output must be a new file')
        result = sweep(load(args.archive), load(args.conditions))
        with args.output.open('x') as f:
            f.write(canonical_json(result))
        return
    options = dict(conditions=load(args.conditions), controls=load(args.controls))
    if args.operation == 'export':
        export(load(args.archive), args.output, **options)
        return
    if args.output.exists():
        parser.error('Output must be a new file')
    result = (search(load(args.archive), budget=args.budget, seed=args.seed, **options)
              if args.operation == 'search' else compare(load(args.archive), load(args.right), **options))
    with args.output.open('x') as f:
        f.write(canonical_json(result))


if __name__ == '__main__':
    main()
