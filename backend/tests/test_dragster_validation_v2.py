"""Verification v2 (D5): real OpenCASCADE, STEP round trips, negative mutants.

Nominal preset bodies come from the real registered style builders (``resolve_build``) with the
real D6 hardware checks. Mutants (deliberately wrong pockets/bores) are built here from the outer
body via ``kernel`` / ``profiles``. ``validation_v2`` itself never imports ``kernel`` or a builder.
The hardware-merge tests monkeypatch ``validation_v2.hardware``.
"""
import ast
import json
import math
from functools import lru_cache
from pathlib import Path

import cadquery as cq
import pytest

from backend.dragsters import profiles, validation_v2 as v2
from backend.dragsters.kernel import loft_body
from backend.dragsters.spec_v2 import canonical_spec_v2, specification_digest_v2
from backend.dragsters.styles import load_preset, resolve_build

MODULE = Path(v2.__file__)
STYLES = ('wedge', 'teardrop', 'narrow_waist')
GENERIC_IDS = {'single_valid_solid', 'absolute_bounds', 'symmetry', 'reference_volume', 'pocket_void',
               'front_axle_void', 'rear_axle_void', 'pocket_wall', 'front_bore_wall', 'rear_bore_wall',
               'spec_revalidation'}
V1_KEYS = {'schema_version', 'spec_sha256', 'step_sha256', 'generator_version', 'cadquery_version',
           'solid_count', 'bounds_mm', 'volume_mm3', 'checks', 'passed', 'engineering_certified'}
# Ids D6's real hardware.cad_checks may legitimately add (the D1 stub adds none).
HARDWARE_IDS = {'wheel_clearance_front', 'wheel_clearance_rear', 'body_above_ground', 'stock_containment'}
REPORT_V2_KEYS = {'style', 'hardware', 'mass', 'method_limits'}


# ---------------------------------------------------------------------------- body construction

def _ymax(spec):
    return max(profiles.half_width_at(spec, x) for x in profiles.sample_xs(0.0, spec.length_mm, 1.0))


@lru_cache(maxsize=None)
def _uncut(style):
    """Outer body of a preset (no pocket, no bores), cached per style."""
    spec = load_preset(style)
    g, L = spec.ground_clearance_mm, spec.length_mm
    if style == 'wedge':
        W, H, N = spec.width_mm, spec.height_mm, spec.nose_height_mm
        points = [(0, -W / 2, g), (L, -W / 2, g), (L, -W / 2, g + H),
                  (L / 2, -W / 2, g + H), (0, -W / 2, g + N), (0, -W / 2, g)]
        wire = cq.Wire.makePolygon([cq.Vector(*p) for p in points])
        return cq.Solid.extrudeLinear(wire, [], cq.Vector(0, W, 0))
    return loft_body(profiles.stations_for(spec), spec.crown_exponent, g, L)


def nominal_body(style):
    """The preset body from the real registered style builder (D2 wedge, D3 teardrop, D4 narrow waist)."""
    spec = load_preset(style)
    return spec, resolve_build(style)(spec)


def mutant_body(style, *, pocket=True, pocket_z=None, pocket_y=0.0, pocket_depth=None,
                front_z=None, rear_z=None, rear=True):
    """Deliberately wrong numbers, bypassing spec validation (verified against the right spec)."""
    spec = load_preset(style)
    L, ya = spec.length_mm, _ymax(spec) + 1
    ra, rp = spec.axle_bore_diameter_mm / 2, spec.pocket_diameter_mm / 2
    tools = []
    if pocket:
        D = spec.pocket_depth_mm if pocket_depth is None else pocket_depth
        z = spec.pocket_center_z_mm if pocket_z is None else pocket_z
        tools.append(cq.Solid.makeCylinder(rp, D + 1, cq.Vector(L - D, pocket_y, z), cq.Vector(1, 0, 0)))
    bore_specs = [(spec.front_axle_x_mm, spec.front_axle_z_mm if front_z is None else front_z)]
    if rear:
        bore_specs.append((spec.rear_axle_x_mm, spec.rear_axle_z_mm if rear_z is None else rear_z))
    for x, z in bore_specs:
        tools.append(cq.Solid.makeCylinder(ra, 2 * ya, cq.Vector(x, -ya, z), cq.Vector(0, 1, 0)))
    return spec, _uncut(style).cut(*tools)


