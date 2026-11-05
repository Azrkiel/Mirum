import hashlib
import json
from pathlib import Path
import sys

import pytest
sys.path.insert(0, str(Path(__file__).parent))
from dragster_state import DragsterState


def fixture():
    files = dict(step=b'step', glb=b'glb', spec=b'{"schema_version":1}')
    report = dict(passed=True, revision_id='revision', spec_sha256=hashlib.sha256(files['spec']).hexdigest(),
                  step_sha256=hashlib.sha256(files['step']).hexdigest(),
                  preview=dict(glb_sha256=hashlib.sha256(files['glb']).hexdigest()))
    files['report'] = json.dumps(report).encode()
    event = dict(status='done', revision_id='revision', run_id='revision', spec=json.loads(files['spec']),
                 report=report, digests={fmt:hashlib.sha256(data).hexdigest() for fmt,data in files.items()})
    return event, files


def test_failure_and_stale_events_preserve_entire_accepted_revision():
    state = DragsterState()
    event, files = fixture()
    first = state.begin()
    assert state.accept(first, event, files)
    newer = state.begin()
    assert not state.accept(first, event, {})
    state.fail(first, 'old error')
    assert state.error is None
    state.fail(newer, 'cancelled')
    assert state.error == 'cancelled'
    assert state.accepted is event and state.files is files


def test_first_failure_and_incomplete_or_corrupted_download_preserve_state():
    state = DragsterState()
    attempt = state.begin()
    state.fail(attempt, 'bad spec')
    assert state.accepted is None
    event, files = fixture()
    assert state.accept(attempt, event, files)
    for changed in ({**files,'step':b'corrupted'}, {'glb':files['glb']}):
        with pytest.raises(ValueError):
            state.accept(state.begin(), event, changed)
        assert state.files is files
