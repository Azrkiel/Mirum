"""Spec v2 contract (D1): models, cross-field rules, parse_any, v1 upgrade, registry, presets.

Pure Python: pydantic only, no CAD or provider imports.
"""
import ast
import copy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.dragsters import hardware, profiles
from backend.dragsters.spec import DragsterSpec
from backend.dragsters.spec_v2 import (
    SPEC_V2_ADAPTER, NarrowWaistV2, TeardropV2, WedgeV2, canonical_spec_v2, parse_any, parse_v2,
    specification_digest_v2, upgrade_v1,
)
from backend.dragsters.styles import REGISTRY, StyleDefinition, load_preset, resolve_build

REPO = Path(__file__).parents[2]
PACKAGE = REPO / 'backend/dragsters'
V1_EXAMPLE = PACKAGE / 'examples/wedge.json'
STYLES = ('wedge', 'teardrop', 'narrow_waist')
MODELS = {'wedge': WedgeV2, 'teardrop': TeardropV2, 'narrow_waist': NarrowWaistV2}

COMMON_BOUNDS = {
    'length_mm': (160, 300), 'ground_clearance_mm': (1, 10),
    'front_axle_x_mm': (10, 290), 'rear_axle_x_mm': (10, 290), 'axle_bore_diameter_mm': (2, 5),
    'pocket_diameter_mm': (12, 24), 'pocket_depth_mm': (30, 80), 'pocket_center_z_mm': (10, 60),
    'min_wall_mm': (2, 5),
    'wheels.front_diameter_mm': (16, 45), 'wheels.rear_diameter_mm': (16, 45),
    'wheels.width_mm': (2, 12), 'wheels.side_clearance_mm': (0.5, 5),
    'stock.length_mm': (160, 320), 'stock.width_mm': (30, 80), 'stock.height_mm': (25, 80),
    'material.density_g_cm3': (0.05, 2.0),
}
STYLE_BOUNDS = {
    'wedge': {'width_mm': (32, 60), 'height_mm': (28, 45), 'nose_height_mm': (8, 18)},
    'teardrop': {
        'nose_width_mm': (8, 30), 'nose_height_mm': (6, 25), 'max_width_mm': (32, 64),
        'max_height_mm': (26, 55), 'max_section_x_frac': (0.25, 0.6), 'tail_width_mm': (26, 64),
        'tail_height_mm': (24, 55), 'side_height_frac': (0.3, 0.8), 'crown_exponent': (2.0, 4.0),
    },
    'narrow_waist': {
        'nose_width_mm': (8, 30), 'nose_height_mm': (6, 25), 'front_section_x_frac': (0.10, 0.30),
        'front_width_mm': (24, 56), 'waist_x_frac': (0.35, 0.60), 'waist_width_mm': (14, 40),
        'body_height_mm': (18, 40), 'housing_width_mm': (30, 64), 'housing_height_mm': (28, 60),
        'housing_length_mm': (40, 110), 'side_height_frac': (0.3, 0.8), 'crown_exponent': (2.0, 4.0),
    },
}
COMMON_KEYS = ['schema_version', 'style', 'units', 'length_mm', 'ground_clearance_mm', 'front_axle_x_mm',
               'rear_axle_x_mm', 'axle_bore_diameter_mm', 'pocket_diameter_mm', 'pocket_depth_mm',
               'pocket_center_z_mm', 'min_wall_mm', 'wheels', 'stock', 'material',
               'wheels.front_diameter_mm', 'wheels.rear_diameter_mm', 'wheels.width_mm',
               'wheels.side_clearance_mm']
STOCK = {'length_mm': 305.0, 'width_mm': 70.0, 'height_mm': 60.0}
MATERIAL = {'name': 'Basswood', 'density_g_cm3': 0.42}


def preset(style):
    return json.loads((PACKAGE / f'presets/{style}.json').read_text())


def full(style):
    """Preset with stock + material populated so their nested fields can be exercised."""
    return preset(style) | {'stock': dict(STOCK), 'material': dict(MATERIAL)}


def patched(data, changes):
    data = copy.deepcopy(data)
    for key, value in changes.items():
        target = data
        *parents, leaf = key.split('.')
        for parent in parents:
            target = target[parent]
        if value is _DELETE:
            del target[leaf]
        else:
            target[leaf] = value
    return data


