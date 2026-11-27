"""N1c-3a: carried LOW fixes from the PR #156 review (backend allow-list, fail-closed ownership checks)."""
from __future__ import annotations

import asyncio
import os

import pytest

from tests.test_program_n1c2_idempotency import build, close
from tests.test_program_n1c2_adapter import adapter_of, make_fake, run  # noqa: F401  (fixtures below)
from tests.test_program_n1c2_adapter import kernel_enabled  # noqa: F401


@pytest.mark.parametrize('backend', ['picogk', 'OCCT', '', 'native', None, 'mirum-native ', 7])
def test_admit_refuses_an_unknown_backend_before_touching_the_store(tmp_path, backend):
    store, brief, jobs = build(tmp_path)
    with pytest.raises(ValueError, match='backend'):
        jobs.admit(brief.brief_id, 'k', 'alice', backend=backend)
    assert store.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
    close(store, jobs)


@pytest.mark.parametrize('backend', ['occt', 'mirum-native'])
def test_admit_accepts_the_two_registered_backends(tmp_path, backend):
    store, brief, jobs = build(tmp_path)
    admitted = jobs.admit(brief.brief_id, 'k', 'alice', backend=backend)
    assert admitted.backend == backend
    close(store, jobs, admitted)


@pytest.mark.skipif(os.name != 'posix', reason='ProcessRunner needs posix process groups')
@pytest.mark.parametrize('missing', ['absent', 'none'])
def test_probe_fails_closed_without_geteuid(tmp_path, monkeypatch, missing):
    import engineering.program.adapters.native as native
    binary, pin = make_fake(tmp_path / 'b')
    assert run(adapter_of(binary, pin).probe()).available  # control: passes with geteuid
    if missing == 'absent':
        monkeypatch.delattr(native.os, 'geteuid')
    else:
        monkeypatch.setattr(native.os, 'geteuid', None)
    assert run(adapter_of(binary, pin).probe()).reasons == ('probe_failed',)


def test_module_documents_the_group_writable_parent_trade_off():
    import engineering.program.adapters.native as native
    assert 'group-writable' in native.__doc__ and 'availability' in native.__doc__.lower()
    assert '0775' in native.__doc__
