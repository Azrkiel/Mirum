# D0 — Baseline and live smoke

Parent: [master plan](00-master-plan.md). Read the master in full first.
Agent: general-purpose (ops). Wave 0. Depends on: nothing. Effort: ≤ 0.5 day.
Branch: `chore/co2-dragster-d0-baseline` (docs-only output).

## Goal

Pin down the facts every later workstream relies on: the exact `main` SHA, that the native
gate is green, how long and how much memory the **current** wedge takes in the worker, and
whether the deployed site's dragster workflow works. Produce one evidence document. Change no
code.

## Read first

- `docs/dragsters/integrated-workflow.md` (setup, API, limits)
- `Dockerfile.verify`, `.github/workflows/ci.yml` (job "Native CAD and offline API regression gate")
- `backend/dragsters/service.py`, `worker.py`, `backend/process_runner.py` (limits)
- `deploy/fly/README.md`

## Owns

- `docs/plans/co2-dragster/evidence/D0-baseline.md` (new)
- Your row in master § 7.

Must not touch any code, Fly secrets, or Fly config.

## Tasks

1. **Record source state.** `git rev-parse origin/main`; `gh run list -R Azrkiel/Mirum --branch main --limit 3`; record the CI run id and the native-gate pytest summary line (`gh run view <id> --log | grep -E "passed|failed"`).
2. **Build the verify image locally.** `docker build -f Dockerfile.verify -t mirum-verify:d0 .` Record build success/time. If disk fills: `docker builder prune -f`, then retry once; if still failing, record the exact error and continue with step 3 in a Linux venv per `docs/dragsters/integrated-workflow.md` § Setup.
3. **Run the existing dragster tests** in the image (offline):
   `docker run --rm --network none --tmpfs /tmp:rw,size=1g mirum-verify:d0 python -m pytest -q backend/tests/test_dragster_*.py frontend/test_dragster_*.py`
   Record counts/skips. Zero skips expected.
4. **Measure the worker budget.** Inside the image, run the fixed worker on the v1 example 5 times, capturing wall time and peak RSS:
   `docker run --rm --network none --tmpfs /tmp:rw,size=1g mirum-verify:d0 sh -c 'for i in 1 2 3 4 5; do mkdir -p /tmp/d0/$i; /usr/bin/time -v python backend/dragsters/worker.py /tmp/d0/$i < backend/dragsters/examples/wedge.json 2>&1 | grep -E "Elapsed|Maximum resident"; done'`
   (If `/usr/bin/time` is missing, use a tiny Python wrapper with `resource.getrusage(RUSAGE_CHILDREN)`.) Record median and max. This is the yardstick for master invariant 7.
5. **Fly machine sizes.** If `fly auth whoami` works: `fly scale show -a mirum-api` and `fly status -a mirum-api`, `fly status -a mirum`. Record CPU/memory and current image/release. If the token is expired, record "blocked: fly auth expired — user must run `fly auth login`" and continue.
6. **Live smoke (only if step 5 works).** Open https://mirum.fly.dev, choose **CO2 dragster**, click **Generate accepted wedge** with the defaults, and confirm: preview renders, 4 downloads present, report `passed: true`. Do it once (the endpoint makes no provider calls, so it costs only machine time). Save a screenshot under `docs/plans/co2-dragster/evidence/d0-live.png`. If the backend machine is asleep the first request may take ~20 s.
7. **PR #19 note.** Record whether PR #19 (`deploy/open-access`) is still open. Do not merge it; D9 handles it.

## Exit criteria

- [x] Evidence doc lists: main SHA, CI run id + native gate counts, local image build result, dragster test counts (0 skips), worker median/max seconds and peak MiB, Fly machine sizes (or blocked note), live smoke result (or blocked note), PR #19 state.
- [x] No code changes in the diff.
- [x] Master § 7 D0 status updated.

## Done report

Branch, SHA, PR, and the numbers above in a 10-line summary.
