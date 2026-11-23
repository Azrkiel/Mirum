"""F08 native suite (P11/P12 geometry evidence): the program interface over HTTP with the REAL kernel.

Native shard only: skipped when cadquery is absent or off posix (same guard as
test_program_compiled_beam_native.py and test_program_delivered_native.py). The app is main.app with the
flag on (environment only), a tmp data dir, the real OCCT adapter, the real job controller and the real
F07 delivered verifier. In a native shard fastapi and httpx are required: a missing one is an error here,
not a skip.

  beam.mir      create, stage, review (brief hash and fields from the stage response), run, poll to a
                terminal state: published, 15 mm selected, evidence verdict verified; the CLI export of
                model.step has the sha256 the status, the evidence and X-Content-SHA256 report.
  beam_5_10.mir run through the CLI: result_kind rejected with HTTP 200 (a result, not an error); the fixed
                requirements are unchanged from the stage.
"""
import hashlib
import io
import json
import os
import time

import pytest

pytest.importorskip('cadquery')

# process_runner.ProcessRunner returns PROCESS_TREE_UNAVAILABLE off posix, so the native path (probe,
# worker) cannot run on a Windows host even when cadquery imports. The native shards are posix.
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='ProcessRunner needs posix process groups')

from engineering.program import cli as K  # noqa: E402
from engineering.program import interface as api  # noqa: E402
from tests.test_program_interface_parity import (  # noqa: E402
    BASE, BEAM_5_10_BYTES, BEAM_BYTES, TOKEN, V1, bridge, http_headers, parity_app, setup_revision,
)

POLL_SECONDS = 540.0


@pytest.fixture(scope='module')
def native_world(tmp_path_factory):
    with pytest.MonkeyPatch.context() as mp:
        with parity_app(tmp_path_factory.mktemp('f08native'), mp, 'native', strict=True) as w:
            yield w


def poll(w, who, rid):
    """Poll GET /revisions/{rid} until the revision leaves the pending kinds (bounded)."""
    deadline = time.monotonic() + POLL_SECONDS
    while True:
        response = w.c.get(f'{V1}/revisions/{rid}', headers=http_headers(who))
        assert response.status_code == 200, response.text
        revision = response.json()['revision']
        if revision['result_kind'] != 'pending':
            return revision
        assert time.monotonic() < deadline, 'the revision did not reach a terminal state'
        time.sleep(1.0)


@pytest.fixture(scope='module')
def beam(native_world):
    w, who = native_world, 'native'
    setup = setup_revision(w, who, BEAM_BYTES)
    staged = setup.stage['revision']
    assert staged['state'] == 'staged' and staged['result_kind'] == 'pending'
    run = w.c.post(f'{V1}/revisions/{setup.rid}/run', headers=http_headers(who))
    assert run.status_code == 200 and run.json()['revision']['state'] == 'running', run.text
    final = poll(w, who, setup.rid)
    return {'setup': setup, 'staged': staged, 'final': final, 'who': who}


def test_native_beam_publishes_15mm_with_verified_evidence(native_world, beam):
    w, who, rid = native_world, beam['who'], beam['setup'].rid
    final = beam['final']
    assert final['state'] == 'published' and final['result_kind'] == 'published', final
    assert final['artifacts'] == 'available' and final['is_current'] is True
    # the reviewed requirements are exactly what ran
    assert final['fixed_requirements_sha256'] == beam['staged']['fixed_requirements_sha256']
    assert final['brief_sha256'] == beam['staged']['brief_sha256']
    spec = w.c.get(f'{V1}/revisions/{rid}/artifacts/spec', headers=http_headers(who))
    assert spec.status_code == 200
    assert json.loads(spec.content)['height_mm'] == 15
    inspected = w.c.get(f'{V1}/revisions/{rid}/evidence', headers=http_headers(who))
    assert inspected.status_code == 200
    evidence = inspected.json()['evidence']
    assert evidence['status'] == 'present' and evidence['verdict'] == 'verified', evidence
    assert evidence['manifest_sha256'] == final['bundle']['manifest_sha256']
    text = inspected.text
    for needle in ('draft_id', 'brief_id', 'job_id'):
        assert needle not in text
    history = w.c.get(f"{V1}/programs/{beam['setup'].pid}/history", headers=http_headers(who)).json()
    assert history['head_revision_id'] == rid


