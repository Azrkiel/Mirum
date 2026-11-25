"""Operator-managed program retirement and offline restoration of verified replays."""

from __future__ import annotations

from argparse import ArgumentParser
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
from tempfile import TemporaryDirectory
import time
from zipfile import ZipFile

from engineering.capabilities import CAPABILITY
from engineering.store import SQLiteStore
from .delivered import EvidenceStore
from .facade import _evidence_view, revision_status
from .evidence import DeliveredEvidence
from .hashing import canonical_json
from .replay import MAX_BYTES, _bounded, build_replay, replay
from .revision_store import RevisionStore
from .revisions import ProgramRevision, RevisionView

ARCHIVE_DIR_ENV = 'MIRUM_ENGINEERING_PROGRAM_ARCHIVE_DIR'
VERSION = 'mirum_program_history_v0_1'
MAX_HISTORY_BYTES = 32 * 1024**2
_ID = re.compile('[0-9a-f]{32}')


def _id(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('invalid program or revision ID')
    return value


def _encoded(value):
    result = canonical_json(value).encode('utf-8')
    if len(result) > MAX_HISTORY_BYTES:
        raise ValueError('history byte limit exceeded')
    return result


def _history(path):
    return json.loads(_bounded(path / 'history.json', MAX_HISTORY_BYTES))


def _write(path, data):
    with path.open('xb') as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())