def export(shape, path):
    cq.exporters.export(shape, str(path), exportType='STEP')
    return path


@pytest.fixture(scope='module')
def preset_reports(tmp_path_factory):
    """{style: (spec, report)} for the three nominal presets (verified once per module)."""
    out = {}
    for style in STYLES:
        spec, body = nominal_body(style)
        path = export(body, tmp_path_factory.mktemp(style) / 'body.step')
        out[style] = (spec, v2.verify_step_v2(path, spec), path)
    return out


def failing_ids(style, tmp_path, spec, body):
    path = export(body, tmp_path / 'mutant.step')
    with pytest.raises(ValueError) as caught:
        v2.verify_step_v2(path, spec)
    message = str(caught.value)
    assert message.startswith('STEP checks failed: ')
    return set(message.removeprefix('STEP checks failed: ').split(', '))


# ---------------------------------------------------------------------------- positive

@pytest.mark.parametrize('style', STYLES)
def test_preset_passes_every_check(style, preset_reports):
    spec, report, _ = preset_reports[style]
    checks = report['checks']
    assert GENERIC_IDS <= set(checks)
    assert all(result['passed'] is True for result in checks.values()), {
        k: r for k, r in checks.items() if not r['passed']}
    assert report['passed'] is True and report['engineering_certified'] is False
    assert report['schema_version'] == 2 and report['style'] == style
    assert report['solid_count'] == 1
    assert report['spec_sha256'] == specification_digest_v2(spec)


@pytest.mark.parametrize('style', STYLES)
def test_report_keys_are_v1_plus_v2_and_json_safe(style, preset_reports):
    spec, report, _ = preset_reports[style]
    assert V1_KEYS <= set(report) and REPORT_V2_KEYS <= set(report)
    assert set(report['bounds_mm']) == {'xmin', 'xmax', 'ymin', 'ymax', 'zmin', 'zmax'}
    assert report['generator_version'] == f'dragster_{style}_v2'
    assert isinstance(report['hardware'], dict) and isinstance(report['mass'], dict)
    text = json.dumps(report, allow_nan=False, sort_keys=True)
    assert json.loads(text) == report


@pytest.mark.parametrize('style', STYLES)
def test_every_check_records_measured_value_and_threshold(style, preset_reports):
    _, report, _ = preset_reports[style]
    for check_id in GENERIC_IDS - {'single_valid_solid', 'spec_revalidation'}:
        evidence = report['checks'][check_id]
        assert 'measured' in evidence and 'threshold' in evidence, check_id
    for check_id in ('pocket_wall', 'front_bore_wall', 'rear_bore_wall'):
        text = report['checks'][check_id]['method_limits']
        assert 'sampled nowhere' in text and 'excludes rear face' in text
        assert 'does not certify whole-body thickness away from the pocket and bores' in text
    assert report['method_limits']  # non-empty mapping


def test_provenance_is_included_only_when_given(preset_reports, tmp_path):
    spec, report, path = preset_reports['wedge']
    assert 'provenance' not in report
    given = v2.verify_step_v2(path, spec, provenance={'length_mm': 'supplied'})
    assert given['provenance'] == {'length_mm': 'supplied'}


