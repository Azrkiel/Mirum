"""Tests for scripts/run_release_md.py (extracts CI-executable blocks from RELEASE.md)."""
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("run_release_md", ROOT / "scripts" / "run_release_md.py")
rr = importlib.util.module_from_spec(spec)
sys.modules["run_release_md"] = rr
spec.loader.exec_module(rr)

FENCE = "`" * 3


def md(tmp_path, *blocks, text=None):
    """blocks: (info, body) pairs."""
    parts = ["# T\n"]
    for info, body in blocks:
        parts.append(f"{FENCE}{info}\n{body}\n{FENCE}\n")
    p = tmp_path / "R.md"
    p.write_text(text if text is not None else "\n".join(parts), encoding="utf-8", newline="\n")
    return p


def run_script(tmp_path, steps):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("no bash")
    f = tmp_path / "run.sh"
    f.write_text(rr.script(steps), encoding="utf-8", newline="\n")
    ev = tmp_path / "timing.json"
    env = {"RELEASE_WORK": str(tmp_path / "w"), "RELEASE_STEP_TIMING": str(ev), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([bash, str(f)], env=env, capture_output=True, text=True)
    return r, json.loads(ev.read_text())


def test_extract_in_order_and_skips_manual_and_other_fences(tmp_path):
    p = md(tmp_path,
           ("bash release-step=a", "echo a"),
           ("bash release-manual=m", "docker volume rm x"),
           ("yaml", "k: v"),
           ("bash release-step=b", "echo b\necho c"))
    assert rr.extract(p) == [("a", "echo a"), ("b", "echo b\necho c")]


def test_duplicate_id_rejected(tmp_path):
    p = md(tmp_path, ("bash release-step=a", "true"), ("bash release-step=a", "true"))
    with pytest.raises(ValueError, match="duplicate"):
        rr.extract(p)


def test_empty_block_rejected(tmp_path):
    p = md(tmp_path, ("bash release-step=a", "   "))
    with pytest.raises(ValueError, match="empty"):
        rr.extract(p)


def test_unterminated_fence_rejected(tmp_path):
    p = md(tmp_path, text=f"{FENCE}bash release-step=a\necho a\n")
    with pytest.raises(ValueError, match="unterminated"):
        rr.extract(p)


def test_unterminated_manual_fence_rejected(tmp_path):
    p = md(tmp_path, text=f"{FENCE}bash release-manual=a\necho a\n")
    with pytest.raises(ValueError, match="unterminated"):
        rr.extract(p)


@pytest.mark.parametrize("info", [
    "bash release-step=", "sh release-step=a", "bash release-step=a b", "bash release-step=a/b",
])
def test_malformed_step_info_rejected(tmp_path, info):
    with pytest.raises(ValueError):
        rr.extract(md(tmp_path, (info, "true")))


@pytest.mark.parametrize("code", [
    "docker compose down -v",
    "docker compose down --volumes",
    "docker volume rm x",
    "docker system prune -f",
    "set -x\ntrue",
    "set -euxo pipefail",
    "echo $MIRUM_ACCESS_TOKEN",
    'echo "$MIRUM_ACCESS_TOKEN"',
    'printf "%s\\n" "${MIRUM_ACCESS_TOKEN}"',
    "printenv MIRUM_ACCESS_TOKEN",
])
def test_forbidden_content_rejected(tmp_path, code):
    with pytest.raises(ValueError, match="forbidden"):
        rr.extract(md(tmp_path, ("bash release-step=a", code)))


def test_token_use_in_header_allowed(tmp_path):
    code = 'curl -H "X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN" http://x'
    assert rr.extract(md(tmp_path, ("bash release-step=a", code))) == [("a", code)]


def test_script_shape():
    s = rr.script([("one", "echo 1"), ("two", "echo 2")])
    assert s.startswith("#!/usr/bin/env bash\nset -euo pipefail\n")
    assert "=== release-step one ===" in s and "=== release-step two ===" in s
    assert s.index("echo 1") < s.index("echo 2")
    assert "set -x" not in s


def test_token_step_emits_mask_only_in_actions(tmp_path):
    steps = [("token", "MIRUM_ACCESS_TOKEN=abc123\nexport MIRUM_ACCESS_TOKEN"), ("after", "true")]
    s = rr.script(steps)
    assert s.count("::add-mask::") == 1
    assert s.index("::add-mask::") > s.index("export MIRUM_ACCESS_TOKEN")
    r, _ = run_script(tmp_path, steps)
    assert r.returncode == 0 and "abc123" not in r.stdout


def test_script_runs_in_one_session_and_writes_timing(tmp_path):
    r, data = run_script(tmp_path, [("one", "export V=7\necho one"), ("two", 'test "$V" = 7')])
    assert r.returncode == 0, r.stderr
    assert [(x["id"], x["status"]) for x in data["steps"]] == [("one", "passed"), ("two", "passed")]
    assert all(isinstance(x["seconds"], int) for x in data["steps"])


def test_script_failure_recorded_and_stops(tmp_path):
    r, data = run_script(tmp_path, [("ok", "true"), ("bad", "false"), ("never", "echo never")])
    assert r.returncode != 0
    assert "never" not in r.stdout
    assert [(x["id"], x["status"]) for x in data["steps"]] == [("ok", "passed"), ("bad", "failed")]


def test_cli_list_and_emit(tmp_path, capsys):
    p = md(tmp_path, ("bash release-step=a", "echo a"), ("bash release-step=b", "echo b"))
    assert rr.main(["--file", str(p), "--list"]) == 0
    assert capsys.readouterr().out.split() == ["a", "b"]
    out = tmp_path / "x.sh"
    assert rr.main(["--file", str(p), "--emit", str(out)]) == 0
    assert "echo b" in out.read_text()


@pytest.mark.skipif(os.environ.get("MIRUM_NATIVE_GATE_IMAGE") == "1" or not (ROOT / "RELEASE.md").exists(),
                    reason="native gate image has no RELEASE.md; runs in ci.yml schema-tests")
def test_real_release_md():
    steps = rr.extract(ROOT / "RELEASE.md")
    assert [i for i, _ in steps] == [
        "build-backend", "build-frontend", "token", "up", "health",
        "api-reference-draft", "api-reference-finalize", "api-reference-job",
        "api-reference-poll", "api-reference-download",
        "backup-volume", "restore-volume", "restart-replacement",
    ]
    text = (ROOT / "RELEASE.md").read_text(encoding="utf-8")
    manual = {"enable-ai", "disable-ai", "save-rollback-images", "rollback", "uninstall", "remove-volume"}
    for m in manual:
        assert f"release-manual={m}" in text
    assert not {i for i, _ in steps} & manual
    code = "\n".join(c for _, c in steps)
    assert "ai.compose.yml" not in code and "docker volume rm" not in code
