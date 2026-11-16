"""Provider is a local double, including client/key path; never network."""
import asyncio
import json
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from backend.dragsters import extraction
from backend.dragsters.spec_v2 import parse_v2
from backend.dragsters.styles import load_preset


@pytest.fixture
def provider(monkeypatch):
    monkeypatch.setenv('GEMINI_API_KEY_HEAVY', 'unit-test-placeholder')
    sdk = ModuleType('google.generativeai')
    model = MagicMock()
    model.generate_content_async = AsyncMock(return_value=SimpleNamespace(text='{"changes":[],"unsupported":[],"questions":[]}'))
    sdk.GenerativeModel = MagicMock(return_value=model)
    sdk.configure = MagicMock(side_effect=AssertionError('Global client mutation'))
    low = ModuleType('google.ai.generativelanguage_v1beta')
    client = MagicMock()
    client.transport.close = AsyncMock()
    low.GenerativeServiceAsyncClient = MagicMock(return_value=client)
    google = ModuleType('google'); google.__path__ = []
    google.generativeai = sdk
    ai = ModuleType('google.ai'); ai.__path__ = []
    ai.generativelanguage_v1beta = low
    google.ai = ai
    for name, module in [('google', google), ('google.ai', ai),
                         ('google.generativeai', sdk), ('google.ai.generativelanguage_v1beta', low)]:
        monkeypatch.setitem(sys.modules, name, module)
    def answer(changes=(), unsupported=(), questions=()):
        model.generate_content_async.return_value = SimpleNamespace(text=json.dumps(dict(
            changes=list(changes), unsupported=list(unsupported), questions=list(questions))))
    return SimpleNamespace(model=model, sdk=sdk, low=low, client=client, answer=answer)


def change(field, value, unit='mm', op='set'):
    return dict(field=field, value=value, unit=unit, op=op)


@pytest.mark.asyncio
@pytest.mark.parametrize('prompt,style,patch,path,expected', [
    ('CO2 wedge 220 mm long', 'wedge', change('length_mm',220), 'length_mm',220),
    ('CO₂ teardrop 24 cm long', 'teardrop', change('length_mm',24,'cm'), 'length_mm',240),
    ('wedge 8.5 inches long', 'wedge', change('length_mm',8.5,'in'), 'length_mm',215.9),
    ('narrow waist front width 42mm', 'narrow_waist', change('front_width_mm',42), 'front_width_mm',42),
    ('tear-drop rear wheels 32mm', 'teardrop', change('wheels.rear_diameter_mm',32), 'wheels.rear_diameter_mm',32),
    ('tear drop side height fraction .55', 'teardrop', change('side_height_frac',.55,'ratio'), 'side_height_frac',.55),
    ('waisted car waist 24mm', 'narrow_waist', change('waist_width_mm',24), 'waist_width_mm',24),
    ('coke bottle housing 46mm wide', 'narrow_waist', change('housing_width_mm',46), 'housing_width_mm',46),
    ('raised rear housing 82mm long', 'narrow_waist', change('housing_length_mm',82), 'housing_length_mm',82),
    ('wedge pocket 5 cm deep', 'wedge', change('pocket_depth_mm',5,'cm'), 'pocket_depth_mm',50),
    ('teardrop crown exponent 3.2', 'teardrop', change('crown_exponent',3.2,'ratio'), 'crown_exponent',3.2),
    ('wedge wheel width .25in', 'wedge', change('wheels.width_mm',.25,'in'), 'wheels.width_mm',6.35),
])
async def test_supported_explicit_quantities(provider, prompt, style, patch, path, expected):
    provider.answer([patch])
    result = await extraction.interpret(prompt, None)
    assert result['status'] == 'resolved', result
    spec = result['spec']
    assert spec['style'] == style
    value = spec
    for part in path.split('.'):
        value = value[part]
    assert value == pytest.approx(expected)
    assert result['provenance'][path] == 'supplied'
    assert path in result['changed_fields']
    parse_v2(json.dumps(spec))
    provider.low.GenerativeServiceAsyncClient.assert_called_once_with(client_options={'api_key':'unit-test-placeholder'})
    assert provider.model._async_client is provider.client
    provider.client.transport.close.assert_awaited_once()
    config = provider.model.generate_content_async.call_args.kwargs['generation_config']
    assert config['temperature'] == 0 and config['max_output_tokens'] == 2048
    assert provider.model.generate_content_async.call_args.kwargs['request_options'] == {'timeout':30}
    assert not any(token in json.dumps(config['response_schema']) for token in ('anyOf','$ref','exclusiveMinimum'))


@pytest.mark.asyncio
@pytest.mark.parametrize('patch,expected', [(change('length_mm',10,op='add'),210),
                                           (change('length_mm',1.1,'none','scale'),220)])
async def test_refinement(provider, patch, expected):
    provider.answer([patch])
    base = load_preset('wedge')
    result = await extraction.interpret('make it longer', base)
    assert result['status'] == 'resolved'
    assert result['spec']['length_mm'] == pytest.approx(expected)
    assert result['provenance']['width_mm'] == 'base_spec'
    assert base.length_mm == 200


