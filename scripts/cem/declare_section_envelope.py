"""Declare required section windows before native experiments; never mission admission."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT), str(ROOT/'backend')]
from cem.core import canonical_json, digest
from cem.glider.families.installed import freeze, validate
from cem.glider.families.coordinate_polars import coordinates_payload

BOUNDS = {
    'e974ec3cea': ([49000., 74000.], [3., 4.5, 6.]),
    'd8e3b2bda8': ([49000., 56000., 64000.], [2.5+i*.375 for i in range(9)]),
    'e9426ae7ef': ([33000., 73000.], [4., 6., 8.]),
    'f925aa8eb7': ([33000., 61000.], [5., 6.5, 8.]),
}


def declare():
    historical = json.loads((ROOT/'docs/testing/cem-coordinate-window-results.json').read_text())
    states, foils = [], {}
    for state in historical['installed_states']:
        installed = freeze(state['archive'], state['controls'])
        current = {digest(coordinates_payload(s.airfoil.coordinates)): coordinates_payload(s.airfoil.coordinates)
                   for wing in validate(installed).wings for s in wing.xsecs}
        foils.update(current)
        matches = installed['installed_sha256'] == state['installed_sha256']
        if not matches:
            raise ValueError('Historical installed-state identity changed')
        states.append(dict(family=state['family'], archive=state['archive'], controls=state['controls'],
            installed=installed, foils=list(current), historical_installed_sha256=state['installed_sha256'],
            identity_match=matches))
    return dict(scope='section_numerical_development_only',
        source_base='50279906d857bfbb6a362ab78e984cd5e9f581a6',
        requirements=json.loads((ROOT/'docs/testing/cem-family-development-envelope.json').read_text()),
        states=states, foils=foils,
        required_banks=[dict(foil=key, reynolds=BOUNDS[key[:10]][0], angles=BOUNDS[key[:10]][1]) for key in foils],
        derivation='Retain swept accepted solver-request windows and unresolved neutral/deflected canard/tandem initialization windows from coordinate trials; no reduction of bounds. These windows are necessary development tests, not the full 15 m/s mission envelope.',
        swept_solver_conditions=dict(speed_m_s=6., alpha_deg=4., altitude_m=0., spanwise=4),
        low_re_diagnostics=dict(reynolds=33000., neutral_angles=[4., 6., 8.], deflected_angles=[5., 6.5, 8.],
            panels=[160, 240], sequences=['ascending', 'ascending_repeat', 'zero_centered', 'reset_each']),
        tolerances=dict(cl=.025, cd=.001, cm=.005),
        limits=dict(timeout_s=20, file_bytes=1048576, audit_artifacts=150),
        production_admission=False, full_mission_qualification=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    declaration = declare()
    with args.output.open('x') as stream:
        stream.write(canonical_json(declaration))
    print('Six installed identities matched; four exact foils declared.')


if __name__ == '__main__':
    main()
