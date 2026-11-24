"""Bounded, versioned archive of a verified program and its exact delivered package."""

from argparse import ArgumentParser
from hashlib import sha256
from io import BytesIO
import json
from pathlib import Path
from zipfile import BadZipFile, ZIP_STORED, ZipFile

from .client import ProgramClient
from .interface import bounded_json_loads
from .hashing import canonical_json, execution_lock_v0_2_sha256, semantic_sha256
from .ir import load_execution_lock_json, load_program_json
from .kernel import ExecutionLockV0_2, canonical_entries

VERSION = 'mirum_program_replay_v0_1'
MAX_BYTES = 64 * 1024**2
MAX_METADATA = 1024**2
MEMBERS = ('program.mir', 'program-ir.json', 'execution-lock.json', 'delivery.zip', 'receipt.json',
           'operation-plan.json', 'check-plan.json')


def _json(value):
    return canonical_json(value).encode()


def _digest(data):
    return sha256(data).hexdigest()


def _bounded(path, maximum=MAX_BYTES):
    with Path(path).open('rb') as stream:
        data = stream.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('archive byte limit exceeded')
    return data


def _members(data, expected=None, *, expanded_limit=MAX_BYTES):
    if not 0 < len(data) <= MAX_BYTES:
        raise ValueError('archive byte limit exceeded')
    with ZipFile(BytesIO(data)) as archive:
        infos = archive.infolist()
        names = {info.filename for info in infos}
        if (not 0 < len(infos) <= 32 or len(names) != len(infos)
                or any(not name or '/' in name or '\\' in name or name in ('.', '..') for name in names)
                or sum(info.file_size for info in infos) > expanded_limit
                or (expected is not None and names != set(expected))):
            raise ValueError('unexpected archive members or byte count')
        files = {}
        for info in infos:
            with archive.open(info) as stream:
                value = stream.read(info.file_size + 1)
                if len(value) != info.file_size:
                    raise ValueError('archive member size changed')
                files[info.filename] = value
        return files


def _small(files, name):
    raw = files[name]
    if len(raw) > MAX_METADATA:
        raise ValueError('metadata byte limit exceeded')
    return bounded_json_loads(raw, max_bytes=MAX_METADATA, max_depth=32)


def check_plan(ir, lock, status):
    if ir.capability.id == 'equipment_bay':
        from .bay_bridge import check_plan_record, content_from_program
        from .planning import generator_candidates

        candidate, = generator_candidates(content_from_program(ir))
        return check_plan_record(status['semantic_sha256'], status['lock_sha256'], candidate)
    if ir.capability.id == 'regen_chamber':
        from .evidence import picogk_verified_check_plan
        return picogk_verified_check_plan()
    from .beam_bridge import brief_content_from_program
    from .evidence import build_check_plan
    from .planning import _candidates_from_content

    candidates = _candidates_from_content(brief_content_from_program(ir))
    return build_check_plan(ir, lock, candidates, mesh_convention_pinned=True)



def _delivered_files(data, ir):
    from engineering.capabilities import REGEN

    limit = REGEN.resources.run_bytes if ir.capability.id == REGEN.id else MAX_BYTES
    return _members(data, expanded_limit=limit)



def capture(client, revision_id):
    """Use only the revision's own authorized, digest-checked download routes."""
    snapshot = client.inspect(revision_id).model_dump(mode='json')
    status, evidence = snapshot['revision'], snapshot['evidence']
    if status['state'] != 'published' or status['artifacts'] != 'available' or evidence['verdict'] != 'verified':
        raise ValueError('capture needs an available, verified published revision')
    fetched = {
        'program.mir': client.export(revision_id, 'program-source').data,
        'program-ir.json': client.export(revision_id, 'program-ir').data,
        'execution-lock.json': client.export(revision_id, 'execution-lock').data,
        'delivery.zip': client.export(revision_id, 'zip').data,
        'receipt.json': _json({'revision': status, 'evidence': evidence}),
    }
    ir = load_program_json(fetched['program-ir.json'])
    lock = load_execution_lock_json(fetched['execution-lock.json'])
    fetched['operation-plan.json'] = _json(canonical_entries(ir.capability.id, ir.execution.exports))
    fetched['check-plan.json'] = _json(check_plan(ir, lock, status))
    out = BytesIO()
    with ZipFile(out, 'w', compression=ZIP_STORED) as bundle:
        for name in sorted(fetched):
            bundle.writestr(name, fetched[name])
        bundle.writestr('replay-manifest.json', _json({
            'schema_version': VERSION, 'members': {name: _digest(value) for name, value in fetched.items()},
        }))
    result = out.getvalue()
    replay(result)
    return result


