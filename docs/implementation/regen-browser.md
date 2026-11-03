# PicoGK in the browser

The browser app runs the fixed PicoGK generator on a Linux server and displays
the accepted STL in a local WebGL viewer. No model key, Docker daemon, JavaScript
CDN, or browser Internet access is needed after installation. This is native
server-side generation, not a WebAssembly port. A GitHub Pages static site alone
cannot run PicoGK.

## Start

From the repository root on `codex-cloud/regen-runner`:

```bash
bash codex-cloud/setup.sh
bash codex-cloud/web.sh
```

Open `http://127.0.0.1:8080` on that machine. Choose the small or reference
example, edit dimensions, and select **Generate mesh**. The app shows generation
status and, after geometry acceptance, an interactive preview and downloads for
the complete STL, report, and canonical parameters. Drag or use arrow keys to
rotate; scroll to zoom. The preview samples at most 1,500,000 triangles for large
meshes; the downloaded STL is the full accepted artifact.

The native setup defaults to `/opt/regen`. On a managed machine without root or
sudo, it checks existing GL/X11 libraries and installs to
`/workspace/regen-runtime`. Both launchers detect that fallback. To choose a
different writable location, use the same override for setup and startup:

```bash
REGEN_PREFIX=/your/writable/regen bash codex-cloud/setup.sh
REGEN_PREFIX=/your/writable/regen bash codex-cloud/web.sh
```

The startup command fails clearly if setup has not completed. The setup script
fetches the existing digest-pinned public runtime, verifies manifest/layer/native
library hashes, and installs the Python packages in `codex-cloud/requirements.txt`.
The existing CLI remains available through `codex-cloud/regen.sh`.

## Run it on a host

The same repository and commands can run on a Linux x86-64 host. Set
`REGEN_WEB_HOST=0.0.0.0` and optionally `REGEN_WEB_PORT` when binding behind your
own authenticated HTTPS reverse proxy. The app defaults to loopback and has no
built-in account system; access to it permits CPU-intensive generation and result
downloads. Run one Uvicorn worker: jobs are held in that process's memory. The
launcher already enforces that setting.

GitHub stores the code and runs contract tests; a long-lived server is still
needed to keep an app online. Deployment and a public URL are separate from
committing this feature.

## Execution and acceptance

- `POST /api/jobs` accepts a strict `RegenChamberSpec` JSON object and returns
  HTTP 202 with a random job ID. The browser minimum voxel size is 0.4 mm; the
  existing CLI keeps its original envelope. Invalid specifications return 422,
  missing runtime 503, and a simultaneous generation 409.
- `GET /api/jobs/{id}` returns queued, running, accepted, rejected, or unavailable.
  Only accepted jobs expose the three named artifact routes. Report acceptance,
  builder provenance, non-certification, and the input/spec/STL SHA256 hashes must agree before any
  download becomes available. A prebuilt test-hook result never qualifies.
- Each job runs the existing CLI in a disposable process group. The native worker
  has a 4 GiB virtual-memory limit, a 256 MiB .NET managed-heap limit, and two
  OpenMP/BLAS threads. The existing builder timeout remains 600 seconds; the web
  job timeout is 900 seconds including verification. The parent checks job output
  every 0.5 seconds and stops jobs over 1 GiB; this is a monitored output bound,
  not a filesystem quota. `.NET` requires a sparse 2 TiB memory file, so a global
  `RLIMIT_FSIZE` is unsuitable here.
- Unrelated environment credentials are excluded from the worker. No arbitrary
  commands, user-selected paths, code uploads, or prebuilt meshes are accepted.
  Browser mutations must be same-origin. Assets are served from the repository.
- Up to four job folders are retained; finished results expire after one hour
  when accessed or on later submissions. Failed meshes are removed immediately.
  Output and worker logs live in a temporary `regen-web-*` directory, outside the
  checkout. Server restarts lose job IDs; download results before stopping it.

The UI and report preserve the existing geometry-only scope. Acceptance does not
evaluate gas dynamics, heat transfer, structural strength, or manufacturing, and
does not establish that a chamber can be manufactured or fired safely.

## Tests

After setup:

```bash
source /opt/regen/env.sh  # or /workspace/regen-runtime/env.sh
cd backend
OMP_NUM_THREADS=2 OPENBLAS_NUM_THREADS=2 python -m pytest \
  tests/test_regen_spec.py tests/test_regen_mesh.py tests/test_regen_cli.py \
  tests/test_regen_budget.py tests/test_regen_web.py \
  -q -k 'not adv' -p no:cacheprovider
```

The new workflow `.github/workflows/regen-web.yml` runs verifier and fixture API
tests without a native builder. Real end-to-end validation requires starting the
web app, generating a small example, and inspecting its accepted report, STL
checksum, browser preview, and downloads. Adversarial fixtures deselected by this
focused command remain separate from the passing results.
