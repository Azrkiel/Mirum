"""AI configuration parity: the release image must match the image B13 qualified on.

Needs Docker and two built images, so it runs in CI (release-images.yml):
  MIRUM_RELEASE_IMAGE  release image tag/ID (backend/Dockerfile, target release)
  MIRUM_VERIFY_IMAGE   Dockerfile.verify image built at the same commit
The tests FAIL (not skip) when the variables are missing: parity is a hard criterion.
No network (--network none) and no model call; scripts/ai_fingerprint.py is mounted in.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT = ROOT / "scripts" / "ai_fingerprint.py"
REFERENCE = json.loads((ROOT / "e2e" / "fixtures" / "ai_parity_reference.json").read_text(encoding="utf-8"))


def _image(var: str) -> str:
    value = os.environ.get(var, "")
    if not value:
        pytest.fail(f"{var} is not set: build the images first (see release-images.yml)")
    return value


def _run(image: str, *cmd: str, mount: bool = False) -> str:
    args = ["docker", "run", "--rm", "--network", "none", "--entrypoint", "python"]
    if mount:
        args += ["-v", f"{FINGERPRINT}:/tmp/ai_fingerprint.py:ro"]
    out = subprocess.run(args + [image, *cmd], capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stderr
    return out.stdout


def _fingerprint(image: str) -> dict:
    return json.loads(_run(image, "/tmp/ai_fingerprint.py", mount=True))


def _freeze(image: str) -> dict[str, str]:
    pins = {}
    for line in _run(image, "-m", "pip", "freeze").splitlines():
        m = re.match(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==(\S+)$", line.strip())
        if m:
            pins[re.sub(r"[-_.]+", "-", m.group(1)).lower()] = m.group(2)
    return pins


def test_a_release_fingerprint_equals_verify_fingerprint():
    release, verify = _fingerprint(_image("MIRUM_RELEASE_IMAGE")), _fingerprint(_image("MIRUM_VERIFY_IMAGE"))
    assert release == verify


def test_b_release_fingerprint_matches_qualified_reference():
    fp = _fingerprint(_image("MIRUM_RELEASE_IMAGE"))
    assert fp["python_version"].startswith(REFERENCE["python_prefix"])
    assert fp["google_genai_closure"]["google-genai"] == REFERENCE["google_genai_version"]
    assert fp["provider_settings"] == REFERENCE["provider_settings"]
    assert fp["prompt_versions"]["engineering_extraction"] == REFERENCE["prompt_versions"]["engineering_extraction"]
    assert fp["prompt_versions"]["engineering_schema"] == REFERENCE["prompt_versions"]["engineering_schema"]
    assert fp["tls_os_trust"] is REFERENCE["tls_os_trust"]
    # Anchor to the qualified source (commit f11cf90), not just to this PR's head: none of
    # these four files has changed since, so the hashes must be identical.
    assert fp["source_sha256"] == REFERENCE["qualified"]["source_sha256"]


def test_c_release_pip_freeze_is_subset_of_verify():
    release, verify = _freeze(_image("MIRUM_RELEASE_IMAGE")), _freeze(_image("MIRUM_VERIFY_IMAGE"))
    assert release
    assert {k: v for k, v in release.items() if verify.get(k) != v} == {}
