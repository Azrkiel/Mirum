"""Committed F02 JSON Schemas and registry export must equal what the models generate."""
from __future__ import annotations

import hashlib
import json

from engineering.program import PROGRAM_IR_VERSION, schema_export as se
from engineering.program.versions import REGISTRY_VERSION, REGISTRY_VERSION_V0_1


def test_committed_files_equal_generated_byte_for_byte():
    for name, text in se.generated().items():
        path = se.SCHEMA_DIR / name
        assert path.is_file(), f'missing {name}; run python -m engineering.program.schema_export'
        assert path.read_bytes() == text.encode('utf-8'), f'{name} is stale'
    assert se.check() == []


def test_no_extra_json_files():
    names = {p.name for p in se.SCHEMA_DIR.glob('*.json')}
    assert names == set(se.generated())


def test_schema_files_parse_and_titles_match_models():
    for name, model in se.SCHEMA_MODELS.items():
        data = json.loads((se.SCHEMA_DIR / name).read_text(encoding='utf-8'))
        assert data['title'] == model.__name__


def test_program_ir_schema_is_closed_and_pins_version():
    data = json.loads((se.SCHEMA_DIR / 'mirum_program_ir_v0_1.schema.json').read_text(encoding='utf-8'))
    assert data['additionalProperties'] is False
    prop = data['properties']['schema_version']
    assert prop.get('const', None) == PROGRAM_IR_VERSION or prop.get('enum') == [PROGRAM_IR_VERSION]


REGISTRY_V0_1_EXPORT_SHA256 = '0f20ac6e522e485b787ecc2e54feb55def59868ec13ceea574ae1c4d932c54df'


def test_registry_v0_1_export_is_the_pre_f03_bytes():
    raw = (se.SCHEMA_DIR / se.REGISTRY_EXPORT_NAME_V0_1).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == REGISTRY_V0_1_EXPORT_SHA256
    data = json.loads(raw)
    assert data['registry_version'] == REGISTRY_VERSION_V0_1
    ops = data['operations']
    assert len(ops) == 14
    assert all(o['version'] == '0.1.0' for o in ops)
    executable = {o['id'] for o in ops if o['executable']}
    assert executable == {'capability.build', 'capability.inspect', 'capability.analyze', 'capability.export'}
    assert all(o['id'].startswith('construct.') for o in ops if not o['executable'])


def test_registry_v0_2_export_lists_v0_1_operations_plus_regen():
    data = json.loads((se.SCHEMA_DIR / se.REGISTRY_EXPORT_NAME_V0_2).read_text(encoding='utf-8'))
    assert data['registry_version'] == REGISTRY_VERSION
    ops = data['operations']
    v01 = [o for o in ops if o['version'] == '0.1.0']
    regen = [o for o in ops if o['version'] == '0.2.0']
    assert len(v01) == 14
    executable = {o['id'] for o in v01 if o['executable']}
    assert executable == {'capability.build', 'capability.inspect', 'capability.analyze', 'capability.export'}
    assert all(o['id'].startswith('construct.') for o in v01 if not o['executable'])
    assert len(ops) == 17
    assert {o['id'] for o in regen} == {'capability.build', 'capability.inspect', 'capability.export'}
    assert all(o['executable'] and o['capability_versions'] == ['regen_chamber@1.0.0'] for o in regen)


def test_files_have_no_cr_bytes_and_end_with_newline():
    for name in se.generated():
        raw = (se.SCHEMA_DIR / name).read_bytes()
        assert b'\r' not in raw
        assert raw.endswith(b'\n') and not raw.endswith(b'\n\n')


def test_import_does_not_write_and_main_check_reports_ok():
    assert se.main(['--check']) == 0
