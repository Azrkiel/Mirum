"""Tests for backend/telemetry.record_phase_timing + phase_timer (A-09).

Pure-Python; runnable via `python backend/tests/test_telemetry_phase.py`.
We patch `telemetry._logger.info` to avoid touching the real JSONL log
file under /tmp/mirum/telemetry on every test run, and to assert the
exact JSON shape the recorder emits."""
from __future__ import annotations

import json
import os
import sys
import time
import unittest.mock
from pathlib import Path

# Telemetry doesn't require a Gemini key, but importing main does;
# tests in this directory keep the placeholder in case future helpers
# pull main in transitively.
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-placeholder-key")

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))


def _captured_records(call_args_list) -> list[dict]:
    """Decode every JSON string passed to _logger.info into a dict."""
    records = []
    for call in call_args_list:
        # _logger.info(json.dumps(record)) → call.args = (json_str,)
        if call.args:
            records.append(json.loads(call.args[0]))
    return records


def test_record_phase_timing_emits_canonical_fields() -> None:
    import telemetry
    with unittest.mock.patch.object(telemetry._logger, "info") as info:
        telemetry.record_phase_timing("job-abc", "planner", 1234.567)
    records = _captured_records(info.call_args_list)
    assert len(records) == 1
    r = records[0]
    assert r["event_type"] == "phase_timing"
    assert r["job_id"] == "job-abc"
    assert r["phase"] == "planner"
    assert r["elapsed_ms"] == 1234.57  # rounded to 2 dp
    assert "timestamp" in r and isinstance(r["timestamp"], (int, float))


def test_record_phase_timing_extra_merges_without_clobber() -> None:
    """`extra` should add fields like part_id / attempt, but MUST NOT be
    able to overwrite the canonical fields (event_type, job_id, phase,
    elapsed_ms, timestamp). Otherwise a caller could silently corrupt
    the analytics schema."""
    import telemetry
    with unittest.mock.patch.object(telemetry._logger, "info") as info:
        telemetry.record_phase_timing(
            "job-1", "machinist", 50.0,
            extra={
                "part_id": "p1",
                "attempt": 2,
                "phase": "tampered",        # MUST be ignored
                "event_type": "tampered",   # MUST be ignored
                "job_id": "tampered",       # MUST be ignored
            },
        )
    r = _captured_records(info.call_args_list)[0]
    assert r["phase"] == "machinist", f"extra clobbered phase: {r}"
    assert r["event_type"] == "phase_timing"
    assert r["job_id"] == "job-1"
    assert r["part_id"] == "p1"
    assert r["attempt"] == 2


def test_phase_timer_records_elapsed_on_clean_exit() -> None:
    import telemetry
    with unittest.mock.patch.object(telemetry._logger, "info") as info:
        with telemetry.phase_timer("job-2", "rea"):
            time.sleep(0.05)
    r = _captured_records(info.call_args_list)[0]
    assert r["phase"] == "rea"
    assert r["elapsed_ms"] >= 40, (
        f"expected >= 40ms (slept 50ms), got {r['elapsed_ms']}ms"
    )
    assert "failed" not in r, "clean exit must not flag failed"


def test_phase_timer_records_elapsed_and_failed_on_exception() -> None:
    """Failures must still be measurable — otherwise we can't distinguish
    'phase didn't run' from 'phase ran and crashed at 30s'."""
    import telemetry
    with unittest.mock.patch.object(telemetry._logger, "info") as info:
        try:
            with telemetry.phase_timer("job-3", "compiler"):
                time.sleep(0.02)
                raise RuntimeError("boom")
        except RuntimeError:
            pass  # exception correctly propagates
    r = _captured_records(info.call_args_list)[0]
    assert r["phase"] == "compiler"
    assert r["elapsed_ms"] >= 15
    assert r["failed"] is True
    assert r["exc_type"] == "RuntimeError"


def test_phase_timer_propagates_exception() -> None:
    """A timer that swallowed the body's exception would mask real bugs.
    Asserting the exception still escapes the with-block guards against
    a refactor that returned True from __exit__."""
    import telemetry
    raised = False
    try:
        with telemetry.phase_timer("job-4", "export"):
            raise ValueError("escape me")
    except ValueError:
        raised = True
    assert raised, "phase_timer must NOT suppress the body's exception"


def test_record_phase_timing_swallows_logger_failure() -> None:
    """Telemetry is analytics-only: a broken logger must never break the
    pipeline. Patch _logger.info to raise; the recorder must silently
    drop the write (and route the secondary failure to debug log)."""
    import telemetry
    with unittest.mock.patch.object(
        telemetry._logger, "info", side_effect=RuntimeError("disk full")
    ):
        # No exception escapes despite the underlying handler raising.
        telemetry.record_phase_timing("job-x", "planner", 1.0)


def _run_all() -> int:
    funcs = [
        test_record_phase_timing_emits_canonical_fields,
        test_record_phase_timing_extra_merges_without_clobber,
        test_phase_timer_records_elapsed_on_clean_exit,
        test_phase_timer_records_elapsed_and_failed_on_exception,
        test_phase_timer_propagates_exception,
        test_record_phase_timing_swallows_logger_failure,
    ]
    failures = 0
    for fn in funcs:
        try:
            fn()
        except AssertionError as e:
            failures += 1
            print(f"FAIL {fn.__name__}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {fn.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS {fn.__name__}")
    print(f"\n{len(funcs) - failures}/{len(funcs)} passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(_run_all())
