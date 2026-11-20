"""Supply-chain pin checks for the release images (stdlib only, no Docker).

Shipped Dockerfiles: backend/Dockerfile and frontend/Dockerfile.
Not shipped (not checked): Dockerfile.verify (test image, owned by CI),
backend/Dockerfile.conda-baseline (kept for reference, never built),
deploy/fly/ has no Dockerfiles any more: the Fly backend is stage `fly` of backend/Dockerfile
(checked in e2e/test_fly_release.py).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SHIPPED = [ROOT / "backend" / "Dockerfile", ROOT / "frontend" / "Dockerfile"]
BACKEND_LOCKS = ["requirements-linux.lock", "requirements-mirum-verification.lock",
                 "requirements-glider.lock"]
RELEASE_LOCK = ROOT / "requirements-release.lock"
FRONTEND_LOCK = ROOT / "frontend" / "requirements.lock"
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")
FORBIDDEN = [r"\bconda\b", r"curl[^\n|]*\|\s*(ba)?sh", r"wget[^\n|]*\|\s*(ba)?sh",
             r"--trusted-host", r"--no-check-certificate", r"\bPIP_CERT\b",
             r"verify\s*=\s*False", r"--insecure", r"\bcurl\b[^\n]*\s-k\b"]
PIN = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([^\s;\\]+)")


def norm(n: str) -> str:
    return re.sub(r"[-_.]+", "-", n).lower()


def logical_lines(text: str) -> list[str]:
    """Join backslash continuations; drop blanks and comments."""
    out, buf = [], ""
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        buf += line
        if buf.strip() and not buf.strip().startswith("#"):
            out.append(buf.strip())
        buf = ""
    if buf.strip():
        out.append(buf.strip())
    return out


def dockerfile_problems(text: str) -> list[str]:
    problems: list[str] = []
    stages: set[str] = set()
    last_user = None
    for ln in logical_lines(text):
        up = ln.split(None, 1)[0].upper()
        if up == "FROM":
            toks = [t for t in ln.split()[1:] if not t.startswith("--")]
            image = toks[0]
            if image not in stages and image != "scratch" and not DIGEST.search(image):
                problems.append(f"unpinned FROM: {ln}")
            if len(toks) >= 3 and toks[1].lower() == "as":
                stages.add(toks[2])
        elif up == "COPY":
            m = re.search(r"--from=(\S+)", ln)
            if m and m.group(1) not in stages and not DIGEST.search(m.group(1)):
                problems.append(f"unpinned COPY --from: {ln}")
        elif up == "USER":
            last_user = ln.split(None, 1)[1].strip()
        if up == "RUN":
            if re.search(r"\bpip3?\s+install\b|-m\s+pip\s+install\b", ln):
                if "--require-hashes" not in ln:
                    problems.append(f"pip install without --require-hashes: {ln}")
            if re.search(r"\bnpm\s+(install|i)\b", ln):
                problems.append(f"npm install (use npm ci): {ln}")
            for m in re.finditer(r"apt-get\s+install\b([^&;]*)", ln):
                for tok in m.group(1).split():
                    if tok.startswith("-"):
                        continue
                    if not re.fullmatch(r"[a-z0-9][a-z0-9.+-]*=[^\s=]+", tok):
                        problems.append(f"apt package not pinned as pkg=ver: {tok}")
        for pat in FORBIDDEN:
            if re.search(pat, ln, re.I):
                problems.append(f"forbidden pattern {pat!r}: {ln}")
    if last_user is None or last_user.split(":")[0] != "10001":
        problems.append(f"final stage must run as USER 10001, got {last_user!r}")
    return problems


def lock_problems(text: str) -> list[str]:
    problems: list[str] = []
    entries = logical_lines(text)
    if not entries:
        problems.append("empty lock")
    for ln in entries:
        if ln.startswith("-"):
            continue
        if not PIN.match(ln):
            problems.append(f"lock line without == pin: {ln[:80]}")
        elif not re.search(r"--hash=sha256:[0-9a-f]{64}", ln):
            problems.append(f"lock line without --hash: {ln[:80]}")
    return problems


def lock_pins(text: str) -> dict[str, str]:
    pins = {}
    for ln in logical_lines(text):
        m = PIN.match(ln)
        if m:
            pins[norm(m.group(1))] = m.group(2)
    return pins


def plain_pins(path: Path) -> dict[str, str]:
    pins = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        m = PIN.match(raw.strip())
        if m:
            pins[norm(m.group(1))] = m.group(2)
    return pins


def _extra_pins() -> dict[str, str]:
    """Transitive packages the source locks omit (see scripts/make_release_lock.py)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("make_release_lock", ROOT / "scripts" / "make_release_lock.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return dict(mod.EXTRA_PINS)


# ---- tests against the repository -------------------------------------------

def test_shipped_dockerfiles_are_pinned():
    for path in SHIPPED:
        assert path.is_file(), path
        assert dockerfile_problems(path.read_text(encoding="utf-8")) == [], path


def test_dockerfiles_install_only_hashed_locks():
    for path in SHIPPED:
        text = path.read_text(encoding="utf-8")
        assert "requirements-release.lock" in text or "requirements.lock" in text, path


def test_release_locks_have_hashes():
    assert lock_problems(RELEASE_LOCK.read_text(encoding="utf-8")) == []
    assert lock_problems(FRONTEND_LOCK.read_text(encoding="utf-8")) == []


def test_release_lock_equals_union_of_backend_locks_plus_extras():
    union: dict[str, str] = {}
    for name in BACKEND_LOCKS:
        for k, v in plain_pins(ROOT / name).items():
            assert union.get(k, v) == v, f"conflicting pins for {k}"
            union[k] = v
    union.update({norm(k): v for k, v in _extra_pins().items()})
    assert lock_pins(RELEASE_LOCK.read_text(encoding="utf-8")) == union


def test_frontend_lock_pins_agree_with_backend_pins():
    release = lock_pins(RELEASE_LOCK.read_text(encoding="utf-8"))
    direct = plain_pins(ROOT / "frontend" / "requirements.txt")
    front = lock_pins(FRONTEND_LOCK.read_text(encoding="utf-8"))
    for k, v in front.items():
        expected = release.get(k, direct.get(k))
        assert expected == v, f"{k}=={v} differs from the backend locks / frontend/requirements.txt"
    for k, v in direct.items():
        assert front.get(k) == v


def test_base_digest_is_shared_between_images():
    digests = set()
    for path in SHIPPED:
        digests |= set(re.findall(r"python:3\.10-slim-bookworm@(sha256:[0-9a-f]{64})",
                                  path.read_text(encoding="utf-8")))
    assert len(digests) == 1, digests


def test_babylon_is_local():
    app = (ROOT / "frontend" / "app.py").read_text(encoding="utf-8")
    assert "cdn.babylonjs.com" not in app
    assert (ROOT / "frontend" / "viewer_assets" / "package-lock.json").is_file()
    cfg = (ROOT / "frontend" / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert re.search(r"enableStaticServing\s*=\s*true", cfg)


def test_no_remote_script_or_link_urls_in_frontend_python():
    pat = re.compile(r"(?:src|href)\s*=\s*[\"']https?://", re.I)
    for py in sorted((ROOT / "frontend").glob("*.py")):
        if py.name.startswith("test_"):
            continue
        assert not pat.search(py.read_text(encoding="utf-8")), py.name


def test_conda_baseline_kept_but_not_shipped():
    assert (ROOT / "backend" / "Dockerfile.conda-baseline").is_file()
    assert "conda" not in (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8").lower()


# ---- mutation sanity: the checks must reject bad input -----------------------

GOOD = ("FROM python:3.10-slim-bookworm@sha256:" + "a" * 64 + "\n"
        "RUN pip install --require-hashes --no-deps -r /l.lock\nUSER 10001:10001\n")


def test_mutation_good_dockerfile_passes():
    assert dockerfile_problems(GOOD) == []


def test_mutation_unpinned_from_fails():
    bad = GOOD.replace("@sha256:" + "a" * 64, "")
    assert any("unpinned FROM" in p for p in dockerfile_problems(bad))


def test_mutation_unpinned_copy_from_fails():
    bad = GOOD + "COPY --from=node:22 /x /y\n"
    assert any("COPY --from" in p for p in dockerfile_problems(bad))


def test_mutation_pip_without_hashes_fails():
    bad = GOOD.replace("--require-hashes ", "")
    assert any("--require-hashes" in p for p in dockerfile_problems(bad))


def test_mutation_unpinned_apt_fails():
    bad = GOOD + "RUN apt-get install -y --no-install-recommends libgl1\n"
    assert any("pkg=ver" in p for p in dockerfile_problems(bad))


def test_mutation_root_user_fails():
    bad = GOOD.replace("USER 10001:10001", "USER root")
    assert dockerfile_problems(bad)


def test_mutation_forbidden_patterns_fail():
    for extra in ("RUN conda install x", "RUN curl http://x | sh",
                  "RUN pip install --trusted-host h --require-hashes -r l"):
        assert dockerfile_problems(GOOD.replace("USER", extra + "\nUSER")), extra


def test_mutation_lock_line_without_hash_fails():
    h = "--hash=sha256:" + "b" * 64
    assert lock_problems(f"foo==1.0 \\\n    {h}\n") == []
    assert lock_problems("foo==1.0\n")
    assert lock_problems(f"foo>=1.0 \\\n    {h}\n")


# ---- release guards need the deployment marker --------------------------------

def stage_env(text: str, stage: str) -> dict[str, str]:
    """ENV key=value pairs set directly in one named stage (later ENV wins)."""
    env: dict[str, str] = {}
    current = None
    for ln in logical_lines(text):
        up = ln.split(None, 1)[0].upper()
        if up == "FROM":
            toks = ln.split()[1:]
            current = toks[toks.index("AS") + 1] if "AS" in toks else None
        elif up == "ENV" and current == stage:
            for tok in ln.split()[1:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    env[k] = v
    return env


def test_release_stage_sets_deployment_release():
    text = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    assert stage_env(text, "release").get("MIRUM_DEPLOYMENT") == "release"
    # b13-eval inherits it and must not override it.
    assert "MIRUM_DEPLOYMENT" not in stage_env(text, "b13-eval")


def test_mutation_missing_deployment_marker_is_detected():
    text = "FROM x@sha256:" + "a" * 64 + " AS release\nENV MIRUM_DEPLOYMENT=Release A=b\n"
    assert stage_env(text, "release").get("MIRUM_DEPLOYMENT") != "release"
    assert stage_env("FROM x AS release\nENV A=b\n", "release").get("MIRUM_DEPLOYMENT") is None


def final_stage_env(text: str) -> dict[str, str]:
    """ENV key=value pairs set in the last stage of a Dockerfile."""
    env: dict[str, str] = {}
    for ln in logical_lines(text):
        up = ln.split(None, 1)[0].upper()
        if up == "FROM":
            env = {}
        elif up == "ENV":
            for tok in ln.split()[1:]:
                if "=" in tok:
                    k, v = tok.split("=", 1)
                    env[k] = v
    return env


def test_frontend_image_disables_the_streamlit_file_watcher():
    text = (ROOT / "frontend" / "Dockerfile").read_text(encoding="utf-8")
    assert final_stage_env(text).get("STREAMLIT_SERVER_FILE_WATCHER_TYPE") == "none"
    # A CLI flag would override the env var; no launch line or shipped config may set it.
    # Only ENV may name it (checked above): no CMD/ENTRYPOINT/RUN/ARG line in any spelling.
    for ln in logical_lines(text):
        if ln.split(None, 1)[0].upper() != "ENV":
            assert "filewatcher" not in ln.lower() and "file_watcher" not in ln.lower(), ln
    for f in ("compose.release.yml", "frontend/.streamlit/config.toml"):
        body = (ROOT / f).read_text(encoding="utf-8").lower()
        assert "filewatcher" not in body and "file_watcher" not in body, f


def test_mutation_watcher_left_on_is_detected():
    text = "FROM a@sha256:" + "a" * 64 + "\nENV STREAMLIT_SERVER_FILE_WATCHER_TYPE=none\nFROM b\nENV A=b\n"
    assert final_stage_env(text).get("STREAMLIT_SERVER_FILE_WATCHER_TYPE") is None


# ---- review follow-ups (M1, M2, L1, L2, L4, L5) --------------------------------

def pin_only_problems(text: str) -> list[str]:
    """Only the FROM / COPY --from digest rules (used for the non-shipped Dockerfile.verify)."""
    return [p for p in dockerfile_problems(text) if p.startswith(("unpinned FROM", "unpinned COPY"))]


def test_dockerfile_verify_from_and_copy_from_are_pinned():
    text = (ROOT / "Dockerfile.verify").read_text(encoding="utf-8")
    assert pin_only_problems(text) == []
    digests = set(re.findall(r"python:3\.10-slim-bookworm@(sha256:[0-9a-f]{64})", text))
    shipped = set(re.findall(r"python:3\.10-slim-bookworm@(sha256:[0-9a-f]{64})",
                             (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")))
    assert digests == shipped and len(digests) == 1


def test_mutation_unpinned_copy_from_in_verify_style_file_fails():
    bad = "FROM x@sha256:" + "a" * 64 + " AS a\nCOPY --from=node:22-bookworm-slim /n /n\n"
    assert pin_only_problems(bad)


def test_every_pip_install_is_binary_only_and_hashed():
    for path in SHIPPED:
        for ln in logical_lines(path.read_text(encoding="utf-8")):
            if re.search(r"-m\s+pip\s+install\b|\bpip3?\s+install\b", ln):
                assert "--only-binary=:all:" in ln, ln
                # The only sdist allowed is PyOpenGL 3.1.0 (no wheel on PyPI), built offline.
                for m in re.finditer(r"--no-binary[= ](\S+)", ln):
                    assert m.group(1) == "PyOpenGL", ln
                assert "--require-hashes" in ln, ln


def test_dockerignore_blocks_secrets():
    lines = {l.strip() for l in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()}
    for pat in ("**/.env", "**/.env.*", "**/*.pem", "**/*.key"):
        assert pat in lines, pat


def test_ci_runner_pytest_is_hash_pinned():
    lock = ROOT / ".github" / "ci-requirements" / "pytest.lock"
    assert lock_problems(lock.read_text(encoding="utf-8")) == []
    assert "pytest" in lock_pins(lock.read_text(encoding="utf-8"))
    wf = (ROOT / ".github" / "workflows" / "release-images.yml").read_text(encoding="utf-8")
    for ln in wf.splitlines():
        if "pip install" in ln:
            assert "--require-hashes" in ln and "pytest.lock" in ln, ln


def test_release_stage_excludes_tests_and_dev_scripts():
    ignore = (ROOT / "backend" / "Dockerfile.dockerignore").read_text(encoding="utf-8").splitlines()
    ignore = {l.strip() for l in ignore}
    assert "backend/tests" in ignore
    text = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    # b13-eval takes only the live-eval scripts and the corpus, not the whole scripts/ tree.
    assert not re.search(r"^COPY\s+scripts/?\s", text, re.M)
    assert "scripts/checked_engineering_live_eval.py" in text
    assert "ai-corpus-v1.json" in text


def test_reference_anchors_source_and_image():
    ref = json.loads((ROOT / "e2e" / "fixtures" / "ai_parity_reference.json").read_text(encoding="utf-8"))
    q = ref["qualified"]
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", q["image_id"])
    assert q["run_id"] == 44358308796 and re.fullmatch(r"[0-9a-f]{40}", q["commit"])
    assert set(q["source_sha256"]) == {"engineering.proposer", "engineering.dragster_proposer",
                                       "config", "subagents.model_registry"}
    files = {"engineering.proposer": "backend/engineering/proposer.py",
             "engineering.dragster_proposer": "backend/engineering/dragster_proposer.py",
             "config": "backend/config.py", "subagents.model_registry": "backend/subagents/model_registry.py"}
    import hashlib
    for mod, rel in files.items():
        assert hashlib.sha256((ROOT / rel).read_bytes()).hexdigest() == q["source_sha256"][mod], \
            f"{rel} changed since the qualified commit; update the reference deliberately"


# ---- regen builder in the release image (RC1 R2b part 2) ---------------------------

def stage_lines(text: str, stage: str) -> list[str]:
    """Logical lines of one named stage (FROM ... AS <stage> up to the next FROM)."""
    out: list[str] = []
    inside = False
    for ln in logical_lines(text):
        up = ln.split(None, 1)[0].upper()
        if up == "FROM":
            toks = ln.split()
            inside = len(toks) >= 4 and toks[-2].lower() == "as" and toks[-1] == stage
            continue
        if inside:
            out.append(ln)
    return out


REGEN_WEB = ROOT / "backend" / "engineering" / "regen_web.py"
RECORDED_SO = ROOT / "picogk-builder" / "picogk-runtime.sha256"


def regen_defaults() -> tuple[str, str]:
    src = REGEN_WEB.read_text(encoding="utf-8")
    builder = re.search(r"'REGEN_BUILDER',\s*'([^']+)'", src).group(1)
    gcc = re.search(r"'REGEN_GCC_LIB',\s*'([^']+)'", src).group(1)
    return builder, gcc


def test_release_stage_ships_builder_and_libs_at_the_paths_regen_web_expects():
    builder, gcc = regen_defaults()
    assert builder == "/opt/regen/app/builder" and gcc == "/opt/regen/lib"
    text = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    release = stage_lines(text, "release")
    copies = [ln for ln in release if ln.split(None, 1)[0].upper() == "COPY" and "--from=picogk-regen" in ln]
    dests = {ln.split()[-1].rstrip("/") for ln in copies}
    assert {"/opt/regen/app", "/opt/regen/lib", "/licenses"} <= dests, copies
    # REGEN_BUILDER / REGEN_GCC_LIB are either left to the defaults or set to exactly those paths.
    env = stage_env(text, "release")
    assert env.get("REGEN_BUILDER", builder) == builder
    assert env.get("REGEN_GCC_LIB", gcc) == gcc
    # LD_LIBRARY_PATH is set per builder subprocess by regen_cli, never globally.
    assert "LD_LIBRARY_PATH" not in env and "LD_LIBRARY_PATH" not in " ".join(
        ln for ln in release if ln.split(None, 1)[0].upper() == "ENV")


def test_release_stage_verifies_the_runtime_sha256_and_is_not_empty():
    text = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    recorded = RECORDED_SO.read_text(encoding="utf-8").split()[0]
    assert re.fullmatch(r"[0-9a-f]{64}", recorded), recorded
    release = " ".join(stage_lines(text, "release"))
    assert "picogk-runtime.sha256" in release and "sha256sum -c" in release
    assert "picogk.26.2.so" in release and "test -x /opt/regen/app/builder" in release
    # The fallback stage for a plain `docker build` is empty on purpose; the check above fails it closed.
    assert re.search(r"^FROM scratch AS picogk-regen\b", text, re.M)


def test_regen_payload_is_built_from_pinned_bookworm_images_with_optional_ca_only():
    for rel in ("spike-picogk/Dockerfile.runtime", "picogk-builder/Dockerfile"):
        text = (ROOT / rel).read_text(encoding="utf-8")
        assert dockerfile_problems_digest_only(text) == [], rel
        assert "trixie" not in re.sub(r"(?m)^#.*$", "", text), f"{rel}: the release base is bookworm; a trixie build needs glibc 2.38+"
        assert "avast-ca.pem" not in re.sub(r"(?m)^#.*$", "", text)
    rt = (ROOT / "spike-picogk" / "Dockerfile.runtime").read_text(encoding="utf-8")
    assert re.search(r"^FROM gcc:15-bookworm@sha256:[0-9a-f]{64}", rt, re.M)
    release = " ".join(stage_lines((ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8"), "release"))
    assert "extra_ca" not in release and ".pem" not in release


def dockerfile_problems_digest_only(text: str) -> list[str]:
    internal = {"picogk-runtime"}
    return [p for p in dockerfile_problems(text)
            if p.startswith(("unpinned FROM", "unpinned COPY")) and not any(f"--from={i}" in p for i in internal)]


def test_release_regen_runtime_libs_are_apt_pinned():
    text = (ROOT / "backend" / "Dockerfile").read_text(encoding="utf-8")
    release = " ".join(stage_lines(text, "release"))
    for pkg in ("libtbb12", "libblosc1", "libboost-iostreams1.74.0", "libsnappy1v5"):
        assert re.search(rf"\b{re.escape(pkg)}=\S+", release), pkg


def test_release_action_builds_through_bake_with_the_picogk_cache_and_smokes_regen():
    action = (ROOT / ".github" / "actions" / "build-release-images" / "action.yml").read_text(encoding="utf-8")
    runtime_wf = (ROOT / ".github" / "workflows" / "picogk-runtime.yml").read_text(encoding="utf-8")
    # Same cache scopes as picogk-runtime.yml, so the PicoGK stages are built once per source change.
    for expr in ("picogk-runtime-${{ hashFiles('spike-picogk/Dockerfile.runtime') }}",
                 "picogk-regen-${{ hashFiles('picogk-builder/Dockerfile', 'picogk-builder/Program.cs', 'picogk-builder/builder.csproj') }}"):
        assert expr in action and expr in runtime_wf
    assert "backend-release" in action and "backend-b13-eval" in action
    assert "regen_release_smoke.py" in action and "--read-only" in action and "--network none" in action
    assert re.search(r"uses: docker/bake-action@[0-9a-f]{40}", action)
    assert "load: true" in action  # nothing is pushed to a registry
    assert not re.search(r"push:\s*true|--push", action)
    # Inputs/outputs unchanged for other workflows that use the action.
    nl = chr(10)
    assert nl + "inputs:" + nl + "  evidence-path:" in nl + action
    assert nl + "outputs:" + nl + "  evidence-path:" in nl + action


def test_regen_release_smoke_uses_release_scratch_sizes_and_runs_twice():
    """K1: the CI regen smoke runs with the release compose's 256 MiB /tmp and /tmp/mirum, twice in one container."""
    action = (ROOT / ".github" / "actions" / "build-release-images" / "action.yml").read_text(encoding="utf-8")
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "/tmp:size=256M" in compose and "/tmp/mirum:size=256M" in compose
    assert "--tmpfs /tmp:rw,size=256m," in action and "--tmpfs /tmp/mirum:rw,size=256m," in action
    assert "size=1g" not in action and "size=2g" not in action
    assert "for run in 1 2; do" in action and 'SMOKE_RUN="$run"' in action
    smoke = (ROOT / "scripts" / "regen_release_smoke.py").read_text(encoding="utf-8")
    assert "f'release-smoke-{RUN}'" in smoke