def test_report_binds_the_inspected_file(preset_reports):
    import hashlib
    _, report, path = preset_reports['teardrop']
    assert report['step_sha256'] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_wedge_volume_and_bounds_match_v1_style_analytic_values(preset_reports):
    spec, report, _ = preset_reports['wedge']
    L, W, H, N = spec.length_mm, spec.width_mm, spec.height_mm, spec.nose_height_mm
    b = report['bounds_mm']
    got = (b['xmin'], b['xmax'], b['ymin'], b['ymax'], b['zmin'], b['zmax'])
    want = (0, L, -W / 2, W / 2, spec.ground_clearance_mm, spec.ground_clearance_mm + H)
    assert max(abs(x - y) for x, y in zip(got, want)) <= 0.01
    assert report['checks']['reference_volume']['threshold'] == pytest.approx(max(0.1, 1e-6 * report['volume_mm3']),
                                                                              rel=1e-3)


def test_wall_distance_evidence_matches_independent_expectation(preset_reports):
    # Wedge: the pocket axis is 12 mm below the flat top and 10 mm radius -> top wall 2 mm.
    spec, report, _ = preset_reports['wedge']
    top_wall = spec.ground_clearance_mm + spec.height_mm - spec.pocket_center_z_mm - spec.pocket_diameter_mm / 2
    measured = report['checks']['pocket_wall']['measured']
    assert measured['min_wall_mm'] <= top_wall + 0.011
    assert measured['min_wall_mm'] >= spec.min_wall_mm - 0.01


# ---------------------------------------------------------------------------- negative mutants

def _t(spec):
    return spec.min_wall_mm


def _pocket_raised(style):
    spec = load_preset(style)
    L, D, g = spec.length_mm, spec.pocket_depth_mm, spec.ground_clearance_mm
    top = min(g + profiles.top_at(spec, x, 0.0) for x in profiles.sample_xs(L - D, L, 0.5))
    wall0 = top - (spec.pocket_center_z_mm + spec.pocket_diameter_mm / 2)
    return dict(pocket_z=spec.pocket_center_z_mm + wall0 - _t(spec) / 2)


def _bore_low(style):
    spec = load_preset(style)
    return dict(front_z=spec.ground_clearance_mm + spec.axle_bore_diameter_mm / 2 + _t(spec) / 2)


MUTANTS = {
    # name: (builder kwargs factory, must fail (all styles), additionally for wedge, allowed superset)
    'pocket_raised': (_pocket_raised, {'pocket_wall'}, set(), {'pocket_wall', 'pocket_void'}),
    'pocket_deeper': (lambda s: dict(pocket_depth=load_preset(s).pocket_depth_mm + 5.0),
                      {'pocket_wall'}, {'reference_volume'},
                      {'pocket_wall', 'reference_volume', 'pocket_void'}),
    'pocket_missing': (lambda s: dict(pocket=False), {'pocket_void', 'reference_volume'}, set(),
                       {'pocket_void', 'reference_volume', 'pocket_wall', 'rear_bore_wall'}),
    'rear_bore_missing': (lambda s: dict(rear=False), {'rear_axle_void'}, {'reference_volume'},
                          {'rear_axle_void', 'reference_volume', 'rear_bore_wall'}),
    'rear_bore_into_pocket': (lambda s: dict(rear_z=load_preset(s).rear_axle_z_mm + 1.5),
                              {'rear_bore_wall'}, set(),
                              {'rear_bore_wall', 'rear_axle_void', 'pocket_wall'}),
    'front_bore_low': (_bore_low, {'front_bore_wall'}, set(), {'front_bore_wall', 'front_axle_void'}),
    'equal_volume_pocket_moved': (lambda s: dict(pocket_y=-2.0), {'pocket_void', 'pocket_wall'}, set(),
                                  {'pocket_void', 'pocket_wall', 'front_bore_wall', 'rear_bore_wall', 'symmetry'}),
}