_DELETE = object()


def validate(data):
    return SPEC_V2_ADAPTER.validate_json(json.dumps(data))


# --- presets / canonical form --------------------------------------------------------------

@pytest.mark.parametrize('style', STYLES)
def test_presets_validate_and_roundtrip(style):
    spec = validate(preset(style))
    assert isinstance(spec, MODELS[style])
    assert spec.style == style and spec.schema_version == 2
    encoded = canonical_spec_v2(spec)
    again = parse_v2(encoded)
    assert canonical_spec_v2(again) == encoded
    assert specification_digest_v2(spec) == hashlib.sha256(encoded.encode()).hexdigest()
    assert specification_digest_v2(again) == specification_digest_v2(spec)
    assert load_preset(style) == spec
    with pytest.raises(ValidationError):
        spec.length_mm = 250


@pytest.mark.parametrize('style', STYLES)
def test_presets_with_stock_and_material_validate(style):
    spec = validate(full(style))
    assert spec.stock.length_mm == 305.0 and spec.material.name == 'Basswood'


def test_canonical_rule_matches_v1():
    spec = load_preset('teardrop')
    expected = json.dumps(spec.model_dump(mode='json'), sort_keys=True, separators=(',', ':'), allow_nan=False)
    assert canonical_spec_v2(spec) == expected


def test_candidate_preset_values_unchanged():
    teardrop = preset('teardrop')
    assert patched(teardrop, {}) == teardrop
    assert {k: teardrop[k] for k in ('length_mm', 'ground_clearance_mm', 'front_axle_x_mm', 'rear_axle_x_mm',
                                     'axle_bore_diameter_mm', 'pocket_diameter_mm', 'pocket_depth_mm',
                                     'pocket_center_z_mm', 'min_wall_mm', 'nose_width_mm', 'nose_height_mm',
                                     'max_width_mm', 'max_height_mm', 'max_section_x_frac', 'tail_width_mm',
                                     'tail_height_mm', 'side_height_frac', 'crown_exponent')} == {
        'length_mm': 220, 'ground_clearance_mm': 3, 'front_axle_x_mm': 40, 'rear_axle_x_mm': 185,
        'axle_bore_diameter_mm': 3, 'pocket_diameter_mm': 20, 'pocket_depth_mm': 52, 'pocket_center_z_mm': 30.5,
        'min_wall_mm': 2, 'nose_width_mm': 16, 'nose_height_mm': 14, 'max_width_mm': 56, 'max_height_mm': 50,
        'max_section_x_frac': 0.35, 'tail_width_mm': 44, 'tail_height_mm': 46, 'side_height_frac': 0.5,
        'crown_exponent': 3.0}
    narrow = preset('narrow_waist')
    assert {k: narrow[k] for k in ('length_mm', 'front_axle_x_mm', 'rear_axle_x_mm', 'pocket_depth_mm',
                                   'pocket_center_z_mm', 'nose_width_mm', 'nose_height_mm', 'front_section_x_frac',
                                   'front_width_mm', 'waist_x_frac', 'waist_width_mm', 'body_height_mm',
                                   'housing_width_mm', 'housing_height_mm', 'housing_length_mm',
                                   'side_height_frac', 'crown_exponent')} == {
        'length_mm': 230, 'front_axle_x_mm': 40, 'rear_axle_x_mm': 195, 'pocket_depth_mm': 60,
        'pocket_center_z_mm': 31, 'nose_width_mm': 14, 'nose_height_mm': 12, 'front_section_x_frac': 0.15,
        'front_width_mm': 40, 'waist_x_frac': 0.45, 'waist_width_mm': 22, 'body_height_mm': 26,
        'housing_width_mm': 44, 'housing_height_mm': 46, 'housing_length_mm': 80, 'side_height_frac': 0.7,
        'crown_exponent': 3.0}
    for data in (teardrop, narrow):
        assert data['wheels'] == {'front_diameter_mm': 30, 'rear_diameter_mm': 34, 'width_mm': 6,
                                  'side_clearance_mm': 1.5}
        assert data['stock'] is None and data['material'] is None


def test_derived_axle_heights():
    spec = load_preset('teardrop')
    assert spec.front_axle_z_mm == 15 and spec.rear_axle_z_mm == 17
    assert 'front_axle_z_mm' not in spec.model_dump()


