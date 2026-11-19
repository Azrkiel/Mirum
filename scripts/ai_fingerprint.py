#!/usr/bin/env python3
"""Print a JSON fingerprint of everything that can change AI behaviour without a code diff.

Run inside an image with backend/ on sys.path (PYTHONPATH=/app/backend or /workspace/backend).
Makes no network or model call.  Used by e2e/test_ai_parity.py to prove the release image
matches the Dockerfile.verify image that B13 qualified on.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from importlib import metadata
from pathlib import Path

ROOT_DIST = "google-genai"


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def dependency_closure(root: str) -> dict[str, str]:
    """Installed versions of ``root`` and everything it requires (markers evaluated here)."""
    from packaging.requirements import Requirement

    seen: dict[str, str] = {}
    todo = [root]
    while todo:
        name = _norm(todo.pop())
        if name in seen:
            continue
        try:
            dist = metadata.distribution(name)
        except metadata.PackageNotFoundError:
            continue  # optional/marker-excluded
        seen[name] = dist.version
        for spec in dist.requires or []:
            req = Requirement(spec)
            if req.marker is None or req.marker.evaluate({"extra": ""}):
                todo.append(req.name)
    return dict(sorted(seen.items()))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _module_sha(modname: str) -> str:
    mod = __import__(modname, fromlist=["_"])
    return _sha256(Path(mod.__file__))


def fingerprint() -> dict:
    import certifi
    import config
    from engineering import dragster_proposer, proposer
    from engineering.proposer import GeminiProposalProvider

    settings = GeminiProposalProvider().settings()
    config_env = {k: getattr(config, k) for k in sorted(dir(config))
                  if (k.startswith("ENGINEERING_") or k.startswith("GEMINI_MODEL")
                      or k.startswith("GEMINI_ENGINEERING"))
                  and isinstance(getattr(config, k), (str, int, float, bool, type(None)))}
    return {
        "python_version": sys.version,
        "provider_settings": settings.model_dump(mode="json"),
        "prompt_versions": {
            "engineering_extraction": proposer.PROMPT_VERSION,
            "engineering_schema": proposer.SCHEMA_VERSION,
            "dragster_interpret": dragster_proposer.PROMPT_VERSION,
        },
        "google_genai_closure": dependency_closure(ROOT_DIST),
        "certifi_bundle_sha256": _sha256(Path(certifi.where())),
        "source_sha256": {m: _module_sha(m) for m in (
            "engineering.proposer", "engineering.dragster_proposer",
            "config", "subagents.model_registry")},
        "tls_os_trust": config.ENGINEERING_TLS_OS_TRUST,
        "ai_config": config_env,
    }


if __name__ == "__main__":
    print(json.dumps(fingerprint(), indent=2, sort_keys=True))