@pytest.mark.parametrize('style', STYLES)
@pytest.mark.parametrize('name', list(MUTANTS))
def test_mutant_is_rejected_with_the_expected_ids(style, name, tmp_path):
    factory, must, must_wedge, allowed = MUTANTS[name]
    spec, body = mutant_body(style, **factory(style))
    failed = failing_ids(style, tmp_path, spec, body)
    required = must | (must_wedge if style == 'wedge' else set())
    if name == 'equal_volume_pocket_moved':           # plan: pocket_void OR pocket_wall
        assert failed & {'pocket_void', 'pocket_wall'}
    else:
        assert required <= failed, (required - failed, failed)
    assert failed <= allowed | required | HARDWARE_IDS, failed - allowed - HARDWARE_IDS


@pytest.mark.parametrize('style', STYLES)
def test_equal_volume_pocket_mutant_really_has_equal_volume(style):
    spec, nominal = nominal_body(style)
    _, moved = mutant_body(style, pocket_y=-2.0)
    assert moved.Volume() == pytest.approx(nominal.Volume(), rel=1e-6)


@pytest.mark.parametrize('style', STYLES)
def test_body_shifted_in_y_fails_symmetry_and_bounds(style, tmp_path):
    spec, body = nominal_body(style)
    failed = failing_ids(style, tmp_path, spec, body.translate(cq.Vector(0, 0.5, 0)))
    assert {'symmetry', 'absolute_bounds'} <= failed
    assert failed <= {'symmetry', 'absolute_bounds', 'pocket_void', 'pocket_wall', 'front_bore_wall',
                      'rear_bore_wall'} | HARDWARE_IDS    # a 0.5 mm shift can break 1.5 mm side clearance


@pytest.mark.parametrize('style', STYLES)
def test_body_scaled_in_x_fails_bounds_and_volume(style, tmp_path):
    spec, body = nominal_body(style)
    scaled = body.transformGeometry(cq.Matrix([[1.03, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0]]))
    failed = failing_ids(style, tmp_path, spec, scaled)
    assert {'absolute_bounds', 'reference_volume'} <= failed
    assert 'single_valid_solid' not in failed and 'symmetry' not in failed


@pytest.mark.parametrize('style', STYLES)
def test_extra_disconnected_cube_fails_single_valid_solid_and_stops(style, tmp_path):
    spec, body = nominal_body(style)
    cube = cq.Solid.makeBox(1, 1, 1, cq.Vector(-20, 0, 0))
    path = export(cq.Compound.makeCompound([body, cube]), tmp_path / 'two.step')
    with pytest.raises(ValueError, match=r'^STEP checks failed: single_valid_solid$'):
        v2.verify_step_v2(path, spec)


def test_missing_or_corrupt_step_raises_reimport_error(tmp_path):
    spec = load_preset('wedge')
    with pytest.raises(ValueError, match='STEP reimport failed'):
        v2.verify_step_v2(tmp_path / 'nope.step', spec)
    bad = tmp_path / 'bad.step'
    bad.write_text('not a step file')
    with pytest.raises(ValueError, match='STEP reimport failed|single_valid_solid'):
        v2.verify_step_v2(bad, spec)


def test_spec_revalidation_catches_an_invalid_spec_instance(preset_reports):
    spec, _, path = preset_reports['wedge']
    bad = spec.model_construct(**{**dict(spec), 'min_wall_mm': 1.0})   # below the v2 range (>= 2)
    with pytest.raises(ValueError, match='spec_revalidation'):
        v2.verify_step_v2(path, bad)


# ---------------------------------------------------------------------------- hardware merge

class _FakeHardware:
    def __init__(self, checks=None, summary=None, boom=False):
        self._checks, self._boom = checks if checks is not None else {}, boom
        self._summary = summary or {'hardware': {'track_width_front_mm': 1.0},
                                    'mass': {'value_g': None, 'basis': 'body_only', 'reason': 'x'}}
        self.calls = []

    def cad_checks(self, body, spec):
        self.calls.append(('cad', spec.style, body.isValid()))
        if self._boom:
            raise RuntimeError('boom')
        return self._checks

    def summary(self, spec, volume_mm3, bounds):
        self.calls.append(('summary', volume_mm3, sorted(bounds)))
        return self._summary


