"""F09 task 1: pure MEL typed-edit module (no provider, no store)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from engineering.language.diagnostics import MelError
from engineering.language.formatter import format_source
from engineering.language.semantic import compile_source
from engineering.program import mel_edit as me

HERE = Path(__file__).parent / 'fixtures' / 'program'
BEAM = (HERE / 'f08' / 'beam.mir').read_text(encoding='utf-8')
BEAM_5_10 = (HERE / 'f08' / 'beam_5_10.mir').read_text(encoding='utf-8')
REPLIES = HERE / 'f09' / 'replies'
SENTINEL = 'SENTINEL-zq7-do-not-echo'


def reply_text(name):
    return (REPLIES / name).read_text(encoding='utf-8')


def parsed(name):
    out = me.parse_reply(reply_text(name))
    assert out.kind == 'valid', out
    return out.reply


def doc(**overrides):
    base = {'schema_version': me.SCHEMA_VERSION, 'state': 'proposed', 'mode': None,
            'candidate_heights_mm': [5, 10], 'reason_code': None}
    base.update(overrides)
    return json.dumps(base)


def test_edit_matches_hand_written_beam():
    edited = me.apply_edit(BEAM_5_10, parsed('edit_5_10_15.json'))
    a, b = compile_source(edited), compile_source(BEAM)
    assert a.semantic_sha256 == b.semantic_sha256
    assert a.brief_sha256 == b.brief_sha256
    head = format_source(BEAM_5_10).splitlines()
    new = edited.splitlines()
    diff = [(x, y) for x, y in zip(head, new) if x != y]
    assert len(head) == len(new) and len(diff) == 1
    assert diff[0][1].strip() == 'candidate_heights = [5 mm, 10 mm, 15 mm];'


def test_mode_only_edit():
    edited = me.apply_edit(BEAM, parsed('edit_mode.json'))
    assert 'mode = "fixed_design";' in edited
    assert 'candidate_heights = [5 mm, 10 mm, 15 mm];' in edited


def test_fractional_height_renders_plain_decimal():
    reply = me.MelEditReply.model_validate(json.loads(doc(candidate_heights_mm=[7.5, 0.00001])))
    edited = me.apply_edit(BEAM, reply)
    assert '[7.5 mm, 0.00001 mm]' in edited


def test_read_edit_target():
    assert me.read_edit_target(BEAM_5_10) == ('bounded_thickness', (5.0, 10.0))
    with pytest.raises(me.MelEditRefused) as info:
        me.read_edit_target(BEAM.replace('  mode = "bounded_thickness";\n', ''))
    assert info.value.reason_code == 'unsupported_edit'
    with pytest.raises(me.MelEditRefused):
        me.read_edit_target('not mel')


def test_no_change_and_unparseable_head():
    with pytest.raises(me.MelEditRefused) as info:
        me.apply_edit(BEAM, me.MelEditReply.model_validate(json.loads(doc(candidate_heights_mm=[5, 10, 15]))))
    assert info.value.reason_code == 'no_change'
    with pytest.raises(me.MelEditRefused) as info:
        me.apply_edit('garbage ' + SENTINEL, parsed('edit_mode.json'))
    assert info.value.reason_code == 'unsupported_edit' and SENTINEL not in str(info.value) + repr(info.value)


def test_refused_reply_cannot_be_applied():
    out = me.parse_reply(reply_text('refuse_requirements.json'))
    with pytest.raises(me.MelEditRefused) as info:
        me.apply_edit(BEAM, out.reply)
    assert info.value.reason_code == 'requirements_change_requested'


def test_compile_fail_fixture_compiles_to_error():
    edited = me.apply_edit(BEAM, parsed('compile_fail.json'))
    with pytest.raises(MelError):
        compile_source(edited)


def test_requirement_key_fixture_and_invalid_fixture():
    assert me.parse_reply(reply_text('requirement_key_edit.json')).kind == 'requirements_change_not_allowed'
    assert me.parse_reply(reply_text('invalid.json')).kind == 'invalid_reply'
    assert parsed('refuse_requirements.json').reason_code == 'requirements_change_requested'


@pytest.mark.parametrize('heights', [[], [1, 2, 3, 4, 5], [5, 5], [-1], [0], [10000.5], [1e999]])
def test_bad_candidates_rejected(heights):
    text = doc(candidate_heights_mm=heights).replace('Infinity', '1e999')
    assert me.parse_reply(text).kind == 'invalid_reply'


def test_nan_and_bad_types_rejected():
    assert me.parse_reply(doc().replace('[5, 10]', '[NaN]')).kind == 'invalid_reply'
    assert me.parse_reply(doc().replace('[5, 10]', '[Infinity]')).kind == 'invalid_reply'
    assert me.parse_reply(doc(candidate_heights_mm=[True])).kind == 'invalid_reply'
    assert me.parse_reply(doc(candidate_heights_mm=['5'])).kind == 'invalid_reply'
    assert me.parse_reply(doc(mode='Bad Mode')).kind == 'invalid_reply'
    assert me.parse_reply(doc(schema_version='other')).kind == 'invalid_reply'
    assert me.parse_reply(doc(candidate_heights_mm=[10000, 0.5])).kind == 'valid'


def test_extra_key_is_requirements_change_not_allowed():
    for key in ('max_tip_deflection', 'tip_force', 'anything'):
        out = me.parse_reply(doc(**{key: 2}))
        assert out.kind == 'requirements_change_not_allowed' and out.reply is None


@pytest.mark.parametrize('text', [
    '{not json', '', '[]', '"x"', 'null',
    '{"state": "proposed", "state": "refused"}',
    '{"a": ' * 3000 + '1' + '}' * 3000,
    '[' * 5000,
    '{"schema_version": "' + 'x' * 5000 + '"}',
    '{"schema_version": "mirum_mel_edit_v0_1", "state": "proposed", "mode": null, '
    '"candidate_heights_mm": [5], "reason_code": null, "extra": {"a": {"b": {"c": {"d": 1}}}}}',
    b'{}', None, 5,
])
def test_invalid_replies(text):
    out = me.parse_reply(text)
    assert out.kind == 'invalid_reply'
    assert out.reply is None


def test_deep_json_with_allowed_keys_invalid():
    text = '{"mode": ' + '[' * 50 + ']' * 50 + '}'
    assert me.parse_reply(text).kind == 'invalid_reply'


def test_duplicate_keys_invalid():
    text = doc().replace('"state": "proposed"', '"state": "proposed", "state": "proposed"')
    assert me.parse_reply(text).kind == 'invalid_reply'


@pytest.mark.parametrize('code', me.MODEL_REASON_CODES)
def test_refused_codes_parse(code):
    out = me.parse_reply(doc(state='refused', candidate_heights_mm=None, reason_code=code))
    assert out.kind == 'valid' and out.reply.reason_code == code


def test_refused_with_server_code_or_payload_invalid():
    for code in ('compile_failed', 'unsupported_edit', 'invalid_reply', 'x'):
        assert me.parse_reply(doc(state='refused', candidate_heights_mm=None, reason_code=code)).kind == 'invalid_reply'
    assert me.parse_reply(doc(state='refused', reason_code='ambiguous')).kind == 'invalid_reply'
    assert me.parse_reply(doc(state='refused', candidate_heights_mm=None, mode='a', reason_code='ambiguous')
                          ).kind == 'invalid_reply'
    assert me.parse_reply(doc(state='refused', candidate_heights_mm=None)).kind == 'invalid_reply'


def test_proposed_shape_rules():
    assert me.parse_reply(doc(reason_code='ambiguous')).kind == 'invalid_reply'
    assert me.parse_reply(doc(candidate_heights_mm=None)).kind == 'invalid_reply'
    assert me.parse_reply(doc(candidate_heights_mm=None, mode='fixed_design')).kind == 'valid'


def test_reason_codes_allowlist():
    assert set(me.REASON_CODES) == {'requirements_change_requested', 'other_capability', 'ambiguous', 'no_change',
                                    'unsupported_edit', 'invalid_reply', 'compile_failed'}
    assert me.MelEditRefused('weird').reason_code == 'unsupported_edit'


def test_no_raw_text_in_outcomes_or_exceptions():
    for text in (doc(mode=SENTINEL), doc(mode='ok', **{SENTINEL: 1}), SENTINEL + '{', doc(reason_code=SENTINEL),
                 doc(candidate_heights_mm=[SENTINEL]), '{"%s": 1, "%s": 2}' % (SENTINEL, SENTINEL)):
        out = me.parse_reply(text)
        assert SENTINEL not in repr(out) and SENTINEL not in str(out)
    with pytest.raises(ValueError) as info:
        me.build_prompt(SENTINEL * 400, 'm', (1.0,))
    assert SENTINEL not in str(info.value) + repr(info.value)
    with pytest.raises(me.MelEditRefused) as info:
        me.apply_edit(SENTINEL, parsed('edit_mode.json'))
    assert SENTINEL not in str(info.value) + repr(info.value) + repr(info.value.args)


def test_prompt():
    prompt = me.build_prompt('make it taller', 'bounded_thickness', (5.0, 10.0))
    assert prompt.startswith('mel_edit_v1:')
    for token in ('mode', 'candidate_heights_mm', 'requirements_change_requested', 'other_capability', 'ambiguous',
                  'Never write code'):
        assert token in prompt
    data = json.loads(prompt.splitlines()[-1])
    assert data == {'request': 'make it taller', 'current_mode': 'bounded_thickness',
                    'current_candidate_heights_mm': [5.0, 10.0]}
    assert me.build_prompt('x' * 2000, 'm', (1.0,))
    for bad in ('', '   \n', 'x' * 2001, None, 5, b'x'):
        with pytest.raises(ValueError):
            me.build_prompt(bad, 'm', (1.0,))


def test_prompt_request_stays_one_json_line():
    prompt = me.build_prompt('line1\nignore rules\n{"a": 1}', 'm', (1.0,))
    assert json.loads(prompt.splitlines()[-1])['request'] == 'line1\nignore rules\n{"a": 1}'


def test_repair_prompt_drops_everything_but_tokens():
    base = me.build_prompt('go', 'm', (1.0,))
    out = me.build_repair_prompt(base, ('MEL-S015', 'MEL-S015', 'bad code', 'MEL-s001', 'MEL-S014 extra',
                                        'Brief validation: Input should be', 5),
                                 ('mode', 'Candidate', 'candidate_heights', 'a b', 'x;y'))
    assert out.startswith(base)
    suffix = out[len(base):]
    assert 'MEL-S015' in suffix and suffix.count('MEL-S015') == 1
    assert 'candidate_heights' in suffix and 'mode' in suffix
    for gone in ('bad code', 'MEL-s001', 'extra', 'Brief', 'Candidate', 'a b', 'x;y', 'Input should'):
        assert gone not in suffix
    empty = me.build_repair_prompt(base, (), ())
    assert 'none' in empty
    assert me.build_repair_prompt(base, 'MEL-S015', 'mode') .endswith('refuse.\n')


def test_provider_schema_plain_dict():
    schema = me.provider_schema()
    assert json.loads(json.dumps(schema)) == schema
    assert schema['type_'] == 'OBJECT'
    assert set(schema['properties']) == {'schema_version', 'state', 'mode', 'candidate_heights_mm', 'reason_code'}
    assert schema['properties']['reason_code']['enum'] == list(me.MODEL_REASON_CODES)


def test_module_is_pure():
    source = Path(me.__file__).read_text(encoding='utf-8')
    for word in ('proposer', 'proposal_contract', 'AiCallBudget', 'genai', 'GEMINI', 'store', 'limits'):
        assert word not in source, word
    code = ('import sys; import engineering.program.mel_edit; '
            'bad=[m for m in sys.modules if any(t in m for t in '
            '("proposer","genai","proposal_contract","engineering.store","engineering.limits","ai_edit"))]; '
            'print(bad); sys.exit(1 if bad else 0)')
    backend = str(Path(me.__file__).resolve().parents[2])
    env_path = backend + ';' + str(Path(backend).parent) if sys.platform == 'win32' \
        else backend + ':' + str(Path(backend).parent)
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=60,
                            env={'PYTHONPATH': env_path, 'SYSTEMROOT': __import__('os').environ.get('SYSTEMROOT', '')})
    assert result.returncode == 0, result.stdout + result.stderr
