"""Release compose and startup-guard checks (RC1 R3 part 2).

Needs the Docker CLI and the images built by .github/actions/build-release-images
(mirum-backend:release, mirum-frontend:release). The static compose tests need only the CLI;
the rest need a running daemon. In CI (CI=true) a missing daemon fails instead of skipping.

Run: pytest -q -p no:cacheprovider e2e/test_startup_guards.py
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.release.yml"
MISCONFIG = ROOT / "e2e" / "compose" / "misconfig"
# busybox:1.37.0 (multi-arch index digest), used only to create a root-owned volume.
BUSYBOX = "busybox@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"

# overlay file stem -> failure code that must appear on the backend's stderr
MISCONFIGS = {
    "no-volume": "engineering_volume_missing",
    "root-owned": "engineering_volume_owner",
    "workers2": "multiple_workers",
    "scratch-disk": "scratch_not_tmpfs",
    "no-token": "access_token_missing",
    "weak-token": "access_token_weak",
    "unknown-deployment": "unknown_deployment",
}


def _need(reason: str):
    if os.environ.get("CI"):
        pytest.fail(reason)
    pytest.skip(reason)


def _docker(*args, check=False, timeout=300, env=None):
    return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=timeout,
                          check=check, env=env)


@pytest.fixture(scope="module")
def cli():
    if shutil.which("docker") is None:
        _need("docker CLI not installed")
    if _docker("compose", "version").returncode != 0:
        _need("docker compose plugin not available")


@pytest.fixture(scope="module")
def daemon(cli):
    if _docker("info").returncode != 0:
        _need("docker daemon not reachable")
    for image in ("mirum-backend:release", "mirum-frontend:release"):
        if _docker("image", "inspect", image).returncode != 0:
            _need(f"{image} not built (run .github/actions/build-release-images first)")


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _env(token: str, **extra) -> dict:
    env = dict(os.environ)
    env["MIRUM_ACCESS_TOKEN"] = token
    env.update(extra)
    return env


def _compose(project: str, files, *args, env, timeout=300):
    cmd = ["docker", "compose", "-p", project]
    for f in files:
        cmd += ["-f", str(f)]
    return subprocess.run(cmd + list(args), capture_output=True, text=True, timeout=timeout,
                          env=env, cwd=ROOT)


def _token() -> str:
    # Run-local value (the equivalent of `openssl rand -hex 32`); never a stored secret.
    return secrets.token_hex(32)


# ---------------------------------------------------------------- static (CLI only)

def _config(*files, token=None, extra=None):
    env = dict(os.environ)
    env.pop("MIRUM_ACCESS_TOKEN", None)
    if token is not None:
        env["MIRUM_ACCESS_TOKEN"] = token
    env["MIRUM_ROOT_OWNED_VOLUME"] = "unused-root-owned"
    env.update(extra or {})
    cmd = ["docker", "compose"]
    for f in files:
        cmd += ["-f", str(f)]
    return subprocess.run(cmd + ["config", "--format", "json"], capture_output=True, text=True,
                          env=env, cwd=ROOT, timeout=60)


def test_compose_requires_access_token(cli):
    proc = _config(COMPOSE)
    assert proc.returncode != 0
    assert "MIRUM_ACCESS_TOKEN" in proc.stderr and "required" in proc.stderr


def test_compose_release_is_hardened(cli):
    proc = _config(COMPOSE, token=_token())
    assert proc.returncode == 0, proc.stderr
    services = json.loads(proc.stdout)["services"]
    for name in ("backend", "frontend"):
        svc = services[name]
        assert svc["read_only"] is True, name
        assert svc["init"] is True, name
        assert svc["cap_drop"] == ["ALL"], name
        assert any(o.startswith("no-new-privileges") for o in svc["security_opt"]), name
        assert svc.get("healthcheck", {}).get("test"), name
        assert svc["pull_policy"] == "never" and "build" not in svc, name
        assert "privileged" not in svc and "cap_add" not in svc, name
        assert all(p["host_ip"] == "127.0.0.1" for p in svc["ports"]), name
        # no bind mounts anywhere
        assert all(v["type"] != "bind" for v in svc.get("volumes", [])), name
    backend, frontend = services["backend"], services["frontend"]
    assert backend["tmpfs"][0].startswith("/tmp:")
    assert any(t.startswith("/tmp/mirum:") and "uid=10001" in t and "gid=10001" in t
               and "mode=0700" in t for t in backend["tmpfs"])
    assert backend["volumes"] == [{"type": "volume", "source": "mirum_engineering",
                                   "target": "/tmp/mirum/engineering", "volume": {}}]
    assert "user" not in backend and "user" not in frontend  # the images run as 10001
    assert len(backend["environment"]["MIRUM_ACCESS_TOKEN"]) >= 32
    assert frontend["environment"]["MIRUM_ACCESS_GATE"] == "1"
    assert frontend["environment"]["BACKEND_URL"] == "http://backend:8000"
    assert "MIRUM_ACCESS_TOKEN" not in frontend["environment"]
    assert frontend["depends_on"]["backend"]["condition"] == "service_healthy"


@pytest.mark.parametrize("name", sorted(MISCONFIGS))
def test_misconfig_overlay_merges(cli, name):
    overlay = MISCONFIG / f"{name}.yml"
    assert overlay.is_file()
    proc = _config(COMPOSE, overlay, token=_token())
    assert proc.returncode == 0, proc.stderr


def test_every_misconfig_overlay_is_tested():
    assert {p.stem for p in MISCONFIG.glob("*.yml")} == set(MISCONFIGS)


# ---------------------------------------------------------------- Docker

@pytest.fixture(scope="module")
def stack(daemon):
    project = f"mirumrel{uuid.uuid4().hex[:8]}"
    token = _token()
    env = _env(token, MIRUM_BACKEND_PORT=str(_free_port()), MIRUM_FRONTEND_PORT=str(_free_port()))
    up = _compose(project, [COMPOSE], "up", "-d", "--wait", "--wait-timeout", "300", env=env,
                  timeout=420)
    info = {"project": project, "token": token, "env": env,
            "backend": f"http://127.0.0.1:{env['MIRUM_BACKEND_PORT']}",
            "frontend": f"http://127.0.0.1:{env['MIRUM_FRONTEND_PORT']}"}
    try:
        if up.returncode != 0:
            logs = _compose(project, [COMPOSE], "logs", "--no-color", env=env)
            pytest.fail(f"compose up failed:\n{up.stderr}\n{logs.stdout}\n{logs.stderr}")
        yield info
    finally:
        _compose(project, [COMPOSE], "down", "-v", "--remove-orphans", env=env)


def _get(url, token=None):
    req = urllib.request.Request(url, headers={"X-Mirum-Access-Token": token} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_good_config_auth_check(stack):
    assert _get(stack["backend"] + "/auth/check", stack["token"]) == 200
    assert _get(stack["backend"] + "/auth/check") == 401
    assert _get(stack["backend"] + "/auth/check", "wrong-" + stack["token"][6:]) == 401


def test_good_config_frontend_healthy(stack):
    assert _get(stack["frontend"] + "/_stcore/health") == 200


def test_good_config_volume_owned_by_app_user(stack):
    proc = _compose(stack["project"], [COMPOSE], "exec", "-T", "backend", "sh", "-c",
                    "id -u; stat -c '%u:%g %a' /tmp/mirum/engineering; "
                    "grep -c ' / ro,' /proc/self/mountinfo",
                    env=stack["env"])
    assert proc.returncode == 0, proc.stderr
    uid, owner, ro = proc.stdout.split()
    assert uid == "10001"
    assert owner.startswith("10001:10001 ")
    assert int(owner.split()[1], 8) & 0o022 == 0
    assert int(ro) >= 1


def _run_backend(project, files, env):
    return _compose(project, files, "run", "--rm", "--no-deps", "-T", "backend", env=env,
                    timeout=240)


def _assert_refused(proc, code):
    assert proc.returncode != 0, f"backend started:\n{proc.stdout}\n{proc.stderr}"
    assert re.search(rf"MIRUM_STARTUP_REFUSED code={code}:", proc.stderr), (
        f"missing code={code}\n--- stderr ---\n{proc.stderr}\n--- stdout ---\n{proc.stdout}")


def test_second_backend_on_same_volume_is_refused(stack):
    proc = _run_backend(stack["project"], [COMPOSE], stack["env"])
    _assert_refused(proc, "engineering_volume_in_use")
    # the first backend is unaffected
    assert _get(stack["backend"] + "/auth/check", stack["token"]) == 200


@pytest.mark.parametrize("name", sorted(MISCONFIGS))
def test_misconfiguration_is_refused(daemon, name):
    project = f"mirumbad{uuid.uuid4().hex[:8]}"
    env = _env(_token(), MIRUM_ROOT_OWNED_VOLUME=f"{project}_rootowned")
    try:
        if name == "root-owned":
            # Non-empty and root-owned, so Docker does not initialise it from the image.
            _docker("run", "--rm", "--network", "none", "-v", f"{project}_rootowned:/v",
                    BUSYBOX, "sh", "-c", "touch /v/x && chown 0:0 /v && chmod 0755 /v",
                    check=True)
        proc = _run_backend(project, [COMPOSE, MISCONFIG / f"{name}.yml"], env)
        _assert_refused(proc, MISCONFIGS[name])
    finally:
        _compose(project, [COMPOSE, MISCONFIG / f"{name}.yml"], "down", "-v",
                 "--remove-orphans", env=env)
        _docker("volume", "rm", "-f", f"{project}_rootowned")
