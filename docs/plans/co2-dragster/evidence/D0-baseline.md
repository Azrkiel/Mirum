# D0 evidence — baseline and live smoke

Date: 2026-11-09 (US Eastern; UTC timestamps below are 2026-11-10). Workstream: [D0](../D0-baseline.md).
Branch `chore/co2-dragster-d0-baseline`, based on `origin/docs/co2-dragster-master-plan` @ `95ccd90`
(the plan is not on `main` yet). No code changed.

## Summary

| Item | Result |
| --- | --- |
| `origin/main` | `01f889e994efef0507d47cc0cc8d5313df30ccf5` |
| CI on main | run `42124170446` (CI, push, 2026-11-05) success; job "Native CAD and offline API regression gate" (`127583940624`): **1580 passed, 1 deselected**, 416 warnings in 697 s |
| Local verify image | built `mirum-verify:d0` (`07408769f2d8`, 3.38 GB) in 30 s on warm cache, **with a local CA workaround** (see § 2) |
| Dragster tests (local, offline) | **199 passed, 8 failed, 0 skipped** (187 s). All 8 failures = worker killed by the 1536 MiB `RLIMIT_AS` on this 16-CPU host (see § 4, finding F1). Not reproducible in CI (green) or on Fly (live smoke passed) |
| Worker budget (v1 example, 5 runs, no RLIMIT) | wall **median 2.75 s, max 2.92 s**; peak RSS **median 524 MiB, max 524 MiB** |
| Worker virtual peak (VmPeak) | **1562–1626 MiB** (6 runs), above the 1536 MiB `RLIMIT_AS` the service applies |
| Fly `mirum-api` | 1 machine `85d3e9f4577938`, shared, **2 vCPU / 4096 MB**, iad, release v5, image `deployment-01M6QJCMX8AMSA69745QRYRKDH` |
| Fly `mirum` | 1 machine `85d3e5a4116018`, shared, **1 vCPU / 1024 MB**, iad, machine version 4, image `deployment-01M6QJE1NF79BGK328KGP65XQR` |
| Live smoke (https://mirum.fly.dev) | **pass**: preview rendered, 4 downloads, report `passed: true` ([screenshot](d0-live.png)) |
| PR #19 (`deploy/open-access`) | **OPEN**, head `894a654`, not merged (D9 handles it) |

## 1. Source state

```text
$ git rev-parse origin/main
01f889e994efef0507d47cc0cc8d5313df30ccf5

$ gh run list -R Azrkiel/Mirum --branch main --limit 3
completed success feat: integrate bounded deterministic wedge workflow  CI                        main push 42124170446 13m56s 2026-11-05T22:45:28Z
completed success Merge pull request #13 from Azrkiel/port/pr4-gaps     CI                        main push 41775290553 12m36s 2026-11-04T04:47:17Z
completed success Merge pull request #13 from Azrkiel/port/pr4-gaps     PicoGK browser contracts  main push 41775290512 30s    2026-11-04T04:47:17Z

$ gh run view 42124170446 --job 127583940624 --log | grep -E "passed|failed"
1580 passed, 1 deselected, 416 warnings in 697.38s (0:11:37)
```

All four CI jobs on `42124170446` succeeded (native gate, airfoil unit tests, schema gate, aero
stage integration).

## 2. Local verify image

Host: Windows 11 laptop, 16 logical CPUs, on AC power; Docker Desktop 29.7.2, VM memory 7.6 GiB.

- `docker build -f Dockerfile.verify -t mirum-verify:d0 .` **failed** (17 s) at the
  `requirements-dragster-ui.txt` pip layer: `CERTIFICATE_VERIFY_FAILED` for `pypi.org`
  (Avast Web Shield TLS interception on this laptop; this layer had never been built locally).
  Disk was not the problem, so no prune was needed.
- Workaround (no repo change): a scratch copy of `Dockerfile.verify` that adds two lines per stage
  before the first network step, with the CA bundle supplied as a named build context:

  ```diff
  +COPY --from=certs ca-bundle.pem /etc/ssl/local-ca-bundle.pem
  +ENV NODE_EXTRA_CA_CERTS=/etc/ssl/local-ca-bundle.pem      # before `RUN npm ci`
  ...
  +COPY --from=certs ca-bundle.pem /etc/ssl/local-ca-bundle.pem
  +ENV PIP_CERT=/etc/ssl/local-ca-bundle.pem                 # before the dragster-ui pip layer
  ```

  `docker build -f <scratch>/Dockerfile.verify.d0 --build-context certs=<dir> -t mirum-verify:d0 .`
  → **success in 30 s** (earlier layers cached). Without the Node line, `npm ci` died with
  "Exit handler never called!". The image is otherwise identical to the CI gate image.
  **Later agents on this laptop need the same workaround.**

## 3. Dragster tests (offline)

```text
docker run --rm --memory 3g --network none --tmpfs /tmp:rw,exec,size=1g,mode=1777 mirum-verify:d0 \
  python -m pytest -q -p no:cacheprovider -rs backend/tests/test_dragster_*.py frontend/test_dragster_*.py
8 failed, 199 passed, 34 warnings in 187.33s (0:03:07)
```

Two deviations from the subplan's command: `--memory 3g`, required by the lead for this 8 GB
Docker VM, and `exec` on the tmpfs, which matches the CI gate. **0 skips.**

Failing tests: all of them go through `service.generate` → `ProcessRunner(memory_mb=1536)`:

```text
backend/tests/test_dragster_api.py::test_real_api_downloads_and_revision          ('Dragster worker: TIMEOUT')
backend/tests/test_dragster_integrated.py::test_native_delivery_and_binding        (worker rc != 0)
backend/tests/test_dragster_integrated.py::test_required_publication_failure[step|glb|spec|report]
backend/tests/test_dragster_integrated.py::test_concurrent_native_attempts_remain_distinct  (assert -9 == 0)
backend/tests/test_dragster_integrated.py::test_changed_native_artifact_is_not_published
```

The same tests pass in CI (`42124170446`). Root cause is in § 4 (F1).

## 4. Worker budget (master invariant 7 yardstick)

`/usr/bin/time` is absent in the image, so a Python wrapper ran `worker.py` on
`backend/dragsters/examples/wedge.json` 5 times (fresh interpreter per run, `RUSAGE_CHILDREN`).
There was no `RLIMIT_AS`; container `--memory 3g`; `OMP_NUM_THREADS=1`.

| Run | rc | wall (s) | peak RSS (MiB) |
| --- | --- | --- | --- |
| 1 | 0 | 2.92 | 523 |
| 2 | 0 | 2.89 | 524 |
| 3 | 0 | 2.75 | 524 |
| 4 | 0 | 2.70 | 524 |
| 5 | 0 | 2.72 | 524 |
| **median / max** | | **2.75 / 2.92** | **524 / 524** |

Each run wrote one `dragster-<id>/` result directory.

### F1 — `RLIMIT_AS` 1536 MiB is exceeded by address-space reservation on a 16-CPU host

The `/proc/<pid>/status` sampler polled every 10 ms during unrestricted runs:

| Visible CPUs (`--cpuset-cpus`) | VmPeak (MiB) | VmHWM / RSS (MiB) | max threads |
| --- | --- | --- | --- |
| 0-1 | 1626, 1626, 1562 | 518–519 | 16 |
| 0-15 | 1562, 1626, 1562 | 519 | 16 |

`os.cpu_count()` is 16 inside the container regardless of cpuset. The worker starts 16 threads.
glibc's default malloc arena limit (8 × online CPUs) lets those threads reserve 64 MiB arenas,
so the **virtual** peak (1.53–1.59 GiB) crosses the 1.5 GiB `RLIMIT_AS` set by
`backend/_memory_entry.py`. The real RSS is only ~0.5 GiB.

Through the real `ProcessRunner(timeout=30, memory_mb=1536)`:

| Condition | Results (rc / code / wall) |
| --- | --- |
| memory_mb=1536, 2 CPUs | TIMEOUT 30.05 s; rc 1 2.37 s; rc 1 2.38 s |
| memory_mb=1536, 4 CPUs | rc 1 2.18 s; SIGSEGV 4.92 s; SIGSEGV 4.17 s |
| memory_mb=1536, 8 CPUs | TIMEOUT 30.06 s; rc 1; rc 1 |
| memory_mb=1536, 16 CPUs, timeout 120 | SIGSEGV 4.37 s; TIMEOUT 120 s (hang) |
| memory_mb=4096 (diagnostic only) | rc 0, 2.72 s |
| `_memory_entry` 1536 MiB, `MALLOC_ARENA_MAX` unset (diagnostic) | rc 139, rc 124 (hang), rc 0 |
| `_memory_entry` 1536 MiB, `MALLOC_ARENA_MAX=2` (diagnostic) | rc 0, rc 0, rc 0 |

The traceback under the limit is a chain of `MemoryError`s while importing `trimesh` (the preview
step, after CadQuery/OCP are loaded).

The failure is flaky and depends on the host: CI's ubuntu-latest runners (4 vCPU) pass, and so
does Fly `mirum-api` (2 vCPU, § 6). It does **not** violate the RSS budget in invariant 7
(≤ 1.0 GiB; measured 0.51 GiB), but it leaves **< 0 MiB of address-space headroom** on 16-CPU
machines, and lofted builds (D2+) will add to it.

**Consequence for later agents:** on this laptop the integrated or API dragster tests fail
intermittently with TIMEOUT, SIGSEGV or rc 1. These failures are unrelated to the agent's change.
**Open question for the lead.** D0 changes no code, so these are options only:
(a) add `MALLOC_ARENA_MAX='2'` to the worker env in `backend/dragsters/service.py` (one line,
next to `OMP_NUM_THREADS`), with a regression test. This does not raise ProcessRunner limits
(invariant 7);
(b) have agents on this laptop measure with a WSL `processors=4` limit. (b) only hides the problem.
Recommend (a), owned by D2 or D5. D2 measures budgets anyway.

## 5. Fly machine sizes

`fly auth whoami` worked (no login needed).

```text
fly scale show -a mirum-api   app: count 1, shared, 2 CPUs, 4096 MB, iad
fly status -a mirum-api       machine 85d3e9f4577938, version 5, iad, stopped (auto-stop) before smoke
                              image mirum-api:deployment-01M6QJCMX8AMSA69745QRYRKDH
fly scale show -a mirum       app: count 1, shared, 1 CPU, 1024 MB, iad
fly status -a mirum           machine 85d3e5a4116018, version 4, iad, stopped (auto-stop) before smoke
                              image mirum:deployment-01M6QJE1NF79BGK328KGP65XQR
```

No secrets, config, or scale were changed.

## 6. Live smoke

At 2026-11-10 02:24 UTC I opened https://mirum.fly.dev. There was no access token prompt, as
expected from PR #19's open-access config. I chose **CO2 dragster** and clicked
**Generate accepted wedge** once, with the default v1 parameters (200 × 42 × 32 mm, nose 12,
clearance 3, axles 40/175 at Z 9, bore 3, pocket Ø20 × 52 at Z 23, wall 2).

- Cold start: `mirum-api` proxy "Starting machine" 02:24:36, uvicorn ready 02:24:48 (~12 s),
  `POST /dragsters/generate` 200 at 02:24:57. The accepted result appeared in the UI about
  45 s after the click, including both cold starts.
- UI: "Required STEP checks passed; STEP-derived preview and all four artifacts verified."
  Plotly STEP-derived preview rendered.
- Downloads present: **Download STEP, Download GLB, Download accepted parameters, Download report** (4).
- Accepted revision `8f4abc4254634ff3bb76e26a7224d2d8`. The report has every `passed` key `true`
  (8 occurrences, including top level), `generator_version: dragster_wedge_v1`,
  `engineering_certified: false`, `volume_mm3: 209869.957`, and bounds X 0…200, Y −21…21, Z 3…35.
- Screenshot: [`d0-live.png`](d0-live.png).

There were no provider calls; the run used only machine time.

## 7. PR #19

```text
$ gh pr view 19 -R Azrkiel/Mirum --json state,title,headRefOid,mergedAt
{"state":"OPEN","title":"deploy(fly): open the public UI, no access token","headRefOid":"894a654d…","mergedAt":null}
```

Not touched. D9 reconciles it before any deploy from `main`.

## Exit criteria

- [x] Evidence doc lists main SHA, CI run id + native gate counts, local image build result,
  dragster test counts (0 skips; 8 environment-specific failures explained in F1), worker
  median/max seconds and peak MiB, Fly machine sizes, live smoke result, and PR #19 state.
- [x] No code changes in the diff (this file, `d0-live.png`, and the master § 7 D0 status cell only).
- [x] Master § 7 D0 status updated.
