"""Native Linux regressions for bounded CAD execution and disconnect cleanup."""
import asyncio
import os
import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compiler import execute_cad_script

pytestmark = pytest.mark.skipif(os.name != "posix" or not Path('/proc/self').exists(), reason="Linux process lifecycle")


def children():
    found = set()
    for path in Path('/proc').glob('[0-9]*/status'):
        try:
            fields = dict(line.split(':', 1) for line in path.read_text().splitlines() if ':' in line)
            if int(fields.get('PPid', -1)) == os.getpid(): found.add(int(path.parent.name))
        except (OSError, ValueError): pass
    return found


def test_stdout_is_bounded_and_overflow_is_not_success():
    result = asyncio.run(execute_cad_script("print('x' * 2097152)"))
    assert result['status'] == 'error'
    assert 'OUTPUT_LIMIT' in result['traceback']


def test_cancellation_drains_owned_cad_child_before_returning():
    async def scenario():
        before = children()
        task = asyncio.create_task(execute_cad_script('while True:\n    pass'))
        owned = set()
        try:
            for _ in range(500):
                owned = children() - before
                if owned: break
                await asyncio.sleep(0.01)
            assert owned, 'CAD child did not start'
            task.cancel()
            with pytest.raises(asyncio.CancelledError): await task
            assert not (children() & owned), 'Cancelled CAD work left its subprocess running'
        finally:
            for pid in children() - before:
                try: os.kill(pid, signal.SIGKILL)
                except ProcessLookupError: pass
            if not task.done(): task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
