"""Dynamics and control qualification attempt for the actual Task C families.

For every family this script, in order:

1. writes the envelope declaration (speeds, alpha/beta, Reynolds, atmosphere,
   configuration revision and mass digest, trim control, mission stability
   limits, absent authority requirement) to disk *before* any evaluation;
2. records the qualification-input inventory (mass/CG/full tensor, physical
   control topology, aerodynamic domain, ledger state, missing inputs);
3. evaluates every declared point and independently replays the report.

When ``--section-audits`` names a directory written by
``assess_family_physics.py``, its passed/replayed coordinate audits supply the
tailless +3-degree bank for an additional declared envelope inside the bank's
native domain. The bank is rebuilt only from complete passed audits whose
evidence digests and recorded replays match.

It also recomputes the published 747 reference (equations check). No aircraft
is qualified by running this; missing inputs are reported, never defaulted.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT / 'backend')]

import numpy as np

from cem.core import canonical_json, digest
from cem.glider.families import archive, build
from cem.glider.families.coordinate_polars import CoordinateBank
from cem.glider.families.physics import (assessment, dynamics, envelope, fixtures, qualification, stability,
                                         trim)

NEUTRAL = dict(tailless_swept=dict(elevon_pitch=0., elevon_roll=0.),
               canard=dict(foreplane_elevator=0., aileron=0.),
               tandem=dict(front_elevator=0., rear_elevator=0., aileron=0.))
PITCH = dict(tailless_swept='elevon_pitch', canard='foreplane_elevator', tandem='front_elevator')
OPERATING_POINT = dict(speed_m_s=6., alpha_deg=4., beta_deg=0., pitch_deg=0., bank_deg=0.)


def reference_747():
    r = fixtures.published_747()
    eq = trim.verify_equilibrium(revision_sha256=r['model'].revision, required=('reference_aero',),
        model=r['model'], mass=r['mass'], flight_state=r['flight_state'], controls={}, reference_span_m=59.64,
        reference_chord_m=8.32, gravity_m_s2=r['g_m_s2'], source='published reference')
    out = dynamics.linearize(eq, model=r['model'], mass=r['mass'], required=('reference_aero',),
        control_limits=[], reference_span_m=59.64, reference_chord_m=8.32, unsteady=r['unsteady'])
    A = np.asarray(out['A'])
    lon = A[np.ix_([0, 2, 4, 7], [0, 2, 4, 7])]
    modes = {m['mode']: dict(real=m['real_per_s'], imag=m['imag_rad_s'], damping_ratio=m['damping_ratio'],
                             natural_frequency=m['natural_frequency_rad_s']) for m in out['modes']}
    worst = float(np.max(np.abs(lon-np.asarray(r['published']['A_longitudinal']))))
    errors = {name: abs(modes[name]['real']-r['published'][name]['real']) for name in
              ('short_period', 'phugoid', 'dutch_roll', 'roll', 'spiral')}
    return dict(source=fixtures.B747['source'], computed_modes=modes,
                published=r['published'], max_A_longitudinal_abs_error=worst, max_real_part_abs_error=errors,
                within_printed_precision=worst <= 5.1e-5 and max(errors.values()) <= 5.1e-5,
                unsteady=out['unsteady_aerodynamics'], decoupled=out['decoupled'])


def load_bank(directory):
    audits = json.loads((directory/'audits.json').read_text())
    declaration = json.loads((directory/'declaration.json').read_text())
    if not all(a['state'] == 'passed' and a['replay'] == 'matched' for a in audits.values()):
        return None, None, declaration, audits
    reports = []
    for name, record in audits.items():
        report = json.loads((directory/name/'coordinate-audit.json').read_text())
        if report['evidence_sha256'] != record['evidence_sha256'] or report['state'] != 'passed':
            raise ValueError('Section audit differs from its recorded passed/replayed evidence')
        reports += [q['report'] for q in report['queries'] if q['directory'].endswith('/ascending')]
    bank = CoordinateBank(reports)
    evidence = dict(state='passed_and_replayed', replay='matched', report_sha256=[digest(r) for r in reports],
                    audit_evidence_sha256=[a['evidence_sha256'] for a in audits.values()])
    return bank, evidence, declaration, audits


def run_envelope(root, label, family, record, controls, decl_fields, inputs):
    provider = {'baseline': lambda: assessment.envelope_provider(record, controls=controls, **inputs)}
    prepared = provider['baseline']()
    declaration = envelope.EnvelopeDeclaration.model_validate_json(canonical_json(dict(decl_fields,
        name=f'{family} {label}', family=family,
        configurations=[dict(name='baseline', revision_sha256=prepared['revision'],
            mass_properties_sha256=prepared['mass']['mass_properties_sha256'],
            description=f'archive {record["aircraft_revision"][:12]}, controls {canonical_json(controls)}')],
        stability_requirement=stability.mission_limits_requirement().model_dump(mode='json'))))
    path = root/f'{family}-{label}-declaration.json'
    path.write_text(canonical_json(declaration.model_dump(mode='json')))  # declared before evaluation
    report = envelope.run(declaration, provider, declared_file=path)
    replay = envelope.replay(report, provider)
    (root/f'{family}-{label}-envelope.json').write_text(canonical_json(report))
    return dict(declaration_sha256=report['declaration_sha256'], state=report['state'], summary=report['summary'],
        blockers=report['blockers'], numerically_qualified=report['numerically_qualified'],
        replay='matched' if replay is False else 'differs', report_sha256=report['report_sha256'],
        trim_unsupported=sorted({u['name'] for p in report['points'] for u in (p['trim'].get('unsupported') or [])}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--section-audits', type=Path)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True, exist_ok=False)
    summary = dict(reference_747=reference_747(), implementation=envelope.implementation_identity(),
                   runtime=envelope.runtime_identity(), families={})
    print('747 reference within printed precision:', summary['reference_747']['within_printed_precision'], flush=True)
    for family in sorted(NEUTRAL):
        record = archive(build(family))
        controls = NEUTRAL[family]
        inputs_record = qualification.inventory(record, controls=controls, operating_point=OPERATING_POINT)
        (root/f'{family}-inputs.json').write_text(canonical_json(inputs_record))
        fields = dict(purpose='Steady 1 g glide envelope of the default screening archive with neutral fixed '
            'lateral controls, declared before evaluation; mission stability limits; no authority requirement '
            'exists, so none is supplied.', speeds_m_s=[5., 6., 7., 8.], alpha_bounds_deg=[-2., 8.],
            reynolds_bounds=[3e4, 1.5e5], altitude_m=0., trim_control=PITCH[family],
            fixed_controls_deg={k: v for k, v in controls.items() if k != PITCH[family]})
        result = dict(inputs=dict(record_sha256=inputs_record['record_sha256'],
            missing=sorted(inputs_record['missing']), equilibrium=inputs_record['equilibrium']['state'],
            mass=inputs_record['mass_properties'], topology=dict(
                channels=[(c['name'], c['role']) for c in inputs_record['control_topology']['channels']],
                exclusive_segments=sum(len(set(s['hinge_chord_fractions'].values())) > 1
                                       for s in inputs_record['control_topology']['segments']),
                shared_segments=sum(len(s['mixing']) > 1 for s in inputs_record['control_topology']['segments']))),
            envelopes=dict(neutral=run_envelope(root, 'neutral', family, record, controls, fields, {})))
        summary['families'][family] = result
        print(family, result['envelopes']['neutral']['state'], result['envelopes']['neutral']['trim_unsupported'],
              flush=True)
    if args.section_audits is not None:
        bank, evidence, declared, audits = load_bank(args.section_audits.resolve())
        family = 'tailless_swept'
        record, controls = declared['archive'], declared['controls']
        inputs = {} if bank is None else dict(bank=bank, bank_evidence=evidence, spanwise=4)
        inputs_record = qualification.inventory(record, controls=controls, operating_point=OPERATING_POINT,
                                                **inputs)
        (root/f'{family}-banked-inputs.json').write_text(canonical_json(inputs_record))
        fields = dict(purpose='Steady 1 g glide inside the passed/replayed section-bank domain of the archived '
            '+3-degree pitch-channel state (frozen controls, no trim control because the bank covers only '
            'this installed state); declared before evaluation.', speeds_m_s=[5.5, 6., 6.5],
            alpha_bounds_deg=[2.5, 5.5], reynolds_bounds=[4.9e4, 7.4e4], altitude_m=0., trim_control=None,
            fixed_controls_deg=controls)
        banked = run_envelope(root, 'banked', family, record, controls, fields, inputs)
        summary['families'][family]['banked'] = dict(audits=audits, bank='passed_and_replayed' if bank else None,
            lifting_only=inputs_record['equilibrium']['lifting_only'], missing=sorted(inputs_record['missing']),
            aerodynamic_domain=inputs_record['aerodynamic_domain'], envelope=banked)
        print(family, 'banked', banked['state'], banked['trim_unsupported'], flush=True)
    (root/'summary.json').write_text(canonical_json(summary))
    print(json.dumps({f: {k: v['state'] for k, v in r['envelopes'].items()} for f, r in summary['families'].items()}),
          flush=True)


if __name__ == '__main__':
    main()