def test_hardware_checks_and_summary_are_merged(monkeypatch, preset_reports):
    spec, _, path = preset_reports['wedge']
    fake = _FakeHardware({'wheel_clearance_front': {'passed': True, 'distance_mm': 1.5}})
    monkeypatch.setattr(v2, 'hardware', fake)
    report = v2.verify_step_v2(path, spec)
    assert report['checks']['wheel_clearance_front'] == {'passed': True, 'distance_mm': 1.5}
    assert report['hardware'] == {'track_width_front_mm': 1.0}
    assert report['mass']['basis'] == 'body_only'
    assert ('cad', 'wedge', True) in fake.calls
    summary_call = next(c for c in fake.calls if c[0] == 'summary')
    assert summary_call[1] == pytest.approx(report['volume_mm3'])
    assert summary_call[2] == ['xmax', 'xmin', 'ymax', 'ymin', 'zmax', 'zmin']


@pytest.mark.parametrize('bad', [
    {'wheel_clearance_front': {'passed': False, 'distance_mm': 0.1}},
    {'wheel_clearance_front': {'distance_mm': 1.5}},                  # no verdict
    {'wheel_clearance_front': {'passed': 'yes'}},                     # not a bool
    {'wheel_clearance_front': True},                                  # not a mapping
    {'single_valid_solid': {'passed': True}},                         # cannot shadow a generic id
], ids=['failed', 'no-verdict', 'non-bool', 'non-mapping', 'shadow'])
def test_failed_or_malformed_hardware_result_fails_closed(monkeypatch, preset_reports, bad):
    spec, _, path = preset_reports['wedge']
    monkeypatch.setattr(v2, 'hardware', _FakeHardware(bad))
    with pytest.raises(ValueError, match='^STEP checks failed: ') as caught:
        v2.verify_step_v2(path, spec)
    assert next(iter(bad)) in str(caught.value) or 'hardware' in str(caught.value)


def test_hardware_exception_fails_closed(monkeypatch, preset_reports):
    spec, _, path = preset_reports['wedge']
    monkeypatch.setattr(v2, 'hardware', _FakeHardware(boom=True))
    with pytest.raises(ValueError, match='hardware_checks'):
        v2.verify_step_v2(path, spec)


def test_hardware_summary_exception_fails_closed(monkeypatch, preset_reports):
    spec, _, path = preset_reports['wedge']
    fake = _FakeHardware()
    fake.summary = lambda *a: (_ for _ in ()).throw(RuntimeError('boom'))
    monkeypatch.setattr(v2, 'hardware', fake)
    with pytest.raises(ValueError, match='hardware_summary'):
        v2.verify_step_v2(path, spec)


# ---------------------------------------------------------------------------- face helpers

def _face_infos(shape):
    return v2.face_infos(shape)


def _synthetic():
    """Box 100 x 40 x 40 (x 0..100, y +-20, z 0..40) with a rear-open pocket and a Y bore."""
    box = cq.Solid.makeBox(100, 40, 40, cq.Vector(0, -20, 0))
    pocket = cq.Solid.makeCylinder(8, 31, cq.Vector(70, 0, 20), cq.Vector(1, 0, 0))
    bore = cq.Solid.makeCylinder(2, 42, cq.Vector(30, -21, 12), cq.Vector(0, 1, 0))
    return box.cut(pocket, bore)


def test_pocket_face_classifier_accepts_cylinder_and_bottom_disk_only():
    infos = _face_infos(_synthetic())
    pocket = [i for i in infos if v2.is_pocket_face(i, L=100, D=30, rp=8, zp=20)]
    kinds = sorted(i['kind'] for i in pocket)
    assert kinds == ['cylinder', 'plane']
    assert not any(v2.is_pocket_face(i, L=100, D=30, rp=8, zp=21.0) for i in infos)     # axis off by 1
    assert not any(v2.is_pocket_face(i, L=100, D=30, rp=8.01, zp=20) for i in infos)    # wrong radius
    assert not any(v2.is_pocket_face(i, L=100, D=29, rp=8, zp=20) for i in infos if i['kind'] == 'plane')  # disk at wrong depth


