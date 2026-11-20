"""Runtime-limit source citations in the REGEN descriptor must point at the real lines."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_rlimit_as_citation_points_at_the_setrlimit_line():
    from engineering.capabilities import REGEN
    ref = next(r for r in REGEN.runtime_limits if r.name == 'worker.RLIMIT_AS_bytes')
    assert ref.value == 4 << 30
    path, line = re.fullmatch(r'(.+):(\d+)', ref.source).groups()
    text = (ROOT / path).read_text(encoding='utf-8').splitlines()[int(line) - 1]
    assert 'setrlimit(resource.RLIMIT_AS' in text and '4 << 30' in text
