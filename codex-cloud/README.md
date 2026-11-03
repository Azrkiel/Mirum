# Regen generator on Codex cloud (no Docker)

Codex cloud containers can't run Docker. `setup.sh` instead unpacks the prebuilt
builder, PicoGK runtime and its libraries from the public image
`ghcr.io/azrkiel/mirum-regen:slice-1` (pinned by digest, every layer hash-checked)
and runs this checkout's Python verifier natively. The C# builder and runtime are
byte-identical between `slice-1` and an image built from `c6e9416`, so the only
differences from the real image are the host glibc (Ubuntu 24.04, 2.39 vs Debian
trixie 2.41) and pip wheels for numpy 2.2.4 / scipy 1.15.3 / pydantic 2.10.6
(same versions as the image's Debian packages).

## Codex environment settings

- Repository: `Azrkiel/Mirum`, a branch containing `codex-cloud/`.
- Setup script: `bash codex-cloud/setup.sh` (about 1 minute; needs ghcr.io,
  Ubuntu apt and PyPI, which setup scripts can reach).
- Agent internet access: off is fine. Everything is installed during setup.

## Running

```bash
codex-cloud/regen.sh picogk-builder/examples/reference.json /tmp/out/reference   # exit 0 accepted, 2 rejected
source /opt/regen/env.sh && cd backend && OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 \
  python -m pytest tests/test_regen_spec.py tests/test_regen_mesh.py tests/test_regen_cli.py tests/test_regen_budget.py \
  -q -k "not adv" -p no:cacheprovider
```

`regen.sh` takes the same arguments as the Docker image, including `--prebuilt-stl`.

## Fidelity check (2026-11-03, local stand-in)

Stand-in: plain `ubuntu:24.04` + uv, `--cpus 2 --memory 3g`, no Docker inside,
against `picogk-regen` built from `c6e9416` (same caps, `--network none`).

| Example | Real image | Codex stand-in | report.json |
|---|---|---|---|
| reference.json | accepted, 115 s | accepted, 123 s | identical except builder seconds / peak MB |
| small.json | accepted, 75 s | accepted, 78 s | identical except builder seconds / peak MB |
| too_many_channels.json | spec rejected | spec rejected, same message | n/a |

Raw and cleaned STL hashes matched exactly (reference cleaned
`e1ade11b…0758`, the hash in `docs/testing/regen-chamber-testers.md`). Small
suite: 148 passed, 43 deselected, peak under 300 MiB. Not yet run on a real Codex
cloud machine, and the 43 `adv` fixtures, defect matrix and 48-spec sweep were
not part of this check.

## Limits (ChatGPT Plus: 2 vCPU, 8 GiB RAM, 8 GiB disk)

- Builder peaks about 0.6 GB at 0.4 mm voxel and 2.5 GB at 0.2 mm, where a raw
  STL is about 0.5 GB. Prefer 0.4 mm and delete meshes after use.
- Results leave the task only as a diff: commit reports and hashes, never STLs.
- Hold-out specs, injected-defect meshes and their results stay outside the repo.
  Don't add them here; Codex cloud only sees what is committed.
- Hashes from this setup match the Docker image for the examples above. Re-check
  `reference.json` against the hash above after changing `IMAGE_DIGEST` or
  library versions.