def test_pocket_disk_must_lie_within_the_pocket_radius():
    # The box's x=70 plane does not exist, but a big planar face at x=L-D must not count.
    box = cq.Solid.makeBox(100, 40, 40, cq.Vector(0, -20, 0))
    infos = _face_infos(box)
    assert not any(v2.is_pocket_face(i, L=100, D=100, rp=8, zp=20) for i in infos)  # nose face at x=0


def test_bore_face_classifier_matches_axis_radius_and_direction():
    infos = _face_infos(_synthetic())
    bore = [i for i in infos if v2.is_bore_face(i, x=30, z=12, ra=2)]
    assert bore and all(i['kind'] == 'cylinder' for i in bore)
    assert not any(v2.is_bore_face(i, x=30, z=12.01, ra=2) for i in infos)
    assert not any(v2.is_bore_face(i, x=30, z=12, ra=2.01) for i in infos)
    assert not any(v2.is_bore_face(i, x=70, z=20, ra=8) for i in infos)       # pocket is along X, not Y


def test_rear_face_classifier_requires_outward_plus_x_plane_at_length():
    infos = _face_infos(_synthetic())
    rear = [i for i in infos if v2.is_rear_face(i, L=100)]
    assert len(rear) == 1 and rear[0]['kind'] == 'plane'
    assert not any(v2.is_rear_face(i, L=99.0) for i in _face_infos(_synthetic()))
    nose = [i for i in infos if i['kind'] == 'plane' and abs(i['plane_x']) < 1e-6]
    assert nose and not any(v2.is_rear_face(i, L=0.0) for i in nose)                 # -X normal at x=0


# ---------------------------------------------------------------------------- import boundary

def _imports(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [(0, alias.name, ()) for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            found.append((node.level, node.module or '', tuple(a.name for a in node.names)))
    return found


def test_validation_v2_never_imports_builders():
    forbidden_modules = {'kernel', 'geometry', 'validation'}
    for level, module, names in _imports(MODULE):
        parts = module.split('.')
        flat = {module, *(f'{module}.{n}' if module else n for n in names)}
        for item in flat:
            segments = item.split('.')
            assert not (set(segments) & forbidden_modules), item
            # the styles registry (``from .styles import REGISTRY``) is allowed; builders are not
            if 'styles' in segments:
                assert item in {'styles', 'styles.REGISTRY'}, item
        assert not module.startswith(('backend.dragsters.kernel', 'backend.dragsters.geometry',
                                      'dragsters.kernel', 'dragsters.geometry')), module
        assert not (level and parts[0] in {'kernel', 'geometry'})


def test_validation_v2_imports_only_allowed_modules():
    allowed_roots = {'__future__', 'hashlib', 'json', 'math', 'pathlib', 'typing', 'cadquery', 'OCP',
                     'spec_v2', 'profiles', 'tolerances', 'hardware', 'styles', 'time', 'dataclasses'}
    for level, module, names in _imports(MODULE):
        roots = [module.split('.')[0]] if module else [n.split('.')[0] for n in names]
        assert all(root in allowed_roots for root in roots), (level, module, names)


def test_importing_validation_v2_does_not_load_the_kernel():
    import subprocess
    import sys
    repo = Path(__file__).parents[2]
    code = ('import sys; import backend.dragsters.validation_v2; '
            'bad = [m for m in sys.modules if m.endswith(("dragsters.kernel", "dragsters.geometry")) '
            'or ".styles." in m]; assert not bad, bad')
    subprocess.run([sys.executable, '-c', code], check=True, cwd=repo)