def replay(data):
    """Re-compile source offline and verify every archived delivery byte and identity."""
    from engineering.language.semantic import compile_source

    try:
        outer = _members(data, (*MEMBERS, 'replay-manifest.json'))
        manifest = _small(outer, 'replay-manifest.json')
        if manifest != {'schema_version': VERSION, 'members': {name: _digest(outer[name]) for name in MEMBERS}}:
            raise ValueError('replay manifest mismatch')
        receipt = _small(outer, 'receipt.json')
        status, evidence = receipt['revision'], receipt['evidence']
        ir = load_program_json(outer['program-ir.json'])
        lock = load_execution_lock_json(outer['execution-lock.json'])
        if not isinstance(lock, ExecutionLockV0_2):
            raise ValueError('this replay version requires a native v0.2 execution lock')
        compiled = compile_source(outer['program.mir'], source_id=ir.provenance.source_id)
        if (compiled.ir != ir or compiled.source_sha256 != status['source_sha256']
                or semantic_sha256(ir) != status['semantic_sha256']
                or execution_lock_v0_2_sha256(lock) != status['lock_sha256']
                or compiled.brief_sha256 != status['brief_sha256']
                or compiled.brief_content.fixed_requirements_sha256 != status['fixed_requirements_sha256']
                or (lock.capability_id, lock.capability_version, lock.backend, lock.exports)
                != (ir.capability.id, ir.capability.version, ir.execution.backend, ir.execution.exports)
                or outer['operation-plan.json'] != _json(canonical_entries(ir.capability.id, ir.execution.exports))):
            raise ValueError('source, IR, lock or plan identities differ')
        if (outer['check-plan.json'] != _json(check_plan(ir, lock, status))
                or _digest(outer['check-plan.json']) != evidence['check_plan_sha256']):
            raise ValueError('archived check plan does not match the verification receipt')
        if (status['state'] != 'published' or evidence['verdict'] != 'verified'
                or _digest(outer['delivery.zip']) != status['bundle']['zip_sha256']):
            raise ValueError('delivery does not match the verified revision')
        delivery = _delivered_files(outer['delivery.zip'], ir)
        delivery_manifest = _small(delivery, 'manifest.json')
        identities = {row['path']: row for row in delivery_manifest['payloads']}
        recorded = {row['path']: row['sha256'] for row in status['bundle']['payloads']}
        if (set(delivery) != set(identities) | {'manifest.json'}
                or {name: row['sha256'] for name, row in identities.items()} != recorded
                or _digest(delivery['manifest.json']) != status['bundle']['manifest_sha256']
                or evidence['manifest_sha256'] != status['bundle']['manifest_sha256']):
            raise ValueError('delivered manifest mismatch')
        for name, identity in identities.items():
            if len(delivery[name]) != identity['bytes'] or _digest(delivery[name]) != identity['sha256']:
                raise ValueError('delivered artifact fingerprint mismatch')
        step = next((name for name in ('model.step', 'generator-body.step') if name in delivery), None)
        if step and _digest(delivery[step]) != evidence['step_sha256']:
            raise ValueError('delivered STEP does not match the verification receipt')
        if ir.capability.id == 'regen_chamber':
            report = _small(delivery, 'generator-report.json')
            if (report['stl_sha256'] != _digest(delivery['generator-cleaned.stl'])
                    or report['spec_sha256'] != evidence['candidate_sha256']):
                raise ValueError('archived STL or PicoGK preset differs from the verified delivery')
        return {'schema_version': VERSION, 'revision_id': status['revision_id'],
                'semantic_sha256': status['semantic_sha256'], 'lock_sha256': status['lock_sha256'],
                'check_plan_sha256': evidence['check_plan_sha256'], 'verified_files': sorted(identities)}
    except (KeyError, TypeError, BadZipFile, OSError) as exc:
        raise ValueError('malformed program replay package') from exc


