"""Frozen JSON contract; no CAD or provider imports required."""
import hashlib
import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from backend.dragsters.spec import DragsterSpec, canonical_spec, specification_digest

EXAMPLE = Path(__file__).parents[1] / 'dragsters/examples/wedge.json'
BOUNDS = {
    'length_mm': (160, 260), 'width_mm': (32, 60), 'height_mm': (28, 45),
    'nose_height_mm': (8, 18), 'ground_clearance_mm': (0, 10),
    'front_axle_x_mm': (10, 250), 'rear_axle_x_mm': (10, 250),
    'axle_z_mm': (4, 24), 'axle_bore_diameter_mm': (2, 5),
    'pocket_diameter_mm': (12, 24), 'pocket_depth_mm': (30, 80),
    'pocket_center_z_mm': (15, 45), 'min_wall_mm': (2, 5),
}


def example():
    return json.loads(EXAMPLE.read_text())


def test_example_roundtrip():
    spec = DragsterSpec.model_validate_json(EXAMPLE.read_text())
    encoded = canonical_spec(spec)
    assert canonical_spec(DragsterSpec.model_validate_json(encoded)) == encoded
    assert specification_digest(spec) == hashlib.sha256(encoded.encode()).hexdigest()
    with pytest.raises(ValidationError):
        spec.width_mm = 50


@pytest.mark.parametrize('field', ['schema_version', 'style', 'units', *BOUNDS])
def test_missing(field):
    data = example()
    del data[field]
    with pytest.raises(ValidationError, match=field):
        DragsterSpec.model_validate_json(json.dumps(data))


@pytest.mark.parametrize('field,value', [
    ('schema_version', True), ('schema_version', 2), ('schema_version', 1.0),
    ('style', 'teardrop'), ('units', 'inch'), ('unknown', 1),
    *[(f, v) for f, (lo, hi) in BOUNDS.items()
      for v in (lo - 0.01, hi + 0.01, True, '12', None, float('nan'), float('inf'), -float('inf'))],
])
def test_bad_fields(field, value):
    data = example()
    data[field] = value
    with pytest.raises(ValidationError, match=field):
        DragsterSpec.model_validate_json(json.dumps(data))


@pytest.mark.parametrize('changes,field', [
    ({'front_axle_x_mm': 180}, 'front_axle_x_mm'),
    ({'rear_axle_x_mm': 44}, 'rear_axle_x_mm'),
    ({'length_mm': 160, 'pocket_depth_mm': 80}, 'pocket_depth_mm'),
    ({'width_mm': 32, 'pocket_diameter_mm': 24, 'min_wall_mm': 5}, 'width_mm'),
    ({'pocket_center_z_mm': 15}, 'pocket_center_z_mm'),
    ({'pocket_center_z_mm': 40}, 'pocket_center_z_mm'),
    ({'axle_z_mm': 4}, 'axle_z_mm'),
    ({'axle_z_mm': 12}, 'axle_z_mm'),
    ({'rear_axle_x_mm': 199}, 'rear_axle_x_mm'),
    ({'front_axle_x_mm': 10, 'nose_height_mm': 8, 'length_mm': 260, 'axle_bore_diameter_mm': 5, 'pocket_diameter_mm': 18}, 'front_axle_x_mm'),
])
def test_relationships(changes, field):
    data = example() | changes
    with pytest.raises(ValidationError, match=field):
        DragsterSpec.model_validate_json(json.dumps(data))
