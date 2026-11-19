"""Publish-time validation once, stat-only status checks, one integrity check on download,
and explicit lease release (including aborted downloads). Reuses real native evidence."""
import asyncio
import pathlib
import threading

import pytest

from backend.tests.test_engineering_bundles import native, publisher  # noqa: F401  (fixture + helper)

JOB = 'a' * 32


def published(tmp_path, native):
    store, pub = publisher(tmp_path)
    brief, result = native
    bundle = asyncio.run(pub.publish(JOB, brief, result))
    return store, pub, bundle


def test_publish_validates_fully_once_and_off_the_event_loop(tmp_path, native, monkeypatch):
    store, pub = publisher(tmp_path)
    brief, result = native
    original = pub.validate
    seen = []
    def spy(*args, **kwargs):
        seen.append(threading.current_thread() is threading.main_thread())
        return original(*args, **kwargs)
    monkeypatch.setattr(pub, 'validate', spy)
    asyncio.run(pub.publish(JOB, brief, result))
    assert seen == [False]
    store.close()


def test_status_check_reads_no_file_content(tmp_path, native, monkeypatch):
    store, pub, bundle = published(tmp_path, native)
    def forbidden(*args, **kwargs):
        raise AssertionError('status polling must not read package bytes')
    monkeypatch.setattr(pub, 'validate', forbidden)
    monkeypatch.setattr(pathlib.Path, 'read_bytes', forbidden)
    monkeypatch.setattr(pathlib.Path, 'read_text', forbidden)
    assert pub.quick_check(JOB, bundle)['kind'] == 'accepted'
    store.close()


def test_quick_check_still_catches_size_and_layout_changes(tmp_path, native):
    store, pub, bundle = published(tmp_path, native)
    path = pub.directory(JOB) / 'report.json'
    path.write_bytes(path.read_bytes() + b'x')
    with pytest.raises(ValueError):
        pub.quick_check(JOB, bundle)
    store.close()


def test_same_size_tamper_passes_status_but_never_downloads(tmp_path, native):
    store, pub, bundle = published(tmp_path, native)
    path = pub.directory(JOB) / 'package.zip'
    data = bytearray(path.read_bytes())
    data[-1] ^= 0xFF                      # same length, different digest
    path.write_bytes(bytes(data))
    pub.quick_check(JOB, bundle)          # cheap status path cannot see it
    with pytest.raises(ValueError):       # the single download-time integrity check does
        with pub.lease(JOB, 'zip', envelope=bundle):
            pass
    assert not pub.leases.get(JOB)        # a refused download leaves no lease behind
    with pub.lease(JOB, 'brief', envelope=bundle) as served:   # untouched files still serve
        assert served.file.read(1)
    store.close()


def test_download_lease_release_is_explicit_and_idempotent(tmp_path, native):
    store, pub, bundle = published(tmp_path, native)
    download = pub.open_lease(JOB, 'zip', envelope=bundle)
    assert pub.leases[JOB] == 1
    download.close(); download.close()
    assert pub.leases[JOB] == 0
    store.close()


def test_aborted_download_releases_the_lease(tmp_path, native):
    from engineering.api_v1 import LeasedResponse
    store, pub, bundle = published(tmp_path, native)
    download = pub.open_lease(JOB, 'zip', envelope=bundle)
    response = LeasedResponse(download, media_type=download.mime_type,
                              headers={'Content-Length': str(download.size)})
    assert pub.leases[JOB] == 1
    async def disconnect_after_headers():
        async def receive():
            return {'type': 'http.disconnect'}
        async def send(message):
            if message['type'] == 'http.response.body':
                raise OSError('client went away')
        scope = {'type': 'http', 'asgi': {'spec_version': '2.3'}, 'method': 'GET', 'headers': []}
        try:
            await response(scope, receive, send)
        except BaseException as error:        # the abort may surface as OSError, a group, or a cancel
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
    asyncio.run(disconnect_after_headers())
    assert pub.leases[JOB] == 0
    pub.cleanup()                         # nothing pins the package any more
    store.close()


def test_closing_a_download_never_waits_for_the_store_lock(tmp_path, native):
    store, pub, bundle = published(tmp_path, native)
    download = pub.open_lease(JOB, 'zip', envelope=bundle)
    held, done = threading.Event(), threading.Event()
    def hold_store_lock():
        with store._lock:
            held.set()
            done.wait(10)
    holder = threading.Thread(target=hold_store_lock); holder.start()
    assert held.wait(5)
    closer = threading.Thread(target=download.close); closer.start()
    closer.join(2)
    finished = not closer.is_alive()
    done.set(); holder.join(); closer.join()
    assert finished and pub.leases[JOB] == 0
    store.close()


def test_concurrent_double_close_cannot_double_decrement_and_pin_or_free_wrongly(tmp_path, native):
    store, pub, bundle = published(tmp_path, native)
    first = pub.open_lease(JOB, 'zip', envelope=bundle)
    second = pub.open_lease(JOB, 'glb', envelope=bundle)
    assert pub.leases[JOB] == 2
    threads = [threading.Thread(target=first.close) for _ in range(8)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert pub.leases[JOB] == 1          # exactly one release from the eight racing closes
    second.close()
    second.close()
    assert pub.leases[JOB] == 0          # never negative
    store.close()


def test_response_never_started_is_released_by_closing(tmp_path, native):
    store, pub, bundle = published(tmp_path, native)
    download = pub.open_lease(JOB, 'glb', envelope=bundle)
    download.close()
    assert pub.leases[JOB] == 0
    store.close()