async def remeasure(data):
    """Reread archived STEP under the exact reviewed native lock."""
    from .adapters import adapter_for
    from .kernel import Artifact, ArtifactRef
    from .planning import PlanRefusal, adapter_for_program, lock_program, plan_program

    evidence = replay(data)
    contents = _members(data, (*MEMBERS, 'replay-manifest.json'))
    receipt = _small(contents, 'receipt.json')
    ir = load_program_json(contents['program-ir.json'])
    lock = load_execution_lock_json(contents['execution-lock.json'])
    from engineering.language.semantic import compile_source

    compiled = compile_source(contents['program.mir'], source_id=ir.provenance.source_id)
    planned = plan_program(compiled)
    if isinstance(planned, PlanRefusal):
        raise ValueError('archived plan is unavailable')
    if ir.execution.backend == 'picogk':
        from functools import partial
        from tempfile import TemporaryDirectory
        from engineering.regen_web import JobRunner

        with TemporaryDirectory() as temporary:
            adapter = adapter_for('picogk', runner=partial(JobRunner, Path(temporary)))
    else:
        adapter = adapter_for_program(adapter_for, compiled)
    fresh = await lock_program(planned, adapter=adapter)
    if fresh != lock:
        raise ValueError('reviewed native lock is not available on this runtime')
    job = receipt['evidence']
    candidate = next(plan for plan in planned.plans if plan.candidate.candidate_sha256 == job['candidate_sha256'])
    delivery = _delivered_files(contents['delivery.zip'], ir)
    if ir.capability.id == 'equipment_bay':
        from engineering.equipment_bay import KERNEL_REVISION
        from engineering.equipment_bay_adapter import BOM, REPORT, STEP

        output = await adapter.measure_bay(delivery[STEP], candidate)
        report = output['report']
        report.update(search=output['search'], kernel_revision=KERNEL_REVISION)
        if (report['outcome'] != 'pass' or _json(report) != delivery[REPORT]
                or _json(output['bom']) != delivery[BOM]):
            raise ValueError('replayed bay differs from recorded numerical evidence')
    elif ir.capability.id == 'regen_chamber':
        path = 'generator-cleaned.stl'
        artifact = Artifact(ArtifactRef(name=path, format='stl', representation='mesh',
                                       sha256=_digest(delivery[path]), size=len(delivery[path]),
                                       node_id='regen.geometry'), delivery[path])
        result = await adapter.inspect(artifact, candidate, report=_small(delivery, 'generator-report.json'))
        if result.outcome != 'consistent':
            raise ValueError('replayed PicoGK mesh does not meet the reviewed reread limits')
    else:
        path = 'model.step'
        artifact = Artifact(ArtifactRef(name=path, format='step', representation='brep',
                                       sha256=_digest(delivery[path]), size=len(delivery[path]),
                                       node_id='beam.solid'), delivery[path])
        result = await adapter.inspect(artifact, candidate)
        if result.outcome != 'consistent':
            raise ValueError('replayed beam differs from the reviewed numerical limits')
    return dict(evidence, native_reread='consistent')



def main():
    import os
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('capture')
    create.add_argument('revision_id')
    create.add_argument('archive', type=Path)
    read = commands.add_parser('verify')
    read.add_argument('archive', type=Path)
    read.add_argument('--measure', action='store_true')
    args = parser.parse_args()
    if args.command == 'capture':
        client = ProgramClient(os.environ['MIRUM_API_URL'], token=os.environ.get('MIRUM_ACCESS_TOKEN'))
        data = capture(client, args.revision_id)
        with args.archive.open('xb') as output:
            output.write(data)
    else:
        data = _bounded(args.archive)
        if args.measure:
            import asyncio
            result = asyncio.run(remeasure(data))
        else:
            result = replay(data)
        print(json.dumps(result, sort_keys=True))


if __name__ == '__main__':
    main()
