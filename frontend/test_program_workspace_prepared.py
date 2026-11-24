"""M1 second review (L1): a prepared large artifact survives the rerun a download click causes."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent))

import program_workspace as pw  # noqa: E402

ID = ('a' * 32, 'model.step', 'f' * 64)


def test_small_artifacts_always_fetch_and_never_remember():
    state = {}
    assert pw._should_fetch(state, ('a' * 32, 'report.json', 'e' * 64), clicked=False) is True
    assert state == {}


def test_a_large_artifact_waits_for_a_click_then_survives_reruns():
    state = {}
    assert pw._should_fetch(state, ID, clicked=False) is False
    assert pw._should_fetch(state, ID, clicked=True) is True
    assert pw._should_fetch(state, ID, clicked=False) is True          # the rerun the download click triggers
    assert state['mel_prepared'] == ID                                  # identity only, no bytes


def test_selecting_another_artifact_drops_the_preparation():
    state = {}
    pw._should_fetch(state, ID, clicked=True)
    other = ('a' * 32, 'package.zip', '9' * 64)
    assert pw._should_fetch(state, other, clicked=False) is False
    assert pw._should_fetch(state, ID, clicked=False) is False