def test_native_cli_export_sha_equals_the_manifest_digest(native_world, beam, tmp_path):
    w, who, rid = native_world, beam['who'], beam['setup'].rid
    final = beam['final']
    assert final['state'] == 'published'
    manifest = {p['path']: p['sha256'] for p in final['bundle']['payloads']}
    assert 'model.step' in manifest
    out, err = io.StringIO(), io.StringIO()
    code = K.main(['export', rid, 'step', '--out', str(tmp_path)], transport=bridge(w, who),
                  environ={'MIRUM_API_URL': BASE, 'MIRUM_ACCESS_TOKEN': TOKEN}, stdout=out, stderr=err)
    assert code == 0 and err.getvalue() == '', err.getvalue()
    written = tmp_path / 'model.step'
    digest = hashlib.sha256(written.read_bytes()).hexdigest()
    meta = json.loads(out.getvalue())
    assert digest == meta['sha256'] == manifest['model.step']
    assert meta['size_bytes'] == written.stat().st_size and meta['filename'] == 'model.step'
    served = w.c.get(f'{V1}/revisions/{rid}/artifacts/step', headers=http_headers(who))
    assert served.status_code == 200 and served.headers['x-content-sha256'] == digest
    evidence = w.c.get(f'{V1}/revisions/{rid}/evidence', headers=http_headers(who)).json()['evidence']
    assert evidence['step_sha256'] == digest
    assert sorted(p.name for p in tmp_path.iterdir()) == ['model.step']


def cli_json(w, who, argv):
    out, err = io.StringIO(), io.StringIO()
    code = K.main(argv, transport=bridge(w, who), environ={'MIRUM_API_URL': BASE, 'MIRUM_ACCESS_TOKEN': TOKEN},
                  stdout=out, stderr=err)
    return code, out.getvalue(), err.getvalue()


def test_native_5_10_batch_is_rejected_not_an_error_and_requirements_stay_fixed(native_world):
    w, who = native_world, 'native'
    setup = setup_revision(w, who, BEAM_5_10_BYTES)
    staged = setup.stage['revision']
    code, out, err = cli_json(w, who, ['run', setup.rid])
    assert code == 0 and err == '' and json.loads(out)['revision']['state'] == 'running', (out, err)
    deadline = time.monotonic() + POLL_SECONDS
    while True:
        code, out, err = cli_json(w, who, ['status', setup.rid])
        assert err == '', err
        final = json.loads(out)['revision']
        if final['result_kind'] != 'pending':
            break
        assert time.monotonic() < deadline, 'the revision did not reach a terminal state'
        time.sleep(1.0)
    # rejection is a result: exit 2 through result_exit_code, no error envelope
    assert final['state'] == 'rejected' and final['result_kind'] == 'rejected', final
    assert code == api.result_exit_code('rejected') == 2
    over_http = w.c.get(f'{V1}/revisions/{setup.rid}', headers=http_headers(who))
    assert over_http.status_code == 200 and over_http.json()['revision']['result_kind'] == 'rejected'
    assert final['fixed_requirements_sha256'] == staged['fixed_requirements_sha256']
    assert final['brief_sha256'] == staged['brief_sha256'] and final['semantic_sha256'] == staged['semantic_sha256']
    # nothing was published: no head, no artifacts, no evidence
    program = w.c.get(f'{V1}/programs/{setup.pid}', headers=http_headers(who)).json()
    assert program['head_revision_id'] is None and program['current'] is None
    assert final['artifacts'] != 'available' and final['is_current'] is False
    evidence = w.c.get(f'{V1}/revisions/{setup.rid}/evidence', headers=http_headers(who)).json()['evidence']
    assert evidence['status'] == 'none'
