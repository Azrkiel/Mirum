"""Structural checks for the Fly deployment of the RC1 release images (stdlib only, no Docker).

Covers backend/Dockerfile stage `fly`, deploy/fly/release-init.sh, deploy/fly/*.fly.toml and
.github/workflows/release-publish.yml. Run with: pytest e2e/test_fly_release.py
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "backend" / "Dockerfile"
INIT = ROOT / "deploy" / "fly" / "release-init.sh"
README = ROOT / "deploy" / "fly" / "README.md"
BACKEND_TOML = ROOT / "deploy" / "fly" / "backend.fly.toml"
FRONTEND_TOML = ROOT / "deploy" / "fly" / "frontend.fly.toml"
WORKFLOW = ROOT / ".github" / "workflows" / "release-publish.yml"
FLY_ACTION = ROOT / ".github" / "actions" / "build-fly-image" / "action.yml"
IMAGES_WORKFLOW = ROOT / ".github" / "workflows" / "release-images.yml"
BAKE = ROOT / "picogk-builder" / "docker-bake.hcl"

RELEASE_CMD = ('CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/backend", "--host", '
               '"0.0.0.0", "--port", "8000", "--workers", "1"]')


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def code_lines(text: str) -> list[str]:
    """Non-blank, non-comment lines."""
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def stage(text: str, name: str) -> list[str]:
    """Logical (continuation-joined) instruction lines of one Dockerfile stage."""
    out, current, buf = [], None, ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        ln, buf = buf.strip(), ""
        if not ln or ln.startswith("#"):
            continue
        if ln.split(None, 1)[0].upper() == "FROM":
            toks = ln.split()
            current = toks[toks.index("AS") + 1] if "AS" in toks else None
        if current == name:
            out.append(ln)
    return out


def toml_code(path: Path) -> str:
    return "\n".join(code_lines(read(path)))


# ---- Dockerfile stage -------------------------------------------------------

def test_fly_stage_is_from_release_and_runs_the_init_script():
    fly = stage(read(DOCKERFILE), "fly")
    assert fly, "no fly stage"
    assert fly[0] == "FROM release AS fly"
    joined = "\n".join(fly)
    assert "USER root" in fly
    assert 'ENTRYPOINT ["/usr/local/bin/mirum-fly-init"]' in fly
    copy = [ln for ln in fly if ln.startswith("COPY")]
    assert len(copy) == 1 and "deploy/fly/release-init.sh" in copy[0]
    assert "--chown=0:0" in copy[0] and "--chmod=0555" in copy[0]
    assert copy[0].endswith("/usr/local/bin/mirum-fly-init")
    assert "command -v setpriv" in joined and "command -v mount" in joined
    release_cmd = [ln for ln in stage(read(DOCKERFILE), "release") if ln.startswith("CMD")]
    assert release_cmd == [RELEASE_CMD]
    assert [ln for ln in fly if ln.startswith("CMD")] == release_cmd


def test_fly_stage_leaves_the_last_dockerfile_user_unprivileged():
    users = [ln for ln in code_lines(read(DOCKERFILE)) if ln.startswith("USER ")]
    assert users[-1].split()[1].split(":")[0] == "10001"


def test_release_stage_is_unchanged_in_behaviour():
    release_lines = stage(read(DOCKERFILE), "release")
    release = "\n".join(release_lines)
    assert "ENTRYPOINT" not in release and "mirum-fly-init" not in release
    assert "MIRUM_OPEN_ACCESS" not in release
    assert "MIRUM_DEPLOYMENT=release" in release
    assert release_lines[-2:] == ["USER 10001:10001", RELEASE_CMD]


def test_bake_has_a_backend_fly_target_built_like_backend_release():
    text = read(BAKE)
    m = re.search(r'target "backend-fly" \{(.*?)\n\}', text, re.S)
    assert m, "no backend-fly target"
    body = m.group(1)
    assert "inherits" in body and '"backend-release"' in body  # same context, dockerfile, contexts
    assert re.search(r'target\s*=\s*"fly"', body)
    assert "mirum-backend-fly" in body


# ---- init script ------------------------------------------------------------

def test_init_script_is_posix_sh_and_fails_closed():
    text = read(INIT)
    assert text.startswith("#!/bin/sh\n")
    assert re.search(r"^set -eu$", text, re.M)
    assert "\r" not in text


def test_init_script_has_every_required_step():
    text = "\n".join(code_lines(read(INIT)))
    assert 'id -u' in text and '= "0"' in text
    assert "/proc/self/mountinfo" in text and '"/data"' in text
    assert re.search(r"mount -t tmpfs -o size=256M,mode=1777\S* tmpfs /tmp$", text, re.M)
    assert re.search(r'mount -t tmpfs -o "size=256M,uid=\$APP_UID,gid=\$APP_GID,mode=0700\S*" tmpfs /tmp/mirum', text)
    assert "APP_UID=10001" in text and "APP_GID=10001" in text
    assert '[ ! -L "$SRC" ]' in text
    assert 'chown -h "$APP_UID:$APP_GID" "$SRC"' in text and 'chmod 0700 "$SRC"' in text
    assert '[ "$(stat -c %u "$VOLUME")" = "0" ]' in text
    assert '[ -z "$(find "$VOLUME" -maxdepth 0 -perm /022)" ]' in text
    assert '[ -d "$SRC" ] && [ ! -L "$SRC" ]' in text
    assert '[ "$(stat -c %u:%g:%a "$DST")" = "$APP_UID:$APP_GID:700" ]' in text
    assert 'SRC="$VOLUME/engineering"' in text and "DST=/tmp/mirum/engineering" in text
    assert 'mount --bind "$SRC" "$DST"' in text
    assert "mount -o remount,ro /" in text
    for flag in ("--reuid=", "--regid=", "--clear-groups", "--inh-caps=-all", "--bounding-set=-all", "--no-new-privs"):
        assert flag in text, flag
    assert "exec setpriv" in text and '-- "$@"' in text


def test_init_script_order_binds_before_ro_remount_before_exec():
    text = read(INIT)
    order = [text.index(s) for s in ("tmpfs /tmp\n", "tmpfs /tmp/mirum", "mount --bind", "remount,ro /", "exec setpriv")]
    assert order == sorted(order)


def test_init_script_has_no_recursive_chmod_or_chown_and_never_prints_env():
    text = "\n".join(code_lines(read(INIT)))
    assert not re.search(r"\b(chmod|chown)\s+(-\w*R|--recursive)", text)
    assert not re.search(r"\b(env|printenv)\b", text)
    assert "export" not in text


def test_init_script_parses_with_sh():
    try:
        result = subprocess.run(["sh", "-n", str(INIT)], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return  # no sh on this host
    assert result.returncode == 0, result.stderr


# ---- fly.toml files ---------------------------------------------------------

def test_fly_tomls_have_no_build_section_and_no_forbidden_settings():
    for path in (BACKEND_TOML, FRONTEND_TOML):
        assert "[build]" not in toml_code(path), path
        assert "MIRUM_ENGINEERING_AI_DRAFTS" not in read(path), path
        assert not re.search(r"^\s*MIRUM_ACCESS_TOKEN\s*=", toml_code(path), re.M), path


def test_backend_toml_settings():
    text = toml_code(BACKEND_TOML)
    assert re.search(r'^MIRUM_OPEN_ACCESS = "1"$', text, re.M)
    assert re.search(r'^MIRUM_TRUSTED_CLIENT_HEADER = "1"$', text, re.M)
    assert re.search(r'\[mounts\]\s+source = "mirum_engineering"\s+destination = "/data"', text)
    assert re.search(r'\[\[vm\]\]\s+size = "shared-cpu-4x"\s+memory = "8gb"', text)
    assert 'auto_stop_machines = "stop"' in text and "min_machines_running = 0" in text
    assert "fly scale count 1" in read(BACKEND_TOML)
    assert 'app = "mirum-api"' in text and 'primary_region = "iad"' in text


def test_backend_has_no_public_service_exposure():
    text = toml_code(BACKEND_TOML)
    assert "[[services]]" not in text and "[[services.ports]]" not in text
    assert "[http_service]" in text and "internal_port = 8000" in text
    assert not re.search(r"^\s*(ports?|handlers)\s*=", text, re.M)
    assert "--no-public-ips" in read(BACKEND_TOML) and "--no-public-ips" in read(README)


def test_open_access_opt_in_is_only_in_the_backend_toml():
    assert "MIRUM_OPEN_ACCESS" not in read(FRONTEND_TOML)
    assert "MIRUM_OPEN_ACCESS" not in read(DOCKERFILE)
    assert "MIRUM_OPEN_ACCESS" not in read(INIT)
    hits = [p for p in (ROOT / "deploy" / "fly").iterdir()
            if p.suffix == ".toml" and 'MIRUM_OPEN_ACCESS = "1"' in read(p)]
    assert hits == [BACKEND_TOML]


def test_frontend_toml_settings():
    text = toml_code(FRONTEND_TOML)
    assert re.search(r'^MIRUM_ACCESS_GATE = "0"$', text, re.M)
    assert re.search(r'^BACKEND_URL = "http://mirum-api\.flycast"$', text, re.M)
    assert re.search(r'\[\[vm\]\]\s+size = "shared-cpu-1x"\s+memory = "1gb"', text)
    assert 'app = "mirum"' in text


def test_old_fly_dockerfiles_are_gone_and_nothing_names_them():
    for name in ("backend.Dockerfile", "frontend.Dockerfile"):
        assert not (ROOT / "deploy" / "fly" / name).exists()
    for path in (README, BACKEND_TOML, FRONTEND_TOML, WORKFLOW):
        text = read(path)
        assert "deploy/fly/backend.Dockerfile" not in text and "deploy/fly/frontend.Dockerfile" not in text


# ---- publish workflow -------------------------------------------------------

def test_publish_workflow_is_dispatch_only_and_main_only():
    text = read(WORKFLOW)
    head = text.split("\njobs:")[0]
    assert re.findall(r"^on:\n\s+(\w+):\s*$", head, re.M) == ["workflow_dispatch"]
    assert "pull_request" not in text and "push:" not in text and "schedule:" not in text
    assert "github.ref == 'refs/heads/main'" in text
    assert "github.ref != 'refs/heads/main'" in text  # explicit refusal job off main
    assert 'test "$GITHUB_REF" = "refs/heads/main"' in text


def test_publish_workflow_permissions_are_minimal():
    text = read(WORKFLOW)
    assert re.search(r"^permissions: \{\}$", text, re.M)
    assert text.count("packages: write") == 1
    job = re.search(r"^  publish:\n(.*?)(?=^  \w|\Z)", text, re.S | re.M).group(1)
    assert re.search(r"permissions:\n\s+contents: read\n\s+packages: write", job)
    assert "id-token" not in text and "contents: write" not in text


def test_publish_workflow_actions_are_sha_pinned_and_use_only_github_token():
    text = read(WORKFLOW)
    uses = re.findall(r"^\s*(?:-\s+)?uses:\s*(\S+)", text + read(FLY_ACTION), re.M)
    assert uses
    for ref in uses:
        if ref.startswith("./"):
            continue
        assert re.fullmatch(r"[\w./-]+@[0-9a-f]{40}", ref), ref
    assert set(re.findall(r"secrets\.(\w+)", text)) == {"GITHUB_TOKEN"}


def test_publish_workflow_pushes_only_after_smoke_on_main_and_records_digests():
    wf = read(WORKFLOW)
    text = wf + read(FLY_ACTION)
    assert "./.github/actions/build-release-images" in wf
    assert "./.github/actions/build-fly-image" in wf
    action = read(FLY_ACTION)
    assert "targets: backend-fly" in action
    assert action.index("Smoke the Fly image") < action.index("Fly image negative cases")
    # the build+smoke action runs before login, which runs before the push
    assert wf.index("build-fly-image") < wf.index("docker/login-action") < wf.index("docker push")
    assert "--cap-add SYS_ADMIN" in text and "MIRUM_OPEN_ACCESS=1" in text
    for needle in ("MIRUM_STARTUP_OPEN_ACCESS", "MIRUM_STARTUP_REFUSED", "MIRUM_FLY_INIT_REFUSED",
                   "access_token_missing", "/auth/check"):
        assert needle in text, needle
    assert wf.count("docker push") == 2 and "docker push" not in action
    assert "release-publish.json" in wf and "GITHUB_STEP_SUMMARY" in wf
    assert "mirum-backend-fly" in wf and "mirum-frontend" in wf
    assert "login-action" not in action  # the smoke action never sees a registry
    for step in ("Log in to ghcr.io", "Push the images"):
        block = wf[wf.index(step):].split("\n      - ")[0]
        assert "if: github.ref == 'refs/heads/main'" in block, step


def test_release_images_workflow_builds_and_smokes_the_fly_image_without_pushing():
    text = read(IMAGES_WORKFLOW)
    assert "./.github/actions/build-fly-image" in text
    assert "docker push" not in text and "packages: write" not in text
