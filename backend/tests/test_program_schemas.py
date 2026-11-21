"""Committed F02 JSON Schemas and registry export must equal what the models generate."""
from __future__ import annotations

import json

from engineering.program import PROGRAM_IR_VERSION, schema_export as se


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


def test_registry_export_lists_fourteen_operations():
    data = json.loads((se.SCHEMA_DIR / se.REGISTRY_EXPORT_NAME).read_text(encoding='utf-8'))
    ops = data['operations']
    assert len(ops) == 14
    executable = {o['id'] for o in ops if o['executable']}
    assert executable == {'capability.build', 'capability.inspect', 'capability.analyze', 'capability.export'}
    assert all(o['id'].startswith('construct.') for o in ops if not o['executable'])


def test_files_have_no_cr_bytes_and_end_with_newline():
    for name in se.generated():
        raw = (se.SCHEMA_DIR / name).read_bytes()
        assert b'\r' not in raw
        assert raw.endswith(b'\n') and not raw.endswith(b'\n\n')


def test_import_does_not_write_and_main_check_reports_ok():
    assert se.main(['--check']) == 0
