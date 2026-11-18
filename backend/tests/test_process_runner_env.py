"""ProcessRunner must not leak secrets to children when env is omitted."""
import json
import os
import sys

import pytest

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(os.name != "posix", reason="POSIX process groups")]

DUMP = "import json, os; print(json.dumps(dict(os.environ)))"


async def run(env=None, **kw):
    from backend.process_runner import ProcessRunner
    runner = ProcessRunner(max_processes=1, timeout=20, **kw)
    try:
        out = await runner.run([sys.executable, "-c", DUMP], **({} if env is None else {"env": env}))
    finally:
        await runner.close()
    assert out["returncode"] == 0, out
    return json.loads(out["stdout"])


async def test_default_env_drops_secrets(monkeypatch):
    for name in ("GEMINI_API_KEY_HEAVY", "GOOGLE_API_KEY", "SOME_TOKEN", "DB_PASSWORD", "MY_SECRET",
                 "GEMINI_MODEL_X", "GOOGLE_APPLICATION_CREDENTIALS"):
        monkeypatch.setenv(name, "leak")
    monkeypatch.setenv("MIRUM_WORK_DIR", "/tmp/mirum/work")
    monkeypatch.setenv("MIRUM_API_KEY", "leak")
    child = await run()
    for name in ("GEMINI_API_KEY_HEAVY", "GOOGLE_API_KEY", "SOME_TOKEN", "DB_PASSWORD", "MY_SECRET",
                 "GEMINI_MODEL_X", "GOOGLE_APPLICATION_CREDENTIALS", "MIRUM_API_KEY"):
        assert name not in child
    assert "leak" not in json.dumps(child)
    assert "PATH" in child
    assert child["MIRUM_WORK_DIR"] == "/tmp/mirum/work"
    assert len(child["MIRUM_PROCESS_TOKEN"]) == 32


async def test_explicit_env_is_honoured():
    child = await run(env={"PATH": os.environ.get("PATH", ""), "CUSTOM_FLAG": "1"})
    assert child["CUSTOM_FLAG"] == "1"
    assert "MIRUM_PROCESS_TOKEN" in child


async def test_mirum_secret_like_names_dropped(monkeypatch):
    for name in ("MIRUM_DB_PASS", "MIRUM_DATABASE_URL", "MIRUM_X_DSN", "MIRUM_AUTH_HEADER",
                 "MIRUM_PRIVATE_BLOB", "MIRUM_SIGNING", "MIRUM_COOKIE_JAR", "MIRUM_SESSION_ID",
                 "MIRUM_CRED_FILE", "MIRUM_REDIS_URI"):
        monkeypatch.setenv(name, "leak")
    monkeypatch.setenv("MIRUM_WORK_DIR", "/tmp/mirum/work")
    child = await run()
    assert not [k for k in child if k.startswith("MIRUM_") and child[k] == "leak"]
    assert child["MIRUM_WORK_DIR"] == "/tmp/mirum/work"
    assert "MIRUM_PROCESS_TOKEN" in child
