"""Sandbox timeout env var is clamped to [5, 60] and tolerates garbage."""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _reload(value):
    if value is None:
        os.environ.pop("MIRUM_CADQUERY_TIMEOUT_S", None)
    else:
        os.environ["MIRUM_CADQUERY_TIMEOUT_S"] = value
    import config
    return importlib.reload(config).CADQUERY_TIMEOUT_S


def test_timeout_clamped():
    old = os.environ.get("MIRUM_CADQUERY_TIMEOUT_S")
    try:
        assert _reload(None) == 30
        assert _reload("0") == 5
        assert _reload("-10") == 5
        assert _reload("100000") == 60
        assert _reload("45") == 45
        assert _reload("not-a-number") == 30
    finally:
        _reload(old)


if __name__ == "__main__":
    test_timeout_clamped()
    print("ok")