# --- field-level rejection -----------------------------------------------------------------

BAD_VALUES = lambda lo, hi: (lo - 0.01, hi + 0.01, True, '12', None, float('nan'), float('inf'), -float('inf'))
FIELD_CASES = [
    (style, field, value)
    for style in STYLES
    for field, (lo, hi) in (COMMON_BOUNDS | STYLE_BOUNDS[style]).items()
    for value in BAD_VALUES(lo, hi)
]


@pytest.mark.parametrize('style,field,value', FIELD_CASES)
def test_bad_field_values(style, field, value):
    with pytest.raises(ValidationError, match=field.split('.')[-1]):
        validate(patched(full(style), {field: value}))


@pytest.mark.parametrize('style,field', [(s, f) for s in STYLES
                                         for f in COMMON_KEYS + list(STYLE_BOUNDS[s])
                                         + ['stock.length_mm', 'stock.width_mm', 'stock.height_mm',
                                            'material.name', 'material.density_g_cm3']])
def test_missing_field(style, field):
    with pytest.raises(ValidationError, match=field.split('.')[-1]):
        validate(patched(full(style), {field: _DELETE}))


@pytest.mark.parametrize('style', STYLES)
@pytest.mark.parametrize('field,value', [
    ('unknown', 1), ('wheels.unknown', 1), ('stock.unknown', 1), ('material.unknown', 'x'),
    ('schema_version', True), ('schema_version', 1), ('schema_version', 2.0), ('schema_version', '2'),
    ('units', 'inch'), ('style', 'blob'), ('wheels', None), ('wheels', 30), ('stock', 'pine'),
    ('material', 0.42), ('material.name', ''), ('material.name', 'x' * 41), ('material.name', 'bad/name'),
    ('material.name', 5), ('material.name', None), ('front_axle_z_mm', 15.0),
])
def test_bad_structure(style, field, value):
    with pytest.raises(ValidationError, match=field.split('.')[-1] if field != 'style' else 'style'):
        validate(patched(full(style), {field: value}))


def test_style_fields_do_not_cross():
    data = preset('teardrop') | {'style': 'wedge'}
    with pytest.raises(ValidationError, match='Extra inputs'):
        validate(data)


@pytest.mark.parametrize('name', ['Basswood', 'pine-1', 'Balsa_wood 2', 'x' * 40])
def test_material_names_accepted(name):
    assert validate(patched(full('wedge'), {'material.name': name})).material.name == name


# --- relationship rules --------------------------------------------------------------------

