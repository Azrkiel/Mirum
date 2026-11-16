"""Rate-limit key: trusted X-Mirum-Client-IP only when explicitly enabled."""
import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import client_key  # noqa: E402


def _req(headers=None, peer="10.0.0.9"):
    client = SimpleNamespace(host=peer) if peer else None
    return SimpleNamespace(headers=headers or {}, client=client)


@pytest.fixture
def trusted(monkeypatch):
    monkeypatch.setattr(client_key.config, "TRUSTED_CLIENT_HEADER", True)


@pytest.fixture
def untrusted(monkeypatch):
    monkeypatch.setattr(client_key.config, "TRUSTED_CLIENT_HEADER", False)


def test_setting_off_ignores_header(untrusted):
    assert client_key.client_key(_req({"X-Mirum-Client-IP": "203.0.113.7"})) == "10.0.0.9"


def test_on_valid_ipv4(trusted):
    assert client_key.client_key(_req({"X-Mirum-Client-IP": "203.0.113.7"})) == "203.0.113.7"


def test_on_valid_ipv6_is_normalised(trusted):
    got = client_key.client_key(_req({"X-Mirum-Client-IP": "2001:0DB8:0:0:0:0:0:1"}))
    assert got == "2001:db8::1"


def test_on_header_name_is_case_insensitive_via_headers_mapping(trusted):
    Headers = pytest.importorskip("starlette.datastructures").Headers
    req = SimpleNamespace(headers=Headers({"x-mirum-client-ip": "198.51.100.4"}),
                          client=SimpleNamespace(host="10.0.0.9"))
    assert client_key.client_key(req) == "198.51.100.4"


def test_on_missing_header_falls_back(trusted):
    assert client_key.client_key(_req()) == "10.0.0.9"


@pytest.mark.parametrize("value", [
    "garbage", "", "  ", "1.2.3", "999.1.1.1", "1.2.3.4, 5.6.7.8", "1.2.3.4,5.6.7.8",
    "1.2.3.4:80", "a" * 500, "1" * 100, "::1%eth0", "\x00", "1.2.3.4\n5.6.7.8",
])
def test_on_bad_values_fall_back(trusted, value):
    assert client_key.client_key(_req({"X-Mirum-Client-IP": value})) == "10.0.0.9"


def test_surrounding_whitespace_is_accepted(trusted):
    assert client_key.client_key(_req({"X-Mirum-Client-IP": " 203.0.113.7 "})) == "203.0.113.7"


def test_no_client_falls_back_to_slowapi_default(untrusted):
    assert client_key.client_key(_req(peer=None)) == "127.0.0.1"


def test_never_raises(trusted):
    class Boom:
        @property
        def headers(self):
            raise RuntimeError("boom")

        client = None

    assert client_key.client_key(Boom()) == "127.0.0.1"
    assert client_key.client_key(object()) == "127.0.0.1"
    assert client_key.client_key(None) == "127.0.0.1"


def test_fallback_matches_slowapi():
    util = pytest.importorskip("slowapi.util")
    for req in (_req(), _req(peer=None)):
        assert client_key.peer_address(req) == util.get_remote_address(req)


def test_setting_default_is_off(monkeypatch):
    import importlib
    import config
    monkeypatch.delenv("MIRUM_TRUSTED_CLIENT_HEADER", raising=False)
    assert importlib.reload(config).TRUSTED_CLIENT_HEADER is False
    monkeypatch.setenv("MIRUM_TRUSTED_CLIENT_HEADER", "1")
    assert importlib.reload(config).TRUSTED_CLIENT_HEADER is True
    monkeypatch.setenv("MIRUM_TRUSTED_CLIENT_HEADER", "yes-please")
    assert importlib.reload(config).TRUSTED_CLIENT_HEADER is False
    monkeypatch.delenv("MIRUM_TRUSTED_CLIENT_HEADER")
    importlib.reload(config)