def _sync(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _archive_root(path, store):
    root = Path(path).resolve(strict=True)
    if not root.is_dir() or root == store.root or store.root in root.parents:
        raise ValueError('archive root must be outside the engineering volume')
    return root


def _captures(root, program_id):
    base = root / '.captures'
    program = base / _id(program_id)
    if base.is_symlink() or program.is_symlink():
        raise ValueError('invalid program capture directory')
    return program


class PublicationArchive:
    """Stage a complete verified replay before the corresponding head CAS."""

    def __init__(self, store, root):
        self.root = _archive_root(root, store)
        base = self.root / '.captures'
        if base.is_symlink():
            raise ValueError('invalid program capture directory')
        base.mkdir(mode=0o700, exist_ok=True)
        _sync(self.root)

    def save(self, revision, payload, evidence, publisher):
        with publisher.lease(revision.job_id, 'zip', envelope=revision.bundle) as download:
            if download.size > MAX_BYTES:
                raise ValueError('delivery exceeds the replay byte limit')
            delivery = download.file.read(MAX_BYTES + 1)
        data = build_replay(
            revision_status(RevisionView(revision=revision, artifacts='available',
                                         is_current=True)).model_dump(mode='json'),
            _evidence_view(evidence).model_dump(mode='json'),
            source=payload.source.encode('utf-8'),
            ir_json=payload.ir_json.encode('utf-8'),
            lock_json=payload.lock_json.encode('utf-8'),
            delivery=delivery,
        )
        folder = _captures(self.root, revision.program_id)
        folder.mkdir(mode=0o700, exist_ok=True)
        _sync(folder.parent)
        with TemporaryDirectory(dir=folder, prefix='.capture-') as temp:
            staged = Path(temp) / 'replay.zip'
            _write(staged, data)
            # A prior attempt may have reached disk before the database commit.
            staged.replace(folder / f'{_id(revision.revision_id)}.zip')
            _sync(folder)

    def discard(self, revision):
        folder = _captures(self.root, revision.program_id)
        (folder / f'{_id(revision.revision_id)}.zip').unlink(missing_ok=True)


def _same_evidence(revision, origin):
    fields = ('program_id', 'source_sha256', 'semantic_sha256', 'brief_sha256',
              'fixed_requirements_sha256', 'lock_sha256', 'job_id', 'bundle')
    return (origin is not None and origin.evidence_revision_id == origin.revision_id
            and all(getattr(revision, field) == getattr(origin, field) for field in fields))


def _snapshot(db, rs, evidence, program_id, now):
    program = rs.program(db, program_id)
    if program is None:
        return None
    revisions = rs.revisions(db, program_id)
    if any(rev.state in ('staged', 'running') for rev in revisions):
        raise ValueError('program has staged or running revisions')
    last_change = max(
        program['updated'],
        *(rev.updated_at.timestamp() for rev in revisions),
        program['created'],
    )
    if last_change > now - CAPABILITY.resources.state_retention_seconds:
        raise ValueError('program has not reached its retention age')
    published = {rev.revision_id: rev for rev in revisions if rev.state == 'published'}
    head = max(published.values(), key=lambda rev: rev.published_seq, default=None)
    if (program['head'], program['head_seq']) != (
            (None, 0) if head is None else (head.revision_id, head.published_seq)):
        raise ValueError('program head does not match its published history')
    records = {}
    for rev in revisions:
        record = evidence.get(db, rev.revision_id)
        if record is not None:
            records[rev.revision_id] = record.model_dump(mode='json')
        if rev.revision_id in published:
            origin = published.get(rev.evidence_revision_id)
            if not _same_evidence(rev, origin):
                raise ValueError('published revision does not match its evidence origin')
    return {
        'schema_version': VERSION,
        'program': {key: program[key] for key in ('id', 'head', 'head_seq', 'created', 'updated')},
        'revisions': [rev.model_dump(mode='json') for rev in revisions],
        'journal': [entry.model_dump(mode='json') for entry in rs.journal(db, program_id)],
        'evidence': records,
    }


def _origins(snapshot):
    return {row['revision_id']: row for row in snapshot['revisions']
            if row['state'] == 'published' and row['revision_id'] == row['evidence_revision_id']}


def _check_replay(data, snapshot):
    result = replay(data)
    rid = _id(result['revision_id'])
    row = _origins(snapshot).get(rid)
    if row is None:
        raise ValueError('replay does not belong to a published revision of this program')
    record = snapshot['evidence'].get(rid)
    if record is None:
        raise ValueError('published revision is missing verified evidence')
    record = DeliveredEvidence.model_validate_json(json.dumps(record))
    rev = ProgramRevision.model_validate_json(json.dumps(row))
    if (rev.program_id != snapshot['program']['id'] or record.verdict != 'verified'
            or record.job_id != rev.job_id
            or record.manifest_sha256 != rev.bundle.manifest_sha256):
        raise ValueError('published evidence does not match its revision')
    with ZipFile(BytesIO(data)) as package:
        receipt = json.loads(package.read('receipt.json'))
    status = revision_status(RevisionView(
        revision=rev, artifacts='available', is_current=False,
    )).model_dump(mode='json')
    # A later promotion can change only the captured current flag.
    receipt['revision']['is_current'] = False
    if receipt != {'revision': status, 'evidence': _evidence_view(record).model_dump(mode='json')}:
        raise ValueError('replay receipt does not match the stored decision')
    return rid, result


def _checked_archive(path, expected_id):
    if path.is_symlink() or not path.is_dir():
        raise ValueError('invalid program archive directory')
    try:
        snapshot = _history(path)
        if (snapshot['schema_version'] != VERSION or snapshot['program']['id'] != expected_id
                or set(snapshot['replays']) != set(_origins(snapshot))):
            raise ValueError('invalid archived program history')
        return snapshot
    except (KeyError, TypeError) as exc:
        raise ValueError('invalid archived program history') from exc


def _replay_bytes(path, snapshot, rid):
    digest = snapshot['replays'].get(rid)
    if digest is None:
        raise ValueError('revision has no archived verified replay')
    data = _bounded(path / f'{_id(rid)}.zip')
    if sha256(data).hexdigest() != digest:
        raise ValueError('archived replay digest mismatch')
    return data


def _save_archive(destination, snapshot, replay_paths):
    required = set(_origins(snapshot))
    with TemporaryDirectory(dir=destination.parent, prefix='.retire-') as temp:
        staged = Path(temp)
        digests = {}
        for path in replay_paths:
            if len(digests) >= len(required):
                raise ValueError('unexpected or duplicate replay input')
            data = _bounded(path)
            rid, _ = _check_replay(data, snapshot)
            if rid in digests:
                raise ValueError('duplicate replay input')
            digests[rid] = sha256(data).hexdigest()
            _write(staged / f'{rid}.zip', data)
        captures = _captures(destination.parent, destination.name)
        for rid in sorted(required - digests.keys()):
            capture = captures / f'{_id(rid)}.zip'
            if not capture.is_file():
                raise ValueError('missing replay for a published revision')
            data = _bounded(capture)
            actual, _ = _check_replay(data, snapshot)
            if actual != rid:
                raise ValueError('archive replay names the wrong revision')
            digests[rid] = sha256(data).hexdigest()
            _write(staged / f'{rid}.zip', data)
        _write(staged / 'history.json', _encoded(dict(snapshot, replays=digests)))
        _sync(staged)
        if destination.exists():
            raise FileExistsError(destination)
        staged.rename(destination)
        _sync(destination.parent)


def _verify_existing(destination, program_id, snapshot=None):
    previous = _checked_archive(destination, program_id)
    if snapshot is not None and previous != dict(snapshot, replays=previous['replays']):
        raise ValueError('existing archive differs from active program history')
    for rid in previous['replays']:
        data = _replay_bytes(destination, previous, rid)
        actual, _ = _check_replay(data, previous)
        if actual != rid:
            raise ValueError('archive replay names the wrong revision')
    _sync(destination)
    _sync(destination.parent)


def retire_program(store, program_id, archive_root, *, replay_paths=(), clock=time.time):
    """Durably archive an idle program before releasing its active rows.

    The caller owns the offline SQLite volume. Replays already saved at publication
    are discovered automatically. Supply a capture for any older missing replay.
    A retry can reuse a complete archive left before the database commit.
    """
    _id(program_id)
    root = _archive_root(archive_root, store)
    destination = root / program_id
    rs = RevisionStore(store, clock=clock)
    evidence = EvidenceStore(store, clock=clock)
    now = clock()
    with store.transaction() as db:
        snapshot = _snapshot(db, rs, evidence, program_id, now)
    if destination.exists() or destination.is_symlink():
        _verify_existing(destination, program_id, snapshot)
    elif snapshot is None:
        raise KeyError('unknown program')
    else:
        _save_archive(destination, snapshot, replay_paths)
    if snapshot is not None:
        with store.transaction() as db:
            if _snapshot(db, rs, evidence, program_id, now) != snapshot:
                raise ValueError('active program changed during archival')
            rs.retire(db, program_id)
    captures = _captures(root, program_id)
    if captures.is_dir():
        shutil.rmtree(captures)
        _sync(captures.parent)
    return destination


def restore_revision(archive, revision_id, output):
    """Copy an exact, validated replay out of retired history for offline verification."""
    archive, output = Path(archive), Path(output)
    snapshot = _checked_archive(archive, _id(archive.name))
    _id(revision_id)
    row = next((row for row in snapshot['revisions'] if row['revision_id'] == revision_id), None)
    if row is None or row['state'] != 'published':
        raise ValueError('revision has no archived verified replay')
    revision = ProgramRevision.model_validate_json(json.dumps(row))
    rid = _id(revision.evidence_revision_id)
    origin = _origins(snapshot).get(rid)
    if origin is None or not _same_evidence(revision, ProgramRevision.model_validate_json(json.dumps(origin))):
        raise ValueError('published revision does not match its evidence origin')
    data = _replay_bytes(archive, snapshot, rid)
    actual, result = _check_replay(data, snapshot)
    if actual != rid:
        raise ValueError('archive replay names the wrong revision')
    # The same filesystem makes the no-overwrite install atomic. Report the replay's
    # original evidence revision, including when the requested row was a rollback.
    with TemporaryDirectory(dir=output.parent, prefix='.restore-') as temp:
        staged = Path(temp) / 'replay.zip'
        _write(staged, data)
        os.link(staged, output)
        _sync(output.parent)
    return result


@contextmanager
def _open_volume(root):
    if not (root / 'engineering.sqlite3').is_file():
        raise ValueError('engineering volume does not contain a database')
    store = SQLiteStore(root)
    try:
        yield store
    finally:
        store.close()


def main():
    parser = ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    retire = commands.add_parser('retire')
    retire.add_argument('volume', type=Path)
    retire.add_argument('program_id')
    retire.add_argument('archive_root', type=Path)
    retire.add_argument('--replay', type=Path, action='append', default=[])
    restore = commands.add_parser('restore')
    restore.add_argument('archive', type=Path)
    restore.add_argument('revision_id')
    restore.add_argument('output', type=Path)
    args = parser.parse_args()
    if args.command == 'retire':
        with _open_volume(args.volume) as store:
            result = retire_program(store, args.program_id, args.archive_root, replay_paths=args.replay)
        print(result)
    else:
        print(json.dumps(restore_revision(args.archive, args.revision_id, args.output), sort_keys=True))


if __name__ == '__main__':
    main()