COMMON_RELATIONSHIPS = {
    'wedge': [
        ({'front_axle_x_mm': 176}, 'front_axle_x_mm must precede rear_axle_x_mm'),
        ({'rear_axle_x_mm': 50}, 'wheels.front_diameter_mm/wheels.rear_diameter_mm'),
        ({'rear_axle_x_mm': 199}, 'rear_axle_x_mm/axle_bore_diameter_mm must clear length_mm'),
        ({'min_wall_mm': 5}, 'wheels.front_diameter_mm/axle_bore_diameter_mm must clear ground_clearance_mm'),
        ({'pocket_center_z_mm': 14}, 'pocket_center_z_mm/pocket_diameter_mm must clear ground_clearance_mm'),
        ({'wheels.rear_diameter_mm': 20}, 'rear_axle_x_mm/wheels.rear_diameter_mm/axle_bore_diameter_mm must clear the pocket'),
    ],
    'teardrop': [
        ({'front_axle_x_mm': 190}, 'front_axle_x_mm must precede rear_axle_x_mm'),
        ({'rear_axle_x_mm': 70}, 'wheels.front_diameter_mm/wheels.rear_diameter_mm'),
        ({'rear_axle_x_mm': 219}, 'rear_axle_x_mm/axle_bore_diameter_mm must clear length_mm'),
        ({'min_wall_mm': 5, 'wheels.front_diameter_mm': 16},
         'wheels.front_diameter_mm/axle_bore_diameter_mm must clear ground_clearance_mm'),
        ({'pocket_center_z_mm': 14}, 'pocket_center_z_mm/pocket_diameter_mm must clear ground_clearance_mm'),
        ({'wheels.rear_diameter_mm': 36}, 'rear_axle_x_mm/wheels.rear_diameter_mm/axle_bore_diameter_mm must clear the pocket'),
    ],
    'narrow_waist': [
        ({'front_axle_x_mm': 200}, 'front_axle_x_mm must precede rear_axle_x_mm'),
        ({'rear_axle_x_mm': 60}, 'wheels.front_diameter_mm/wheels.rear_diameter_mm'),
        ({'rear_axle_x_mm': 229}, 'rear_axle_x_mm/axle_bore_diameter_mm must clear length_mm'),
        ({'min_wall_mm': 5, 'wheels.front_diameter_mm': 16},
         'wheels.front_diameter_mm/axle_bore_diameter_mm must clear ground_clearance_mm'),
        ({'pocket_center_z_mm': 14}, 'pocket_center_z_mm/pocket_diameter_mm must clear ground_clearance_mm'),
        ({'wheels.rear_diameter_mm': 38}, 'rear_axle_x_mm/wheels.rear_diameter_mm/axle_bore_diameter_mm must clear the pocket'),
    ],
}
STYLE_RELATIONSHIPS = {
    'wedge': [
        ({'length_mm': 160, 'pocket_depth_mm': 80, 'rear_axle_x_mm': 140}, 'pocket_depth_mm must remain'),
        ({'width_mm': 32, 'pocket_diameter_mm': 24, 'min_wall_mm': 4.5, 'pocket_center_z_mm': 27},
         'width_mm must contain pocket_diameter_mm'),
        ({'pocket_center_z_mm': 24}, 'exceeds height_mm'),
        ({'wheels.front_diameter_mm': 40}, 'front_axle_x_mm/wheels.front_diameter_mm/axle_bore_diameter_mm must clear wedge top'),
    ],
    'teardrop': [
        ({'tail_width_mm': 60}, 'tail_width_mm must not exceed max_width_mm'),
        ({'tail_height_mm': 52}, 'tail_height_mm must not exceed max_height_mm'),
        ({'pocket_center_z_mm': 36}, 'pocket_center_z_mm/pocket_diameter_mm/pocket_depth_mm with min_wall_mm must lie inside'),
        ({'side_height_frac': 0.3}, 'front_axle_x_mm/wheels.front_diameter_mm/axle_bore_diameter_mm with min_wall_mm must stay below the side height'),
    ],
    'narrow_waist': [
        ({'waist_x_frac': 0.6, 'housing_length_mm': 100}, 'waist_x_frac'),
        ({'waist_width_mm': 40}, 'waist_width_mm must be narrower than front_width_mm'),
        ({'waist_width_mm': 36, 'front_width_mm': 50, 'housing_width_mm': 34},
         'waist_width_mm must be narrower than housing_width_mm'),
        ({'nose_width_mm': 30, 'front_width_mm': 24}, 'nose_width_mm must not exceed front_width_mm'),
        ({'nose_height_mm': 25, 'body_height_mm': 24}, 'nose_height_mm must not exceed body_height_mm'),
        ({'housing_height_mm': 29}, 'housing_height_mm must be at least body_height_mm + 4'),
        ({'pocket_depth_mm': 80}, 'pocket_depth_mm must fit inside housing_length_mm'),
        ({'pocket_center_z_mm': 37}, 'pocket_center_z_mm/pocket_diameter_mm/pocket_depth_mm with min_wall_mm must lie inside'),
        ({'side_height_frac': 0.5}, 'front_axle_x_mm/wheels.front_diameter_mm/axle_bore_diameter_mm with min_wall_mm must stay below the side height'),
    ],
}
RELATIONSHIP_CASES = [(style, changes, message)
                      for table in (COMMON_RELATIONSHIPS, STYLE_RELATIONSHIPS)
                      for style, cases in table.items()
                      for changes, message in cases]


@pytest.mark.parametrize('style,changes,message', RELATIONSHIP_CASES)
def test_relationship_rules(style, changes, message):
    data = patched(preset(style), changes)
    with pytest.raises(ValidationError) as caught:
        validate(data)
    errors = caught.value.errors()
    assert len(errors) == 1 and errors[0]['type'] == 'value_error', errors
    assert message in str(caught.value)


