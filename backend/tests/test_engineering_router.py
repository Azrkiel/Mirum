"""U7a: provider-free capability router. Rules only; no model, no network."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from dragsters.intent import detect
from engineering.router import ROUTER_VERSION, RouteDecision, route

BACKEND = Path(__file__).resolve().parents[1]
PROMPTS = json.loads((BACKEND / 'eval_harness' / 'prompts.json').read_text(encoding='utf-8'))

# Eval prompt id -> (capability it newly routes to, one-line justification).
# Empty on purpose: no prompt in the set names a registered capability.
# F_nozzle_* are plain De Laval nozzles (no regenerative cooling channels);
# D_closure_ld_glider is a 12 m full-size glider wing with a cruise speed.
ALLOWLIST: dict[str, tuple[str, str]] = {}

POSITIVE = {
    'regen_chamber': [
        'Design a regeneratively cooled rocket thrust chamber with cooling channels',
        'regenerative cooling channels around a rocket engine nozzle',
        'Make a regen cooled thrust chamber and bell nozzle for a small rocket',
        'rocket nozzle with regenerative cooling channels in the wall',
        'A regeneratively-cooled combustion chamber for a liquid rocket engine, 3D printable',
        'Generate a thrust chamber with coolant channels for a rocket engine',
        'regen chamber for a bipropellant rocket, channels along the nozzle',
        'I need a rocket nozzle that is regeneratively cooled with channels in the throat region',
    ],
    'glider_cem': [
        'Design a hand-launched glider that maximizes distance',
        'Optimize a hand toss glider for the longest flight distance',
        'passive glider design for maximum distance in still air',
        'Design a balsa glider launched by hand to maximize range',
        'foam glider with a hand launch, maximize glide distance',
        'best catapult launched glider for distance',
        'Design a hi-start launched glider for maximum duration',
        'drop launched passive glider, maximize distance',
    ],
    'rectangular_cantilever': [
        'Size a rectangular cantilever beam for a 200 N tip force with a 2 mm deflection limit',
        'Rectangular cantilever beam sizing: length 300 mm, width 20 mm, find thickness',
        'Choose the thickness of an aluminum rectangular cantilever beam under a tip load',
        'cantilever beam rectangular cross-section, tip force 100 N, allowable stress 150 MPa',
        'Design a rectangular cantilever beam that deflects less than 1 mm',
        'Size the thickness of a cantilever beam, rectangular section, fixed at one end',
        'What thickness does a rectangular cantilever beam need for a 50 N tip load',
        'rectangular steel cantilever beam sizing with a displacement limit',
    ],
    'rectangular_plate': [
        'Size the thickness of a simply supported rectangular plate under uniform pressure of 0.05 MPa',
        'A clamped rectangular plate, 400 x 300 mm, uniform load, find the thickness for a 1 mm deflection limit',
        'Rectangular plate with fixed edges under distributed pressure: check deflection and stress',
        'What thickness does a simply supported rectangular steel plate need under 5 kPa uniform pressure',
        'Design a flat rectangular plate with clamped edges under uniform pressure, max deflection 2 mm',
        'Size an aluminum rectangular plate, all four edges simply supported, uniformly distributed load',
        'rectangular plate in bending, clamped on all edges, uniform pressure 0.1 MPa, allowable stress 200 MPa',
        'Compute the maximum stress of a rectangular plate with fixed edges under a uniform load',
        'Rectangular panel, called a plate here, simply supported on four edges, uniform pressure, sizing',
    ],
}

NEGATIVE = {
    'regen_chamber': [
        'A garden hose nozzle with a spray pattern selector',
        '3D printer nozzle, 0.4 mm brass, with a heat break',
        'Rocket nozzle (De Laval), throat diameter 30mm, exit diameter 90mm',
        'Regenerative braking module housing for an electric bike',
        'Water-cooled CPU block with cooling channels',
        'A pressure washer nozzle with a quick connect',
        'Cylindrical combustion chamber, 60mm inner radius, 6 fuel inlet ports',
        'A model rocket body tube with fins',
        'A rocket stove with a cooling channel for the handle',
    ],
    'glider_cem': [
        'A hang glider frame',
        'A full-size sailplane fuselage',
        'A paper airplane glider template',
        'A sugar glider cage with a wheel',
        'Design a high-aspect-ratio glider wing, span 12 m, cruise 30 m/s',
        'A porch glider chair for two',
        'Hand-launched glider with a thermal soaring mission',
        'An electric motor powered RC glider with a servo bay',
        'A drawer glider rail for a cabinet',
    ],
    'rectangular_cantilever': [
        'A laser beam alignment bracket',
        'An I-beam bridge, 6 m span',
        'Cantilever snap fit clip for a battery cover',
        'A cantilever roof truss for a stadium',
        'A hollow circular cantilever beam tube',
        'A tapered cantilever beam with a drilled hole pattern',
        'A simply supported beam with a central load',
        'A beam compass for drawing circles',
        'An H-beam column base plate',
    ],
    'rectangular_plate': [
        'An H-beam column base plate',
        'A rectangular mounting plate with 4 holes, simply supported, uniform pressure, find thickness',
        'A ribbed rectangular plate, clamped edges, uniform pressure, size the thickness',
        'A circular plate with clamped edges under uniform pressure, find the thickness',
        'A rectangular plate simply supported under a point load at the center, check deflection',
        'Check the buckling of a rectangular plate under in-plane compression, simply supported',
        'A rectangular plate with one free edge and three clamped edges under uniform pressure, thickness',
        'A rectangular composite laminate plate under uniform pressure, simply supported, size thickness',
        'A rectangular license plate bracket for a car',
        'A plate heat exchanger with rectangular plates under pressure, clamped edges, stress',
        'A rectangular steel armour plate under ballistic impact',
        'A rectangular dinner plate for serving food',
        'Rectangular plate under uniform pressure with large deflection and membrane stress, simply supported',
        'A rectangular plate with a central cutout under uniform pressure, clamped edges, thickness',
        'A rectangular adapter plate flange, bolted, uniform load, clamped edges',
        'A rectangular plate under uniform pressure with a thermal gradient, clamped, stress',
        'Rectangular plate under a dynamic impact load, simply supported, deflection',
        'A triangular plate simply supported under uniform pressure, thickness',
        'A rectangular gusset plate with a patch load, fixed edges, stress',
        'A rectangular sandwich plate, clamped, uniform pressure, deflection',
    ],
}


def test_version_present():
    assert isinstance(ROUTER_VERSION, str) and ROUTER_VERSION


@pytest.mark.parametrize('capability,prompt',
                         [(c, p) for c, ps in POSITIVE.items() for p in ps])
def test_positive(capability, prompt):
    d = route(prompt)
    assert d.capability_id == capability, d
    assert d.assurance == 'checked_design' and d.reason


@pytest.mark.parametrize('capability,prompt',
                         [(c, p) for c, ps in NEGATIVE.items() for p in ps])
def test_negative(capability, prompt):
    d = route(prompt)
    assert d.capability_id != capability, d
    assert d.capability_id is None
    assert d.assurance == 'unverified_geometry' and d.reason


def test_cantilever_beam_stays_beam():
    assert route('Size a rectangular cantilever beam for a 200 N tip force').capability_id == 'rectangular_cantilever'
    assert route('Size a clamped rectangular cantilever beam under a tip load').capability_id == 'rectangular_cantilever'


def test_counts_at_least_eight():
    assert all(len(v) >= 8 for v in POSITIVE.values())
    assert all(len(v) >= 8 for v in NEGATIVE.values())


def test_dragster_delegates_to_detector():
    for p in ('Design a CO2 dragster', 'a co2-powered car body', 'dragster with teardrop shape'):
        assert detect(p).is_dragster
        assert route(p).capability_id == 'co2_dragster'
    for p in ('pinewood derby car', 'full-size dragster', 'a toy car'):
        assert not detect(p).is_dragster
        assert route(p).capability_id is None


def test_two_matches_return_none_with_reason():
    d = route('Design a CO2 dragster and a hand-launched glider that maximizes distance')
    assert d.capability_id is None and d.assurance == 'unverified_geometry'
    assert 'co2_dragster' in d.reason and 'glider_cem' in d.reason
    d = route('Size a rectangular cantilever beam tip force 100 N; also a regeneratively '
              'cooled rocket thrust chamber with cooling channels')
    assert d.capability_id is None and 'ambiguous' in d.reason.lower()


def test_eval_set_routes_as_today():
    assert len(PROMPTS) == 88
    unexpected, seen = [], set()
    for item in PROMPTS:
        today = 'co2_dragster' if detect(item['description']).is_dragster else None
        got = route(item['description']).capability_id
        if item['id'] in ALLOWLIST:
            seen.add(item['id'])
            assert got == ALLOWLIST[item['id']][0], item['id']
        elif got != today:
            unexpected.append((item['id'], today, got))
    assert not unexpected
    assert seen == set(ALLOWLIST)
    assert all(just.strip() for _, just in ALLOWLIST.values())


def test_deterministic():
    prompts = [p for ps in POSITIVE.values() for p in ps] + [i['description'] for i in PROMPTS]
    first = [route(p) for p in prompts]
    assert first == [route(p) for p in prompts]
    assert all(isinstance(d, RouteDecision) for d in first)


def test_returned_ids_are_registered():
    from engineering.capabilities import list_capabilities
    registered = {c.id for c in list_capabilities()}
    assert registered == {'rectangular_cantilever', 'co2_dragster', 'regen_chamber', 'glider_cem', 'rectangular_plate'}
    texts = [p for ps in POSITIVE.values() for p in ps] + [i['description'] for i in PROMPTS]
    texts += ['Design a CO2 dragster']
    ids = {route(t).capability_id for t in texts}
    assert ids - {None} <= registered
    assert ids - {None} == registered


def test_import_hygiene():
    code = (
        "import sys\n"
        "before = set(sys.modules)\n"
        "import engineering.router\n"
        "new = set(sys.modules) - before\n"
        "bad = ('google', 'cadquery', 'OCP', 'numpy', 'scipy', 'aerosandbox', 'fastapi')\n"
        "hit = sorted(m for m in new if m.split('.')[0] in bad)\n"
        "print('HIT', hit)\n"
        "sys.exit(1 if hit else 0)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(BACKEND) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    r = subprocess.run([sys.executable, '-c', code], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
