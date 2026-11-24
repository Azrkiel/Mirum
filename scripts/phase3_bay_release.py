"""Native release-image smoke of the equipment-bay through ProgramInterface HTTP."""

import asyncio
from pathlib import Path
from hashlib import sha256
import json
import os
import time

from engineering.program.client import ClientError, ProgramClient, TransportError
from engineering.program.replay import capture, remeasure

SOURCE = Path(os.environ.get('BAY_SMOKE_SOURCE', '/app/backend/engineering/fixtures/equipment_bay/nominal.mir'))
URL = os.environ.get('BAY_SMOKE_URL', 'http://127.0.0.1:8000')


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def run(client, stage, *, seconds=180):
    revision = stage.revision
    rid = revision.revision_id
    client.review(rid, revision.brief_sha256, stage.review_fields, 'native image smoke')
    client.run(rid)
    stop = time.monotonic() + seconds
    while time.monotonic() < stop:
        status = client.status(rid).revision
        if status.result_kind != 'pending':
            return status
        time.sleep(0.75)
    raise RuntimeError('equipment-bay revision did not settle')


def main():
    client = ProgramClient(URL, token=os.environ.get('MIRUM_ACCESS_TOKEN'))
    for attempt in range(90):
        try:
            client.meta()
            break
        except (ClientError, TransportError):
            if attempt == 89:
                raise
            time.sleep(1)
    source = SOURCE.read_bytes()
    program = client.program_create().program_id
    nominal = run(client, client.stage(program, source, source_id='equipment-bay.mir'))
    require(nominal.state == 'published', f'nominal revision {nominal.state}: {nominal.reason}')
    rid = nominal.revision_id
    evidence = client.inspect(rid).evidence
    step = client.export(rid, 'generator-body-step').data
    bom = client.export(rid, 'generator-bom').data
    require(evidence.verdict == 'verified' and evidence.step_sha256 == sha256(step).hexdigest(),
            'verification receipt does not identify the delivered STEP')
    good = json.loads(client.export(rid, 'generator-report').data)
    require(good['envelope']['measured_mm'] == [480.0, 250.0] and good['mounting']['counts']['pass'] == 40,
            'nominal measurements changed')
    archive = asyncio.run(remeasure(capture(client, rid)))
    bad_source = source.replace(b'"nominal"', b'"supplier-shifted"')
    shifted = run(client, client.stage(program, bad_source, source_id='equipment-bay.mir',
                                       expected_parent=rid,
                                       require_requirements_sha256=nominal.fixed_requirements_sha256))
    require(shifted.state == 'rejected', f'supplier revision {shifted.state}: {shifted.reason}')
    bad = json.loads(client.export(shifted.revision_id, 'generator-report').data)
    failed = [row for row in bad['mounting']['correspondences'] if row['outcome'] == 'fail']
    require(len(failed) == 4 and {(row['occurrence_id'], row['offset_mm']) for row in failed} == {('module-01', 2.0)},
            'supplier substitution measurements changed')
    require(client.history(program).head_revision_id == rid
            and client.export(rid, 'generator-body-step').data == step
            and client.export(rid, 'generator-bom').data == bom,
            'rejection altered the accepted head or bytes')
    pico_evidence = None
    if os.environ.get('BAY_SMOKE_PICOGK') == '1':
        source_path = SOURCE.parent.parent / 'picogk' / 'reference.mir'
        pico_program = client.program_create().program_id
        pico = run(client, client.stage(pico_program, source_path.read_bytes(), source_id='picogk-reference.mir'),
                   seconds=420)
        require(pico.state == 'published', f'PicoGK revision {pico.state}: {pico.reason}')
        inspected = client.inspect(pico.revision_id).evidence
        require(inspected.verdict == 'verified' and inspected.claim_scope == 'geometry_checks_only'
                and inspected.step_sha256 is None,
                'PicoGK evidence must verify the STL within its geometry scope')
        pico_evidence = {'claim_scope': inspected.claim_scope, 'verdict': inspected.verdict,
                         'checks': [check.check_id for check in inspected.checks],
                         'replay': asyncio.run(remeasure(capture(client, pico.revision_id)))}
    print(json.dumps({
        'native_versions': good['tool_versions'], 'nominal_envelope_mm': good['envelope']['measured_mm'],
        'nominal_matches': good['mounting']['counts'], 'substitution_matches': bad['mounting']['counts'],
        'step_sha256': evidence.step_sha256, 'replay': archive, 'picogk': pico_evidence,
    }, indent=2, sort_keys=True))


if __name__ == '__main__':
    main()
