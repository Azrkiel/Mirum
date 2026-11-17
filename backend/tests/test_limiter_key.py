"""The app limiter keys on client_key, not the raw transport peer."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def test_limiter_uses_client_key():
    pytest.importorskip("slowapi")
    pytest.importorskip("cadquery")
    import main
    from client_key import client_key
    assert main.limiter._key_func is client_key


def test_main_source_wires_client_key():
    # Source-level guard that runs without FastAPI/slowapi installed.
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "main.py"),
               encoding="utf-8").read()
    assert "Limiter(key_func=client_key)" in src
    assert "get_remote_address" not in src
