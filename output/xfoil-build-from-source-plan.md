# Plan — XFOIL build-from-source (Path A)

## Context

`/analysis/aero` is wired end-to-end on `feat/post-generate-xfoil-pdf`
(commits `f7ae311` → `267d5e4`) but every NACA invocation returns
`{"status":"error","detail":"XFOIL failed: XfoilRunError: ..."}`
because the Debian-packaged `xfoil 6.99.dfsg+1-3+b1` SIGFPEs the moment
`OPER` enters the panel-method matrix setup. The handoff
(`enhance-geometry-handoff.md` §6) chased this through three dead-ends:

- `debian:bookworm-slim` ships the same broken `.deb` (not a Trixie issue)
- `pip install xfoil` / `xfoil-python` — broken / nonexistent packages
- `libxfoil` in a py3.9 sidecar — α=0 works, α>0 diverges (`viscrms > 1`,
  output α drifts to 16–57°). Pure aerodynamic-options issue, not a
  marshalling bug — needs domain expertise to calibrate.

§6.D recommends **Path A**: compile XFOIL 6.99 from MIT source inside a
multi-stage Docker build and `COPY` the verified binary into the runtime
image. This plan executes Path A on a fresh branch off post-merge `main`,
so the work happens after `feat/geometry-cert-rich-schema`,
`feat/post-generate-xfoil-pdf`, and `chore/hardening-from-handoff` have
all landed (handoff §7 ordering — see [Prerequisites](#prerequisites)).

Intended outcome: a `docker compose up --build` produces an image whose
`/usr/local/bin/xfoil` returns `Cl≈0.22, Cd≈0.0065` for NACA 0012 at α=2
(handoff §6.D.3), and `/analysis/aero` returns `{"status":"ok",
"aero_report":{...}, "pdf_b64":"..."}` for the wing prompt instead of
the current `XfoilRunError`.

## Prerequisites

Do these before starting this plan (they are NOT in scope here):

1. Merge `feat/geometry-cert-rich-schema`, `feat/post-generate-xfoil-pdf`,
   and `chore/hardening-from-handoff` to `main` per handoff §7.A.
2. Image rebuild after merge per §7.E so PYTHONPATH, pythonocc-core,
   fpdf2, matplotlib bake in. The `docker-compose.override.yml` then
   only needs to retain `read_only: false`.

This plan starts from a clean post-merge `main`.

## Branch + worktree setup

```powershell
cd C:\Users\Kevin\Developments\T2CIII
git fetch origin
git worktree add .claude\worktrees\fix+xfoil-build-from-source `
  -b fix/xfoil-build-from-source origin/main
cd .claude\worktrees\fix+xfoil-build-from-source
```

## The change

Single file: `backend/Dockerfile`. Convert the existing single-stage
build into a two-stage build. The current file is 71 lines; the new
file is ~95 lines.

### 1. Add the XFOIL builder stage at the top of the file

Insert above the existing `FROM continuumio/miniconda3`:

```dockerfile
# ── Stage 1: build XFOIL 6.99 from MIT source ─────────────────────────────────
# The Debian-packaged xfoil (6.99.dfsg+1-3+b1, present in bookworm AND trixie)
# SIGFPEs on every OPER invocation — gfortran ABI mismatch with libgfortran5.
# Compile from MIT's reference tarball instead. See enhance-geometry-handoff.md §6.
FROM continuumio/miniconda3 AS xfoil-builder

RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        gfortran make wget ca-certificates \
    && rm -rf /var/lib/apt/lists/*

# Pinned upstream tarball. SHA256 must match — if MIT swaps the file
# under us, the build fails here instead of producing a silent miscompile.
# First-build workflow: leave SHA256 empty, run the build, paste the actual
# digest from `sha256sum: WARNING:` line into the Dockerfile, rebuild.
ENV XFOIL_TARBALL_URL=https://web.mit.edu/drela/Public/web/xfoil/xfoil6.99.tgz
ENV XFOIL_TARBALL_SHA256=__FILL_IN_AFTER_FIRST_BUILD__

# --no-check-certificate is acceptable for the build stage only: integrity
# is enforced by the SHA256 below. The verified binary is what ships in
# stage 2; this stage is discarded. Handoff §6.D.4(b).
# Makefile patches:
#   -std=legacy + -fallow-argument-mismatch  → gfortran ≥10 compat with
#       pre-Fortran-90 source
#   -fno-stack-arrays                        → keep large panel-method
#       arrays off the 8MB default stack
RUN wget -q --no-check-certificate -O /tmp/xfoil.tgz "$XFOIL_TARBALL_URL" && \
    echo "$XFOIL_TARBALL_SHA256  /tmp/xfoil.tgz" | sha256sum -c - && \
    tar -xzf /tmp/xfoil.tgz -C /opt/ && \
    cd /opt/Xfoil/bin && \
    sed -i 's/-O/-O -fno-stack-arrays -fallow-argument-mismatch -std=legacy/' Makefile && \
    make xfoil && \
    # Build-stage smoke test: fail the image build immediately if the
    # binary still SIGFPEs or produces nonsensical Cl. Expected Cl≈0.22 at
    # α=2 for NACA 0012, Re=∞ (no VISC) — match 0.1–0.2x to be tolerant
    # of XFOIL's variable column padding.
    printf "PLOP\nG F\n\nNACA 0012\nOPER\nALFA 2\n\nQUIT\n" | ./xfoil 2>&1 | \
      grep -Eq "CL\s*=\s*0\.[12]" || \
      { echo "FATAL: XFOIL self-test failed in builder stage"; exit 1; }
```

### 2. Drop `xfoil` from the runtime apt-get install line

`backend/Dockerfile:7-13` currently reads:

```dockerfile
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
        libgl1 \
        xfoil \
        libosmesa6 \
        libgles2 \
    && rm -rf /var/lib/apt/lists/*
```

Remove the `xfoil \` line. Also delete the four-line comment block at
`backend/Dockerfile:19-21` that documents the apt install (`xfoil: Mark
Drela's...`) — it is replaced by the builder stage comment block.

### 3. Copy the verified binary into the runtime image

Add immediately after the apt-get block (before the conda env creation):

```dockerfile
# XFOIL binary, compiled and self-tested in the xfoil-builder stage.
# Lands at /usr/local/bin/xfoil — analysis/airfoil.py:_resolve_xfoil_bin
# returns plain 'xfoil' on Linux, which PATH resolves to this binary.
COPY --from=xfoil-builder /opt/Xfoil/bin/xfoil /usr/local/bin/xfoil
```

No other changes. `analysis/airfoil.py:_resolve_xfoil_bin` already
returns `"xfoil"` on Linux (line 674), so `PATH` discovery resolves to
`/usr/local/bin/xfoil`. No env var, no config change.

## Verification

Run in order. Each step must pass before the next.

### V1 — Build the image

```powershell
cd C:\Users\Kevin\Developments\T2CIII\.claude\worktrees\fix+xfoil-build-from-source
$env:COMPOSE_PROJECT_NAME = "t2ciii"
docker compose build backend 2>&1 | tee build.log
```

First build will fail at the `sha256sum -c` line with
`WARNING: 1 computed checksum did NOT match`. Capture the actual SHA256
from the line above and paste it into the Dockerfile, then rebuild.
The build is expected to take ~3–4 min (Fortran compile is fast,
miniconda layers cache).

### V2 — Standalone binary smoke test inside the container

```powershell
docker compose up -d backend
MSYS_NO_PATHCONV=1 docker exec t2ciii-backend-1 bash -c `
  'printf "PLOP\nG F\n\nNACA 0012\nOPER\nALFA 2\n\nQUIT\n" | xfoil 2>&1 | tail -20'
```

Expected: the last iteration line should show `CL = 0.22xx` and
`CD = 0.0065`–`0.0080` (handoff §6.D.3 / §6.E reference values). The
Cd range is wider than the strict published 0.0065 because the
single-α call uses XFOIL's default Re (∞) — viscous mode is off, so
Cd is inviscid-only. **There must NOT be a SIGFPE backtrace.**

### V3 — In-container pytest of `analysis/` (Group B goes live)

The existing test suite at `analysis/tests/test_airfoil.py` has a
Group B class — `TestXfoilGoldenNaca0012` (B1–B7) — gated by
`@unittest.skipUnless(_XFOIL_PRESENT)`. Until this branch the helper
at line 71 always returned False inside CI (the Debian binary SIGFPE'd
before the `"XFOIL" in stdout` check), so Group B has never actually
run. With the source-built binary on PATH it executes for real.

Run inside the running container so the test sees the same `xfoil`
binary that production code will:

```powershell
docker exec t2ciii-backend-1 bash -c `
  '/opt/conda/envs/cadenv/bin/python -m pytest /opt/analysis/tests/test_airfoil.py -v 2>&1 | tail -40'
```

Expected (every assertion live):
- B1: `Cl(α=5°) ≈ 0.57 ± 0.03` — thin-airfoil + XFOIL correction
- B2: `Cd(α=0°) ≈ 0.006 ± 0.001`
- B3: `stall_alpha ∈ [15°, 17°]`
- B4: `PolarResult` contract — all 15 keys present, types correct
- B5–B7: monotonic Cl up to stall + converged_count ≥ 15

If any of B1–B7 fails, the source-built binary diverges from the
documented XFOIL 6.99 reference values — investigate before continuing
to V4. A run where Group B is **skipped** rather than executed
(message: "XFOIL binary not found") means the `COPY --from=xfoil-builder`
landed in the wrong PATH location — re-check `which xfoil` inside the
container.

### V4 — Multi-NACA sweep from inside the container

Group B only exercises NACA 0012. The runner code (`_build_xfoil_script`,
`_parse_polar_file`) was written against documentation, never tested
live across a NACA family. Sweep a representative set from inside the
container so any polar-parser drift or stdin-format divergence surfaces:

```powershell
docker exec t2ciii-backend-1 /opt/conda/envs/cadenv/bin/python -c @'
from analysis.airfoil import analyze_naca
env = {"alpha_start":0.0, "alpha_end":12.0, "alpha_step":2.0,
       "reynolds":1e6, "mach":0.0, "n_crit":9.0}
for code in ["0008", "0012", "0018", "2412", "4412", "6412"]:
    r = analyze_naca(code, 200.0, env, timeout_s=60)
    print(f"{code}: converged={r['converged_count']:>2}  "
          f"Cl_max={r['Cl_max']}  stall={r['stall_alpha']}")
'@
```

Expected per row: `converged_count ≥ 5`, `Cl_max` not None, no
`XfoilRunError`. `Cl_max` should rise monotonically with camber for
the 4-digit family at fixed thickness (so 4412 > 2412 > 0012 at the
same α range). A `converged_count` of 0 or 1 on any code is a polar-
parser drift bug, not a XFOIL bug — fix it in
`analysis/airfoil.py:_parse_polar_file` before moving on.

### V5 — End-to-end `/analysis/aero` against live Gemini

Only after V3 + V4 pass. This is the production-shape check: it
exercises the wiring from `backend/main.py:/analysis/aero` →
`analysis.airfoil.analyze_naca` → the source-built XFOIL, plus the
PDF report path that pulls in `analysis/pdf_report.py` + `analysis/plots.py`.

Generate a wing first (so a STEP file exists at the path the endpoint
expects), then hit the endpoint:

```powershell
curl -s -N -m 180 -X POST http://localhost:8000/generate `
  -H "Content-Type: application/json" `
  -d '{"prompt":"NACA 2412 wing, 200 mm chord, 100 mm span, 3 mm thick rib"}' `
  > gen.txt
# scrape session_id from the SSE stream, then:
curl -s -m 60 -X POST http://localhost:8000/analysis/aero `
  -H "Content-Type: application/json" `
  -d '{"naca_code":"2412","chord_mm":200,"span_mm":100}' | python -m json.tool
```

Expected: `{"status":"ok","aero_report":{...},"pdf_b64":"..."}` with
the polar containing ≥10 converged α points. Currently this returns
`{"status":"error","detail":"XFOIL failed: XfoilRunError: ..."}`.

### V6 — Merge gates (per `.claude/rules/worktree-merge-gate.md`)

```powershell
# (a) schema tests — always required, fast
cd backend
python tests/test_prompts_schema.py
python tests/test_runner_filters.py
python tests/test_md_report.py

# (b) regression slice — Domain D owns the aero prompts
python -m eval_harness.runner --regression-for D `
  --report-md "../output/qa-reports/$(Get-Date -Format yyyy-MM-dd)-xfoil-build.md"
# Then diff against the most-recent baseline on main. No newly-failing IDs.

# (c) security review — recommended but not strictly required by the
# matrix (Dockerfile changes aren't on the trigger list). The new build
# pulls a third-party tarball with --no-check-certificate, mitigated by
# SHA256 pinning. Dispatch the agent if available:
#   Agent({ subagent_type: "mirum-security-reviewer",
#     description: "Review xfoil build-from-source",
#     prompt: "Audit backend/Dockerfile diff between origin/main and
#       fix/xfoil-build-from-source. Scope: multi-stage build, third-party
#       tarball with --no-check-certificate + SHA256 verify, binary copied
#       to runtime stage." })
```

## Critic loop — `analysis/` folder integration audit

After V3+V4 pass and **before** opening the PR, run a Critic pass
scoped to the `analysis/` folder only. The runner code in
`analysis/airfoil.py` was written against XFOIL 6.99 documentation but
the live Group B suite was effectively never run in CI (the Debian
binary failed the `_xfoil_available()` probe at line 71, so every
prior CI run silently skipped B1–B7). With the source-built binary
the assumptions baked into `_build_xfoil_script` and `_parse_polar_file`
are exercised for the first time — that's the gap this critic closes.

Dispatch the agent (general-purpose subagent — there is no
`xfoil-critic` agent and the `mirum-security-reviewer` is the wrong
scope; this is a correctness audit, not a security audit):

```
Agent({
  description: "Critic on analysis/ folder vs source-built XFOIL",
  subagent_type: "general-purpose",
  prompt: "
    Audit the analysis/ folder ONLY — these four files and nothing else:
      analysis/airfoil.py
      analysis/pdf_report.py
      analysis/plots.py
      analysis/tests/test_airfoil.py
      analysis/tests/mock_xfoil.py
    against the freshly-source-built XFOIL 6.99 binary now installed at
    /usr/local/bin/xfoil in the running container t2ciii-backend-1.

    Background: until this branch, the binary on PATH was the Debian
    6.99.dfsg+1-3+b1 package which SIGFPE'd on every OPER invocation,
    so the runner's stdin/polar assumptions were never exercised live.

    For each focus area below, answer yes/no with concrete evidence
    captured from inside the container (use `docker exec t2ciii-backend-1
    bash -c '...'` for every observation). Quote the exact lines you
    examined.

    1. _build_xfoil_script (airfoil.py:377) — does source-built XFOIL
       accept this exact command sequence?
         - Run `printf '<the script>' | xfoil` inside the container,
           capture stdout, confirm OPER subcommands fire in the
           expected order (PLOP -> NACA load -> OPER -> VISC + Re ->
           MACH -> [VPAR + N] -> ITER -> PACC + path + blank -> ASEQ
           -> PACC -> blank -> QUIT).
         - Specifically check: does VISC's interactive prompt match
           the runner's 'enable, then Re on next line' assumption,
           or does source-built XFOIL prompt 'Enter VISC Reynolds
           number' on the same line?
         - Does PACC followed by filename + blank line still suppress
           the drag-dump prompt?

    2. _parse_polar_file (airfoil.py:455) — does the PACC file the
       source-built binary writes have the 7-column layout the parser
       expects (alpha CL CD CDp CM Top_Xtr Bot_Xtr)?
         - Capture an actual polar.txt from a NACA 0012 run inside
           the container and diff against the parser's regex
           assumptions (the dash-line separator detection at line 488,
           the 5-or-more-tokens check at line 501, the index-0/1/2/4
           positions for alpha/Cl/Cd/Cm).
         - If the column layout changed even slightly (e.g. column
           padding, extra Mach column, different separator character),
           the parser silently drops rows.

    3. _resolve_xfoil_bin (airfoil.py:657) — confirm PATH lookup in
       the container resolves to /usr/local/bin/xfoil (the COPY
       target), not to a stale /usr/bin/xfoil from a layer cache or
       an apt remnant. Run `which xfoil` and `xfoil --version` (or
       equivalent) inside the container.

    4. mock_xfoil.py (analysis/tests/mock_xfoil.py) — is the mock
       still representative of source-built XFOIL behaviour now that
       a real binary exists? Mocks that diverge from reality hide
       real bugs in tests that rely on them. List which production
       tests reach for the mock vs the live binary, and whether the
       mock's stdout/polar shape now matches what source-built XFOIL
       actually emits.

    5. analyze_wing_step (airfoil.py:783) — exercise the
       STEP -> .dat -> polar path on a real wing STEP produced by
       /generate. Run /generate to produce one, locate the .step file
       under /tmp/mirum/work/<uuid>/, call analyze_wing_step on it
       inside the container, and confirm the polar matches NACA 2412
       reference values to within ±10%.

    Output a single table with columns:
       severity (CRITICAL / HIGH / MEDIUM / LOW) | file:line | issue | suggested fix

    Hard scope rules:
    - Do NOT propose changes outside analysis/.
    - backend/main.py is out of scope.
    - backend/Dockerfile is out of scope (that's this PR's concern).
    - If a finding is 'the binary is wrong', that's the wrong critic —
      surface it but mark it as outside the analysis/ folder scope.
  "
})
```

Resolve all CRITICAL and HIGH findings before opening the PR. MEDIUM
findings are deferrable only with a tracked follow-up. LOW are
optional.

## Rollback

If V2 fails (build succeeds but binary still misbehaves) or any of
V3 / V4 / V5 fail in a way that cannot be addressed by a fix in
`analysis/`, revert is local: drop the branch, nothing on `main` is
affected. The previous broken-apt-xfoil state is documented in
`analysis/airfoil.py`'s KNOWN ISSUE comment (commit `267d5e4` on the
now-merged feat/post-generate-xfoil-pdf), so the fallback path is
just "leave that comment in place; users still see the existing error
response."

If V6(b) shows new regression failures elsewhere (e.g. a Domain D
prompt that worked with the error-returning endpoint now breaks
because the polar is real), open a separate task — those would be
test-expectation updates, not blockers for this fix.

## Out of scope (explicitly deferred)

- Path B (libxfoil + py3.9 conda) — handoff §6.D explicitly de-ranks
  this; the BL-solver divergence needs aero domain expertise.
- Path C (third-party prebuilt binaries) — last resort per §6.D.
- Cycle 1 tech debt (`brepgprop_*` migration, BRepCheck dedup,
  compound non-manifold) — handoff §5.F; separate plan.
- Windows-side XFOIL — `bin/install_xfoil_windows.ps1` already pulls
  the same MIT tarball at runtime; no change needed.

## Critical files reference

- `backend/Dockerfile` — the only file modified
- `analysis/airfoil.py:519` `run_xfoil_polar()` — the consumer, no change
- `analysis/airfoil.py:657` `_resolve_xfoil_bin()` — already PATH-aware
- `enhance-geometry-handoff.md` §6 — the deep-dive that drove this plan
- `.claude/rules/worktree-merge-gate.md` — the gate definitions used by V4