@pytest.mark.asyncio
async def test_density_conversion(provider):
    base = load_preset('wedge').model_dump()
    base['material'] = dict(name='balsa', density_g_cm3=.2)
    provider.answer([change('material.density_g_cm3',160,'kg/m3')])
    result = await extraction.interpret('density 160 kg per cubic metre', parse_v2(json.dumps(base)))
    assert result['status'] == 'resolved'
    assert result['spec']['material']['density_g_cm3'] == .16


@pytest.mark.asyncio
@pytest.mark.parametrize('prompt', ['wedge and teardrop', 'design a CO2 dragster'])
async def test_style_clarifications_do_not_call_provider(provider, prompt):
    result = await extraction.interpret(prompt, None)
    assert result['status'] == 'needs_clarification' and result['questions']
    provider.sdk.GenerativeModel.assert_not_called()


@pytest.mark.asyncio
async def test_qualitative_and_unsupported_apply_nothing(provider):
    provider.answer([change('length_mm',220)], questions=['How much wider in mm?'])
    result = await extraction.interpret('wedge, make it wider', None)
    assert result['status'] == 'needs_clarification' and 'spec' not in result
    provider.answer([change('length_mm',220)], unsupported=['spoiler'])
    result = await extraction.interpret('wedge with spoiler', None)
    assert result['status'] == 'unsupported' and 'spec' not in result
    assert result['supported_fields']


@pytest.mark.asyncio
@pytest.mark.parametrize('patch', [change('__class__',1), change('schema_version',1,'none'),
    change('length_mm',1,'ratio'), change('length_mm',True), change('length_mm',float('nan')),
    change('length_mm',float('inf')), change('length_mm',200,op='execute'),
    change('side_height_frac',1.1,'none','scale'), change('wheels.rear_diameter_mm',1.1,'none','scale'),
    change('length_mm',200,'feet')])
async def test_invalid_model_patch_rejected(provider, patch):
    provider.answer([patch])
    with pytest.raises(extraction.ExtractionUnavailable):
        await extraction.interpret('teardrop', None)


@pytest.mark.asyncio
@pytest.mark.parametrize('text', ['x'*20480, '{', '[]', '{"changes":[],"unsupported":[],"questions":[],"code":"x"}',
                                  '['*1200 + ']'*1200,
                                  json.dumps(dict(changes=[change('length_mm',220)]*50, unsupported=[], questions=[]))],
                         ids=['over-budget','broken-json','array','extra-field','deep-json','too-many-changes'])
async def test_size_shape_caps(provider, text):
    provider.model.generate_content_async.return_value = SimpleNamespace(text=text)
    with pytest.raises(extraction.ExtractionUnavailable):
        await extraction.interpret('wedge', None)


@pytest.mark.asyncio
@pytest.mark.parametrize('failure', [TimeoutError('private key'), RuntimeError('/tmp/private unit-test-placeholder')])
async def test_provider_failure_is_typed_and_redacted(provider, failure):
    provider.model.generate_content_async.side_effect = failure
    with pytest.raises(extraction.ExtractionUnavailable) as caught:
        await extraction.interpret('wedge', None)
    assert 'private' not in str(caught.value) and 'placeholder' not in str(caught.value)
    provider.client.transport.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_missing_key(provider, monkeypatch):
    monkeypatch.delenv('GEMINI_API_KEY_HEAVY')
    with pytest.raises(extraction.ExtractionUnavailable):
        await extraction.interpret('wedge', None)
    provider.sdk.GenerativeModel.assert_not_called()


@pytest.mark.asyncio
async def test_invalid_geometry_is_clarification(provider):
    provider.answer([change('pocket_center_z_mm',60)])
    result = await extraction.interpret('wedge pocket Z 60mm', None)
    assert result['status'] == 'needs_clarification'
    assert 'pocket_center_z_mm' in ' '.join(result['questions'])
    assert 'spec' not in result


@pytest.mark.asyncio
async def test_teardrop_36mm_rear_wheel_requires_clearance_clarification(provider):
    provider.answer([change('wheels.rear_diameter_mm',36)])
    result = await extraction.interpret('CO2 teardrop rear wheels 36mm', None)
    assert result['status'] == 'needs_clarification'
    assert 'wheels.rear_diameter_mm' in ' '.join(result['questions'])


def test_live_sample_comparison_detects_missing_and_wrong_values_without_calls():
    from backend.dragsters.eval.run_live import mismatches
    case = dict(expected_style='wedge', expected_fields={'length_mm':220, 'wheels.width_mm':6})
    assert mismatches(case, dict(status='resolved', spec={'style':'wedge','length_mm':220,'wheels':{'width_mm':6}})) == []
    assert mismatches(case, dict(status='resolved', spec={'style':'teardrop','length_mm':221}))
    assert mismatches(case, dict(status='needs_clarification'))


@pytest.mark.asyncio
async def test_no_material_name_is_not_invented(provider):
    provider.answer([change('material.density_g_cm3',160,'kg/m3')])
    result = await extraction.interpret('wedge density 160 kg/m3', None)
    assert result['status'] == 'needs_clarification'
    assert 'material name' in ' '.join(result['questions'])


@pytest.mark.asyncio
async def test_complete_stock_patch_can_create_optional_block(provider):
    provider.answer([change('stock.length_mm',250),change('stock.width_mm',60),change('stock.height_mm',50)])
    result = await extraction.interpret('wedge stock 250x60x50 mm', None)
    assert result['status'] == 'resolved'
    assert result['spec']['stock'] == dict(length_mm=250,width_mm=60,height_mm=50)