@pytest.mark.parametrize('style', ['teardrop', 'narrow_waist'])
def test_bore_pocket_separation_either_disjunct(style):
    # rear axle sits under the pocket (axially overlapping) but vertically clear: accepted
    spec = load_preset(style)
    zp, rp = spec.pocket_center_z_mm, spec.pocket_diameter_mm / 2
    za, ra, t = spec.rear_axle_z_mm, spec.axle_bore_diameter_mm / 2, spec.min_wall_mm
    assert spec.rear_axle_x_mm + ra + t > spec.length_mm - spec.pocket_depth_mm
    assert za + ra + t <= zp - rp
    # axially clear but vertically overlapping: accepted (shorter pocket)
    data = patched(preset(style), {'pocket_depth_mm': 30, 'pocket_center_z_mm': 24})
    model = validate(data)
    assert model.rear_axle_x_mm + ra + t <= model.length_mm - model.pocket_depth_mm
    assert model.rear_axle_z_mm + ra + t > model.pocket_center_z_mm - rp


def test_lofted_containment_matches_profiles():
    """The pocket rule is exactly disk_margin >= 0 sampled over [L-D, L]."""
    spec = load_preset('teardrop')
    n, g = spec.crown_exponent, spec.ground_clearance_mm
    rp, t, zp = spec.pocket_diameter_mm / 2, spec.min_wall_mm, spec.pocket_center_z_mm
    stations = profiles.teardrop_stations(spec)
    worst = min(profiles.disk_margin(*profiles.interpolate(stations, x), n, 0, zp - g, rp + t)
                for x in profiles.sample_xs(spec.length_mm - spec.pocket_depth_mm, spec.length_mm, 0.5))
    assert worst >= 0
    # raising the pocket by slightly more than the slack must fail
    with pytest.raises(ValidationError, match='must lie inside'):
        validate(patched(preset('teardrop'), {'pocket_center_z_mm': zp + worst + 0.01}))
    validate(patched(preset('teardrop'), {'pocket_center_z_mm': zp + worst - 0.01}))


# --- parse_any ------------------------------------------------------------------------------

def test_parse_any_dispatches():
    assert isinstance(parse_any(V1_EXAMPLE.read_text()), DragsterSpec)
    for style in STYLES:
        assert isinstance(parse_any(json.dumps(preset(style))), MODELS[style])


@pytest.mark.parametrize('version', ['"2"', '2.0', '3', '0', 'true', 'null', '"1"', '1.0'])
def test_parse_any_rejects_bad_versions(version):
    data = json.dumps(preset('teardrop') | {'schema_version': 0}).replace('"schema_version": 0',
                                                                          f'"schema_version": {version}')
    with pytest.raises(ValueError, match='schema_version'):
        parse_any(data)


@pytest.mark.parametrize('text', [
    json.dumps({k: v for k, v in preset('teardrop').items() if k != 'schema_version'}),
    '[]', '2', 'not json', '{"schema_version": 2, "style": "teardrop", "length_mm": NaN}', '',
])
def test_parse_any_rejects_garbage(text):
    with pytest.raises(ValueError):
        parse_any(text)


def test_parse_any_v1_version_rejects_v2_body():
    with pytest.raises(ValueError):
        parse_any(json.dumps(preset('teardrop') | {'schema_version': 1}))
    with pytest.raises(ValueError):
        parse_any(json.dumps(json.loads(V1_EXAMPLE.read_text()) | {'schema_version': 2}))


# --- upgrade_v1 -----------------------------------------------------------------------------

def test_upgrade_v1_example():
    v1 = DragsterSpec.model_validate_json(V1_EXAMPLE.read_text())
    v2, provenance = upgrade_v1(v1)
    assert isinstance(v2, WedgeV2)
    for field in ('length_mm', 'width_mm', 'height_mm', 'nose_height_mm', 'ground_clearance_mm', 'front_axle_x_mm',
                  'rear_axle_x_mm', 'axle_bore_diameter_mm', 'pocket_diameter_mm', 'pocket_depth_mm',
                  'pocket_center_z_mm', 'min_wall_mm'):
        assert getattr(v2, field) == getattr(v1, field)
        assert provenance[field] == 'migrated_from_v1'
    assert v2.wheels.front_diameter_mm == v2.wheels.rear_diameter_mm == 2 * v1.axle_z_mm
    assert v2.front_axle_z_mm == v2.rear_axle_z_mm == v1.axle_z_mm
    assert (v2.wheels.width_mm, v2.wheels.side_clearance_mm) == (6.0, 1.5)
    assert v2.stock is None and v2.material is None
    assert provenance['wheels.front_diameter_mm'] == provenance['wheels.rear_diameter_mm'] == 'derived_from_v1_axle_z'
    assert provenance['wheels.width_mm'] == provenance['wheels.side_clearance_mm'] == 'preset:wedge'
    assert v2 == load_preset('wedge')


