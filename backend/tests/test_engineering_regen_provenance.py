import hashlib
from pathlib import Path

import pytest

from backend.tests.test_engineering_regen_adapter import accepted_report, build, candidate
from engineering.generator_policy import assess_generator
from engineering.regen_adapter import regen_provenance, with_provenance


def test_provenance_hashes_worker_files_and_records_policy(tmp_path, monkeypatch):
    builder = tmp_path / 'builder'
    runtime = tmp_path / 'picogk.26.2.so'
    builder.write_bytes(b'builder fixture')
    runtime.write_bytes(b'runtime fixture')
    gcc_lib = str(tmp_path / 'lib')
    image_id = 'registry.example/test-image_v1@sha256:abcd'
    monkeypatch.setenv('REGEN_BUILDER', str(builder))
    monkeypatch.setenv('REGEN_GCC_LIB', gcc_lib)
    monkeypatch.setenv('REGEN_IMAGE_ID', image_id)

    assert regen_provenance() == {
        'builder_path': str(builder),
        'runtime_so_path': str(runtime),
        'gcc_lib_path': gcc_lib,
        'builder_sha256': hashlib.sha256(b'builder fixture').hexdigest(),
        'runtime_so_sha256': hashlib.sha256(b'runtime fixture').hexdigest(),
        'image_id': image_id,
        'run_bytes': 256 * 1024**2,
        'resource_policy_version': 'engineering_resources_v1',
    }


def test_provenance_worker_defaults():
    result = regen_provenance({})
    assert Path(result['builder_path']) == Path('/opt/regen/app/builder')
    assert Path(result['runtime_so_path']) == Path('/opt/regen/app/picogk.26.2.so')
    assert result['gcc_lib_path'] == '/opt/regen/lib'
    assert result['image_id'] is None


@pytest.mark.parametrize('image_id', ['', 'bad id', 'id\n', 'x' * 129])
def test_missing_files_and_invalid_image_id(tmp_path, image_id):
    result = regen_provenance({'REGEN_BUILDER': str(tmp_path / 'absent'), 'REGEN_IMAGE_ID': image_id})
    assert result['builder_sha256'] is None
    assert result['runtime_so_sha256'] is None
    assert result['image_id'] is None


@pytest.mark.parametrize('accepted', [True, False])
def test_provenance_preserves_evaluation_and_policy_outcome(tmp_path, accepted):
    cand = candidate()
    evaluation = build(cand, accepted_report(passed=accepted), accepted=accepted)
    env = {'REGEN_BUILDER': str(tmp_path / 'builder')}

    enriched = with_provenance(evaluation, env)

    assert 'provenance' not in evaluation
    assert enriched.keys() == evaluation.keys() | {'provenance'}
    assert all(enriched[key] is value for key, value in evaluation.items())
    assert enriched['provenance'] == regen_provenance(env)
    decision = assess_generator(cand, enriched)
    assert decision.available, decision.reasons
    assert decision.outcome.value == ('accepted' if accepted else 'rejected')
