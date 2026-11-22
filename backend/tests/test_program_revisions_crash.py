"""F06 task 5: RevisionService.recover() (D12), crash seams, restart and cancel-vs-settle races.

Host tests (Windows OK) drive recover() through the HostStore fake with a faked job table and publisher.
Everything marked ``native_only`` needs cadquery, a real posix SQLiteStore, the job controller and
ProcessRunner: those tests run only in the CI native shards (the real os._exit crashes included).

Crash injection: subclass RevisionService and override ``_crash_point``; the publish seam patches
``os.replace`` inside the crashing subprocess only.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering.contracts import Lifecycle, Outcome
from engineering.program import transactions as tx
from engineering.program.revisions import TERMINAL_STATES
from engineering.program.transactions import RevisionService
from tests.test_program_revision_records import H, bundle, compiled, lock  # noqa: F401  (fixtures)
from tests.test_program_revisions import (  # noqa: F401  (fixtures and helpers reused from task 4)
    BEAM, CLIENT, F06, P03, REJECTED, WHITESPACE, Clock, HostStore, World, _finalize_brief,
    _publisher_class, _replay, clock, evals, evolve, hexid, host, in_state, journal_count, native_only,
    new_program, occt, raw_insert, rs, stage_final, staged, start_run, step_sha, table_counts, with_first,
    land, land_staged)

ROOT = Path(__file__).resolve().parents[2]


# ====================================================================== host tests: recover() logic

class FakePub:
    def __init__(self, clock, *, validate_error=None, quick_error=None, ttl=100.0):
        self.clock, self.validate_error, self.quick_error, self.ttl = clock, validate_error, quick_error, ttl
        self.calls = []

    def validate(self, job_id, envelope=None):
        self.calls.append(('validate', job_id, envelope))
        if self.validate_error is not None:
            raise self.validate_error

    def quick_check(self, job_id, envelope=None):
        self.calls.append(('quick_check', job_id))
        if self.quick_error is not None:
            raise self.quick_error

    def expires_at(self, job_id):
        return self.clock() + self.ttl


def _service(host, clock, pub):
    jobs = SimpleNamespace(store=host, publisher=pub)  # no admit/start: recover must never call them
    briefs = SimpleNamespace(repository=SimpleNamespace(store=host))
    return RevisionService(host, jobs, pub, briefs, clock=clock)


@pytest.fixture
def runs(monkeypatch):
    table = {}
    monkeypatch.setattr(tx, 'parse_run', lambda value: table[value])
    return table


def _accepted(bundle):
    return SimpleNamespace(lifecycle=Lifecycle.COMPLETED, outcome=Outcome.ACCEPTED, bundle=bundle,
                           brief_sha256=H, fixed_requirements_sha256=H)


def _ended(lifecycle, outcome=Outcome.NOT_EVALUATED):
    return SimpleNamespace(lifecycle=lifecycle, outcome=outcome, bundle=None,
                           brief_sha256=H, fixed_requirements_sha256=H)


def _running(host, rs, lock, bundle, runs, pid, n, parent, run, *, cancel=False):
    rev = evolve(in_state(staged(lock, bundle, pid, n, parent), 'running', bundle), job_id=hexid(0xA00 + n))
    raw_insert(host, rs, rev)
    if run is not None:
        host.db.execute('INSERT INTO jobs(id, brief, record) VALUES (?,NULL,?)', (rev.job_id, f'run-{n}'))
        runs[f'run-{n}'] = run
    if cancel:
        host.db.execute('UPDATE program_revisions SET cancel=1 WHERE id=?', (rev.revision_id,))
    return rev


def _published_head(host, rs, lock, bundle, pid, n):
    rev = evolve(in_state(staged(lock, bundle, pid, n, None), 'published', bundle), job_id=hexid(0xA00 + n))
    raw_insert(host, rs, rev)
    host.db.execute('UPDATE programs SET head=?, head_seq=1 WHERE id=?', (rev.revision_id, pid))
    return rev


def _state(svc, pid, rid):
    return next(r for r in svc.history(pid, client='c') if r.revision_id == rid)


def _events(svc, pid):
    return [(e.event, e.revision_id) for e in svc.journal(pid, client='c')]


@pytest.mark.parametrize('kind', ['missing', 'staged', 'foreign'])
def test_recover_fails_closed_on_a_bad_head_and_modifies_nothing(host, clock, rs, lock, bundle, kind):
    from tests.test_program_revisions import payload
    pid, other = new_program(rs, host, 'c'), new_program(rs, host, 'c')
    bystander = staged(lock, bundle, pid, 7, None)
    with host.transaction() as db:
        rs.insert_staged(db, bystander, payload(), expected_parent=None)
        rs.append_journal(db, pid, bystander.revision_id, 'admit_intent', {})
    if kind == 'missing':
        head = hexid(0xDEAD)
    elif kind == 'staged':
        head = bystander.revision_id
    else:
        head = _published_head(host, rs, lock, bundle, other, 1).revision_id
    host.db.execute('UPDATE programs SET head=? WHERE id=?', (head, pid))
    before = journal_count(host)
    with pytest.raises(RuntimeError):
        _service(host, clock, FakePub(clock))
    assert journal_count(host) == before
    assert host.db.execute('SELECT state FROM program_revisions WHERE id=?',
                           (bystander.revision_id,)).fetchone()[0] == 'staged'


def test_recover_staged_with_intent_fails_interrupted_without_intent_stays(host, clock, rs, lock, bundle):
    from tests.test_program_revisions import payload
    pid = new_program(rs, host, 'c')
    quiet, intent = staged(lock, bundle, pid, 1, None), staged(lock, bundle, pid, 2, None)
    with host.transaction() as db:
        rs.insert_staged(db, quiet, payload(), expected_parent=None)
        rs.insert_staged(db, intent, payload(), expected_parent=None)
        rs.append_journal(db, pid, intent.revision_id, 'admit_intent', {'brief_id': hexid(9)})
    svc = _service(host, clock, FakePub(clock))
    assert _state(svc, pid, quiet.revision_id).state == 'staged'
    failed = _state(svc, pid, intent.revision_id)
    assert failed.state == 'failed' and failed.reason == 'interrupted' and failed.job_id is None
    assert _events(svc, pid) == [('staged', quiet.revision_id), ('staged', intent.revision_id),
                                 ('admit_intent', intent.revision_id), ('recovered', intent.revision_id)]
    assert svc.current(pid, client='c') is None


def test_recover_running_without_a_job_row_fails(host, clock, rs, lock, bundle, runs):
    pid = new_program(rs, host, 'c')
    rev = _running(host, rs, lock, bundle, runs, pid, 1, None, None)
    svc = _service(host, clock, FakePub(clock))
    done = _state(svc, pid, rev.revision_id)
    assert done.state == 'failed' and done.reason == 'job_missing'
    assert _events(svc, pid) == [('failed', rev.revision_id), ('recovered', rev.revision_id)]


def test_recover_rolls_forward_an_accepted_job_with_full_validation(host, clock, rs, lock, bundle, runs):
    pid = new_program(rs, host, 'c')
    first = _published_head(host, rs, lock, bundle, pid, 1)
    rev = _running(host, rs, lock, bundle, runs, pid, 2, first.revision_id, _accepted(bundle))
    pub = FakePub(clock)
    svc = _service(host, clock, pub)
    done = _state(svc, pid, rev.revision_id)
    assert done.state == 'published' and done.published_seq == 2 and done.bundle == bundle
    assert host.db.execute('SELECT head, head_seq FROM programs WHERE id=?', (pid,)).fetchone()[:] == (rev.revision_id, 2)
    assert [c for c in pub.calls if c[0] == 'validate'] == [('validate', rev.job_id, bundle)]
    assert _events(svc, pid)[-2:] == [('published', rev.revision_id), ('recovered', rev.revision_id)]


def test_recover_conflicts_when_a_sibling_already_took_the_head(host, clock, rs, lock, bundle, runs):
    pid = new_program(rs, host, 'c')
    first = _published_head(host, rs, lock, bundle, pid, 1)
    a = _running(host, rs, lock, bundle, runs, pid, 2, first.revision_id, _accepted(bundle))
    b = _running(host, rs, lock, bundle, runs, pid, 3, first.revision_id, _accepted(bundle))
    svc = _service(host, clock, FakePub(clock))
    states = {r.revision_id: r for r in svc.history(pid, client='c')}
    winner, loser = sorted((a, b), key=lambda r: r.revision_id)
    assert states[winner.revision_id].state == 'published'
    assert states[loser.revision_id].state == 'conflict' and states[loser.revision_id].reason == 'stale_parent'
    assert host.db.execute('SELECT head FROM programs WHERE id=?', (pid,)).fetchone()[0] == winner.revision_id
    assert ('recovered', loser.revision_id) in _events(svc, pid)


@pytest.mark.parametrize('how', ['validate', 'quick', 'expired'])
def test_recover_never_publishes_an_invalid_bundle(host, clock, rs, lock, bundle, runs, how):
    pid = new_program(rs, host, 'c')
    rev = _running(host, rs, lock, bundle, runs, pid, 1, None, _accepted(bundle))
    pub = FakePub(clock, validate_error=ValueError('Package fingerprint changed') if how == 'validate' else None,
                  quick_error=KeyError('gone') if how == 'quick' else None, ttl=-1.0 if how == 'expired' else 100.0)
    svc = _service(host, clock, pub)
    done = _state(svc, pid, rev.revision_id)
    assert done.state == 'failed'
    assert done.reason == {'validate': 'artifacts_invalid', 'quick': 'publication_invalid',
                           'expired': 'artifacts_expired'}[how]
    assert svc.current(pid, client='c') is None


@pytest.mark.parametrize('name,run,cancel,state', [
    ('cancel_flag', _accepted(None), True, 'cancelled'),
    ('job_cancelled', _ended(Lifecycle.CANCELLED), False, 'cancelled'),
    ('job_unavailable', _ended(Lifecycle.UNAVAILABLE), False, 'failed'),
    ('job_rejected', _ended(Lifecycle.COMPLETED, Outcome.REJECTED), False, 'rejected'),
])
def test_recover_other_terminal_jobs(host, clock, rs, lock, bundle, runs, name, run, cancel, state):
    pid = new_program(rs, host, 'c')
    rev = _running(host, rs, lock, bundle, runs, pid, 1, None, run, cancel=cancel)
    pub = FakePub(clock)
    svc = _service(host, clock, pub)
    assert _state(svc, pid, rev.revision_id).state == state
    assert svc.current(pid, client='c') is None
    assert _events(svc, pid)[-1] == ('recovered', rev.revision_id)


def test_recover_is_idempotent_and_leaves_terminal_and_unrelated_rows_alone(host, clock, rs, lock, bundle, runs):
    pid = new_program(rs, host, 'c')
    first = _published_head(host, rs, lock, bundle, pid, 1)
    _running(host, rs, lock, bundle, runs, pid, 2, first.revision_id, _accepted(bundle))
    pub = FakePub(clock)
    svc = _service(host, clock, pub)
    before = (_events(svc, pid), svc.history(pid, client='c'))
    calls = len(pub.calls)
    again = _service(host, clock, pub)
    assert (_events(again, pid), again.history(pid, client='c')) == before
    assert len(pub.calls) == calls  # nothing running any more: no validate, no quick_check


def test_recover_leaves_a_running_revision_whose_job_is_still_live(host, clock, rs, lock, bundle, runs):
    pid = new_program(rs, host, 'c')
    rev = _running(host, rs, lock, bundle, runs, pid, 1, None, _ended(Lifecycle.RUNNING))
    svc = _service(host, clock, FakePub(clock))
    assert _state(svc, pid, rev.revision_id).state == 'running'
    assert _events(svc, pid) == []


# ====================================================================== native: world with restart

async def open_world(root, evals, occt, *, service_cls=RevisionService):
    from engineering.briefs import BriefService
    from engineering.jobs import JobController
    from engineering.store import SQLiteBriefRepository, SQLiteStore
    w = World()
    w.root = root
    w.store = SQLiteStore(root)
    try:
        w.pub = _publisher_class()(w.store)
        w.briefs = BriefService(SQLiteBriefRepository(w.store))
        w.replay = _replay(evals)
        w.jobs = JobController(w.store, publisher=w.pub, adapter=w.replay)
    except BaseException:
        w.store.close()
        raise
    try:
        w.svc = service_cls(w.store, w.jobs, w.pub, w.briefs, adapter_factory=lambda backend, mode='native': occt)
    except BaseException:
        await w.jobs.close()
        w.store.close()
        raise
    w.pid = None
    return w


async def close_world(w):
    await w.svc.close()  # first: the settle tasks must not run once the jobs are cancelled
    await w.jobs.close()
    w.store.close()


async def restart(w, evals, occt):
    pid, root = w.pid, w.root
    await close_world(w)
    w2 = await open_world(root, evals, occt)
    w2.pid = pid
    return w2


async def fresh(tmp_path, evals, occt):
    w = await open_world(tmp_path, evals, occt)
    w.pid = w.svc.create_program(CLIENT)
    return w


def events(w):
    return [(e.event, e.revision_id) for e in w.svc.journal(w.pid, client=CLIENT)]


def revision(w, rid):
    return w.svc.view(rid, client=CLIENT).revision


def head(w):
    view = w.svc.current(w.pid, client=CLIENT)
    return None if view is None else view.revision.revision_id


async def finish_job_without_settling(w, rev, brief_id):
    """Run a revision to a completed job but leave the revision running (settle is a no-op meanwhile)."""
    w.svc.settle = lambda rid, **kwargs: None
    try:
        await start_run(w, rev, brief_id)
        await w.svc.wait(rev.revision_id)
    finally:
        del w.svc.settle
    assert revision(w, rev.revision_id).state == 'running'


# ====================================================================== native: in-process restart

@native_only
def test_restart_staged_without_intent_stays_and_with_intent_fails_interrupted(tmp_path, evals, occt):
    async def scenario():
        w = await fresh(tmp_path, evals, occt)
        try:
            first, _ = await with_first(w)
            quiet, _ = await stage_final(w, WHITESPACE, first.revision_id)
            intent, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
            with w.store.transaction() as db:
                w.svc.revision_store.append_journal(db, w.pid, intent.revision_id, 'admit_intent',
                                                    {'brief_id': brief_id})
            jobs_before = table_counts(w)[3]
            w = await restart(w, evals, occt)
            assert revision(w, quiet.revision_id).state == 'staged'
            failed = revision(w, intent.revision_id)
            assert failed.state == 'failed' and failed.reason == 'interrupted' and failed.job_id is None
            assert head(w) == first.revision_id
            assert ('recovered', intent.revision_id) in events(w)
            assert ('recovered', quiet.revision_id) not in events(w)
            assert type(w.replay).calls == 0 and table_counts(w)[3] == jobs_before  # no native work, no admit
        finally:
            await close_world(w)
    asyncio.run(scenario())


@native_only
@pytest.mark.parametrize('how,state,reason', [('cancelled', 'cancelled', 'cancelled'),
                                              ('unavailable', 'failed', 'job_unavailable')])
def test_restart_running_revision_with_an_interrupted_job(tmp_path, evals, occt, how, state, reason):
    from engineering.jobs import TERMINAL, parse_run
    from engineering.store import SQLiteStore

    async def scenario():
        w = await fresh(tmp_path, evals, occt)
        try:
            first, sha = await with_first(w)
            w.replay.delay = 10
            rev, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
            running = await start_run(w, rev, brief_id)
            await asyncio.sleep(0.5)
            live = w.jobs.read(running.job_id)
            assert live.lifecycle not in TERMINAL
            await close_world(w)  # a graceful close cancels the job
            if how == 'unavailable':  # make it look like a killed process: the live record, not cancelled
                store = SQLiteStore(tmp_path)
                with store.transaction() as db:
                    db.execute('UPDATE jobs SET record=?, cancel=0 WHERE id=?', (live.model_dump_json(), running.job_id))
                    assert parse_run(db.execute('SELECT record FROM jobs WHERE id=?',
                                                (running.job_id,)).fetchone()['record']).lifecycle not in TERMINAL
                store.close()
            w = await open_world(tmp_path, evals, occt)
            w.pid = rev.program_id
            done = revision(w, rev.revision_id)
            assert done.state == state and done.reason == reason and done.published_seq is None
            assert head(w) == first.revision_id and step_sha(w, first.revision_id) == sha
            assert events(w)[-1] == ('recovered', rev.revision_id)
        finally:
            await close_world(w)
    asyncio.run(scenario())


@native_only
def test_restart_rolls_forward_a_job_that_completed_before_the_crash(tmp_path, evals, occt):
    async def scenario():
        w = await fresh(tmp_path, evals, occt)
        try:
            first, _ = await with_first(w)
            rev, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
            await finish_job_without_settling(w, rev, brief_id)
            assert head(w) == first.revision_id
            jobs_before = table_counts(w)[3]
            w = await restart(w, evals, occt)
            done = revision(w, rev.revision_id)
            assert done.state == 'published' and done.published_seq == 2  # CAS success
            view = w.svc.current(w.pid, client=CLIENT)
            assert view.revision.revision_id == rev.revision_id and view.artifacts == 'available'
            recorded = next(p.sha256 for p in done.bundle.payloads if p.path == 'model.step')
            assert step_sha(w, rev.revision_id) == recorded
            assert events(w)[-2:] == [('published', rev.revision_id), ('recovered', rev.revision_id)]
            assert type(w.replay).calls == 0 and table_counts(w)[3] == jobs_before
        finally:
            await close_world(w)
    asyncio.run(scenario())


@native_only
def test_restart_conflicts_when_a_sibling_moved_the_head_meanwhile(tmp_path, evals, occt):
    async def scenario():
        w = await fresh(tmp_path, evals, occt)
        try:
            first, sha = await with_first(w)
            a, a_brief = await stage_final(w, WHITESPACE, first.revision_id)
            c, c_brief = await stage_final(w, WHITESPACE, first.revision_id)
            await finish_job_without_settling(w, a, a_brief)
            winner = await land_staged(w, c, c_brief)
            assert winner.state == 'published'
            w = await restart(w, evals, occt)
            lost = revision(w, a.revision_id)
            assert lost.state == 'conflict' and lost.reason == 'stale_parent' and lost.published_seq is None
            assert head(w) == c.revision_id  # CAS conflict: the head did not move for a
            assert step_sha(w, first.revision_id) == sha
            assert events(w)[-2:] == [('conflict', a.revision_id), ('recovered', a.revision_id)]
        finally:
            await close_world(w)
    asyncio.run(scenario())


@native_only
@pytest.mark.parametrize('kind', ['staged_sibling', 'foreign_published', 'unknown'])
def test_restart_refuses_a_head_that_is_not_a_published_revision_of_its_program(tmp_path, evals, occt, kind):
    async def scenario():
        w = await fresh(tmp_path, evals, occt)
        closed = False
        try:
            first, _ = await with_first(w)
            pid = w.pid
            intent, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
            with w.store.transaction() as db:
                w.svc.revision_store.append_journal(db, pid, intent.revision_id, 'admit_intent', {})
            if kind == 'staged_sibling':
                bad = intent.revision_id
            elif kind == 'unknown':
                bad = hexid(0xBAD)
            else:
                w.pid = w.svc.create_program(CLIENT)
                bad = (await land(w, BEAM, None)).revision_id
                w.pid = pid
            with w.store.transaction() as db:
                db.execute('UPDATE programs SET head=? WHERE id=?', (bad, pid))
            await close_world(w)
            closed = True
            with pytest.raises(RuntimeError):
                await open_world(tmp_path, evals, occt)
            from engineering.store import SQLiteStore
            store = SQLiteStore(tmp_path)
            with store.transaction() as db:  # fail closed: the interrupted revision was NOT recovered
                assert db.execute('SELECT state FROM program_revisions WHERE id=?',
                                  (intent.revision_id,)).fetchone()[0] == 'staged'
                assert db.execute("SELECT COUNT(*) FROM program_journal WHERE event='recovered'").fetchone()[0] == 0
            store.close()
        finally:
            if not closed:
                await close_world(w)
    asyncio.run(scenario())


# ====================================================================== native: real os._exit crashes

def _capture():
    """The task 4 ``evals`` fixture body, as a plain function (used when pickling is impossible)."""
    from engineering.briefs import BriefService, MemoryBriefRepository
    from engineering.language.semantic import compile_source
    from engineering.search import search
    out = []
    for path, expected in ((P03 / 'beam.mir', 'selected'), (F06 / 'beam_5_10.mir', 'rejected')):
        compiled_ = compile_source(path.read_bytes(), source_id='capture.mir')
        svc = BriefService(MemoryBriefRepository())
        prepared = svc.create(json.loads(compiled_.brief_content.model_dump_json()))
        brief = _finalize_brief(svc, SimpleNamespace(
            draft_id=prepared.draft.draft_id, draft_revision=prepared.draft.revision,
            brief_sha256=prepared.draft.content.brief_sha256))
        result = asyncio.run(search(brief))
        assert result.state == expected
        out.extend(result.evaluations)
    return tuple(out)


def _load_evals(path):
    import pickle
    try:
        with open(path, 'rb') as handle:
            return pickle.load(handle)
    except Exception:
        return _capture()


async def crash_main(root, seam, evals_path):
    """Subprocess body: land rev1, stage rev2, then die with os._exit(17) at ``seam`` while rev2 runs."""
    root = Path(root)
    evals = _load_evals(evals_path)
    from engineering.program.adapters.occt import OcctAdapter
    occt = OcctAdapter()
    armed = {'on': False}

    class Crashing(RevisionService):
        def _crash_point(self, name):
            if armed['on'] and name == seam:
                os._exit(17)

    w = await open_world(root, evals, occt, service_cls=Crashing)
    w.pid = w.svc.create_program(CLIENT)
    first, sha = await with_first(w)
    if seam == 'publish_after_replace':
        real = os.replace

        def replace(src, dst, *args, **kwargs):
            real(src, dst, *args, **kwargs)
            if armed['on'] and str(w.pub.staging) in str(src):
                os._exit(17)  # the directory is in place, the publications row is not
        os.replace = replace
    rev, brief_id = await stage_final(w, WHITESPACE, first.revision_id)
    (root / 'state.json').write_text(json.dumps(
        dict(pid=w.pid, first=first.revision_id, second=rev.revision_id, sha=sha)), encoding='utf-8')
    armed['on'] = True
    await w.svc.run(rev.revision_id, brief_id, client=CLIENT)
    await w.svc.wait(rev.revision_id)
    await asyncio.sleep(2)
    os._exit(3)  # the seam never fired


@pytest.fixture(scope='module')
def evals_file(evals, tmp_path_factory):
    import pickle
    path = tmp_path_factory.mktemp('f06crash') / 'evals.pkl'
    try:
        path.write_bytes(pickle.dumps(evals))
    except Exception:
        pass  # the subprocess reruns the capture
    return path


# seam -> (state of rev2, reason, rev2 is the head afterwards)
CRASHES = {
    'after_admit_before_link': ('failed', 'interrupted', False),
    'after_link_before_start': ('failed', 'job_unavailable', False),
    'before_settle_commit': ('published', None, True),
    'publish_after_replace': ('failed', 'job_unavailable', False),
}


@native_only
@pytest.mark.parametrize('seam', list(CRASHES))
def test_real_crash_at_each_seam_recovers_on_restart(tmp_path, evals, occt, evals_file, seam):
    script = ('import asyncio, sys\n'
              'from tests.test_program_revisions_crash import crash_main\n'
              'asyncio.run(crash_main(sys.argv[1], sys.argv[2], sys.argv[3]))\n')
    env = dict(os.environ, PYTHONPATH=f'{ROOT / "backend"}{os.pathsep}{ROOT}')
    result = subprocess.run([sys.executable, '-c', script, str(tmp_path), seam, str(evals_file)],
                            timeout=240, capture_output=True, env=env, cwd=str(ROOT))
    assert result.returncode == 17, result.stderr.decode(errors='replace')[-2000:]
    state = json.loads((tmp_path / 'state.json').read_text(encoding='utf-8'))
    want_state, want_reason, rolled = CRASHES[seam]

    async def scenario():
        w = await open_world(tmp_path, evals, occt)
        w.pid = state['pid']
        try:
            history = w.svc.history(w.pid, client=CLIENT)
            assert {r.revision_id for r in history} == {state['first'], state['second']}
            for rev in history:  # terminal, or staged without a link
                assert rev.state in TERMINAL_STATES or (rev.state == 'staged' and rev.job_id is None), rev
            second = revision(w, state['second'])
            assert second.state == want_state and second.reason == want_reason
            assert list(w.pub.staging.iterdir()) == []
            seqs = [e.seq for e in w.svc.journal(w.pid, client=CLIENT)]
            assert seqs == sorted(set(seqs))  # strictly increasing
            assert ('recovered', state['second']) in events(w)
            if rolled:
                assert head(w) == state['second'] and second.published_seq == 2
                recorded = next(p.sha256 for p in second.bundle.payloads if p.path == 'model.step')
                assert step_sha(w, state['second']) == recorded
                assert w.svc.current(w.pid, client=CLIENT).artifacts == 'available'
            else:
                assert head(w) == state['first'] and second.published_seq is None
                assert step_sha(w, state['first']) == state['sha']
                if second.job_id is not None:
                    assert not w.pub.directory(second.job_id).exists()
            assert type(w.replay).calls == 0  # recovery never starts native work
        finally:
            await close_world(w)
    asyncio.run(scenario())


# ====================================================================== native: cancel versus settle

@native_only
def test_threaded_cancel_versus_settle_has_exactly_one_terminal_outcome(tmp_path, evals, occt):
    terminal = ('published', 'rejected', 'failed', 'cancelled', 'conflict')

    async def scenario():
        w = await fresh(tmp_path, evals, occt)
        try:
            parent = (await with_first(w))[0].revision_id
            outcomes = set()
            for _ in range(4):
                rev, brief_id = await stage_final(w, WHITESPACE, parent)
                await finish_job_without_settling(w, rev, brief_id)
                gate = threading.Barrier(4)

                def settle(_):
                    gate.wait()
                    return w.svc.settle(rev.revision_id)

                def cancel(_):
                    gate.wait()
                    return w.svc._cancel_sync(rev.revision_id, client=CLIENT)

                with ThreadPoolExecutor(4) as pool:
                    futures = [pool.submit(settle, 0), pool.submit(cancel, 1),
                               pool.submit(settle, 2), pool.submit(cancel, 3)]
                    results = [f.result(timeout=60) for f in futures]
                final = revision(w, rev.revision_id)
                assert final.state in ('published', 'cancelled'), final
                assert all(r.state in ('running', 'published', 'cancelled') for r in results)
                mine = [e.event for e in w.svc.journal(w.pid, client=CLIENT) if e.revision_id == rev.revision_id]
                assert len([e for e in mine if e in terminal]) == 1, mine
                assert mine[0] == 'staged' and mine.count('published') == (final.state == 'published')
                if final.state == 'published':
                    assert head(w) == rev.revision_id and final.published_seq is not None
                    parent = rev.revision_id
                else:
                    assert head(w) == parent and final.published_seq is None
                outcomes.add(final.state)
            seqs = [e.seq for e in w.svc.journal(w.pid, client=CLIENT)]
            assert seqs == sorted(set(seqs))
            assert outcomes <= {'published', 'cancelled'}
        finally:
            await close_world(w)
    asyncio.run(scenario())


def test_this_module_is_ascii_lf():
    text = Path(__file__).read_bytes()
    assert text.isascii() and b'\r' not in text