@pytest.mark.parametrize('changes', [
    {'ground_clearance_mm': 0, 'height_mm': 34, 'pocket_center_z_mm': 22, 'axle_z_mm': 8.5},  # v2 needs g >= 1
    {'axle_z_mm': 7.5},  # wheel diameter 15 < 16
])
def test_upgrade_v1_out_of_v2_range(changes):
    v1 = DragsterSpec.model_validate_json(json.dumps(json.loads(V1_EXAMPLE.read_text()) | changes))
    with pytest.raises(ValueError, match='still generates as v1'):
        upgrade_v1(v1)


def test_upgrade_v1_requires_v1_model():
    with pytest.raises(TypeError):
        upgrade_v1(load_preset('wedge'))


# --- style registry + hardware stub --------------------------------------------------------

def test_registry_entries():
    assert set(REGISTRY) == set(STYLES)
    assert REGISTRY['wedge'] == StyleDefinition('wedge', 'Low wedge', 'dragster_wedge_v2',
                                                'dragsters.styles.wedge:build', 'presets/wedge.json')
    assert REGISTRY['teardrop'] == StyleDefinition('teardrop', 'Smooth teardrop', 'dragster_teardrop_v2',
                                                   'dragsters.styles.teardrop:build', 'presets/teardrop.json')
    assert REGISTRY['narrow_waist'] == StyleDefinition(
        'narrow_waist', 'Narrow waist + raised rear housing', 'dragster_narrow_waist_v2',
        'dragsters.styles.narrow_waist:build', 'presets/narrow_waist.json')


@pytest.mark.parametrize('style', STYLES)
def test_resolve_build_returns_callable(style):
    assert callable(resolve_build(style))


# Styles still stubbed; each builder workstream drops its style here (wedge landed in D2, teardrop in D3).
@pytest.mark.parametrize('style', ['narrow_waist'])
def test_resolve_build_returns_stub(style):
    build = resolve_build(style)
    with pytest.raises(NotImplementedError, match='builder lands in D'):
        build(load_preset(style))


def test_resolve_unknown_style():
    with pytest.raises(KeyError):
        resolve_build('blob')
    with pytest.raises(KeyError):
        load_preset('blob')


def test_hardware_stub_contract():
    spec = load_preset('teardrop')
    assert hardware.wheel_envelopes(spec) == []
    assert hardware.cad_checks(object(), spec) == {}
    assert hardware.summary(spec, 1.0, None) == {
        'hardware': {}, 'mass': {'value_g': None, 'basis': 'body_only', 'reason': 'hardware checks not implemented'}}


def test_v2_modules_import_without_cad():
    code = (
        "import sys\n"
        "for name in ('cadquery', 'OCP', 'numpy', 'trimesh', 'google.generativeai'):\n"
        "    sys.modules[name] = None\n"
        "import backend.dragsters, backend.dragsters.profiles, backend.dragsters.spec_v2\n"
        "import backend.dragsters.styles, backend.dragsters.hardware\n"
        "from backend.dragsters.styles import load_preset, resolve_build\n"
        "for style in ('wedge', 'teardrop', 'narrow_waist'):\n"
        "    load_preset(style)\n"
        "    resolve_build(style)\n"
        "bad = [m for m in sys.modules if m.split('.')[0] in ('cadquery', 'OCP', 'numpy', 'trimesh', 'agents')\n"
        "       and sys.modules[m] is not None]\n"
        "assert not bad, bad\n"
        "print('ok')\n"
    )
    result = subprocess.run([sys.executable, '-c', code], cwd=REPO, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'ok'


def test_package_init_stays_import_free():
    tree = ast.parse((PACKAGE / '__init__.py').read_text())
    assert len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)  # docstring only
