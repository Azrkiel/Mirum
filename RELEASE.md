# Mirum RC1 release guide

## Overview

RC1 runs locally from two Docker images. There is no hosted RC1 deployment.
Typed checked design covers rectangular cantilevers, CO₂ dragster bodies, glider
CEM and regenerative chamber geometry. It needs no provider credentials. Each
accepted result satisfies the checks for its specific capability and reviewed
inputs. Results carry no engineering certification or manufacturing approval.

## Requirements

- A Linux x86-64 Docker engine, or Docker Desktop running Linux containers on
  x86-64, with Compose v2 supporting `up --wait` and Docker Buildx. The
  containers have memory limits of 6 GiB for the backend and 1 GiB for the
  frontend. Leave additional memory and disk space for the build and backups.
- Bash, `nproc`, `openssl`, `curl` with `--fail-with-body`, `jq`, `sha256sum`
  and `unzip`. The build needs network access to fetch pinned dependencies
  and sources.
- A clean checkout of the candidate commit and free loopback ports 8000 and
  8501 for the walkthrough. Use a fresh Compose project and an empty volume for
  this walkthrough. Set `COMPOSE_PROJECT_NAME` before starting if you need a
  distinct project name. Keep the project name and repository directory
  consistent across commands.

Run the `release-step` blocks in order from the repository root in the same
Bash session with `set -euo pipefail`. Use a dedicated private work directory
at `${RELEASE_WORK:-/tmp/mirum-release}`. The token stays in the session
environment. Keep shell tracing off. The `release-manual` blocks are for
operator use.

## Clean install

The [release build action](.github/actions/build-release-images/action.yml)
uses Buildx Bake to build the backend with its PicoGK runtime and regen
payload. The same invocation builds the `mirum-backend:b13-eval` evaluation
image. Compose runs `mirum-backend:release` and `mirum-frontend:release`.

```bash release-step=build-backend
builder=$(docker buildx create --driver docker-container)
BUILD_JOBS="$(nproc)" docker buildx bake --builder "$builder" \
  -f picogk-builder/docker-bake.hcl backend-release backend-b13-eval --load --pull
```

```bash release-step=build-frontend
docker build --pull --no-cache -f frontend/Dockerfile -t mirum-frontend:release .
```

## Start

```bash release-step=token
umask 077
mkdir -p -- "${RELEASE_WORK:-/tmp/mirum-release}"
MIRUM_ACCESS_TOKEN=$(openssl rand -hex 32)
export MIRUM_ACCESS_TOKEN
```

```bash release-step=up
docker compose -f compose.release.yml up -d --wait
```

```bash release-step=health
curl --fail-with-body -sS -o /dev/null \
  -H "X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN" http://127.0.0.1:8000/auth/check
curl --fail-with-body -sS -o /dev/null http://127.0.0.1:8501/_stcore/health
```

## Access control

Open the Streamlit app at `http://127.0.0.1:8501` and enter the token in the
**Access token** field. API requests to `http://127.0.0.1:8000` use the
`X-Mirum-Access-Token` header. The same shared token grants access to all
checked records in this deployment. Give it only to trusted operators.
Keep both ports on loopback and handle production tokens through a private
secret store. For a token generated in the shell, transfer it to the browser
with your local clipboard utility without displaying it, then clear the
clipboard. To rotate, load the new token into the shell and recreate the backend
during a maintenance window. Open new browser sessions with the new token.

## Credential-free typed use

The [reference cantilever](examples/engineering/cantilever.json) uses a
200 mm length, 10 mm width, 50 N transverse tip load, 1 mm displacement limit
and 0.01 mm digital dimensional tolerance. It assumes an ideal clamp and linear
elasticity, with an illustrative 70,000 MPa modulus and 120 MPa allowable.

1. Select **Checked design**. Use the prefilled reference values, set the
   material source to **Illustrative example** and enter the example's
   `material_reference` in **Material / allowable source reference**.
   Keep **Choose the smallest passing reviewed thickness** and choices
   `5, 10, 15`. Select **Save draft**.
2. Under **Review the exact draft**, read the requirements, assumptions,
   draft revision and brief SHA-256. Acknowledge the values and select
   **Finalize reviewed brief**.
3. Select **Run reviewed beam checks**. The attempts should show 5 mm and
   10 mm rejected and 15 mm accepted. Inspect the measured STEP preview and
   download `package.zip` or the individual artifacts.

The API sequence below reviews the published example. Before adapting it to
other inputs, review every value and acknowledge the specific returned draft
revision and brief hash.

```bash release-step=api-reference-draft
work=${RELEASE_WORK:-/tmp/mirum-release}
api=http://127.0.0.1:8000/engineering/v1
auth="X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN"
curl --fail-with-body -sS -H "$auth" "$api/capabilities" > "$work/capabilities.json"
jq -e '
  .ai_enabled == false and .ai_available == false
  and ([.capabilities[] | select(.available) | .id] | sort) ==
    ["co2_dragster", "glider_cem", "rectangular_cantilever", "rectangular_plate", "regen_chamber"]
' "$work/capabilities.json" >/dev/null
jq --slurpfile capabilities "$work/capabilities.json" '
  .specification as $spec
  | ($capabilities[0].capabilities[] | select(.id == $spec.family)) as $beam
  | {mode: "form", capability: $beam.id, input: {
      schema_version: "engineering_brief_v1",
      capability: {
        id: $beam.id, version: $beam.version,
        numerical_policy_version: $beam.numerical.version,
        resource_policy_version: $beam.resources.version
      },
      mode: "bounded_thickness",
      candidate_heights_mm,
      required_checks: $beam.required_checks,
      requirements: ($spec | {
        units, length_mm, width_mm, dimensional_tolerance_mm, tip_force_n,
        max_tip_deflection_mm, assumptions,
        material: {youngs_modulus_mpa, allowable_stress_mpa,
                   source_kind: "illustrative", reference: .material_reference}
      })
    }}
' examples/engineering/cantilever.json > "$work/draft-request.json"
curl --fail-with-body -sS -H "$auth" -H 'Content-Type: application/json' \
  --data-binary "@$work/draft-request.json" "$api/drafts" > "$work/draft.json"
jq -e --slurpfile request "$work/draft-request.json" '
  .issues == [] and .draft.content == $request[0].input
  and (.brief_sha256 | test("^[0-9a-f]{64}$"))
' "$work/draft.json" >/dev/null
```

```bash release-step=api-reference-finalize
work=${RELEASE_WORK:-/tmp/mirum-release}
jq --slurpfile capabilities "$work/capabilities.json" '{
  draft_id: .draft.draft_id, draft_revision: .draft.revision, brief_sha256,
  acknowledged_fields: $capabilities[0].review_fields,
  reviewed_at: (now | todateiso8601), reviewed_by: "RC1 reference walkthrough"
}' "$work/draft.json" > "$work/review.json"
curl --fail-with-body -sS -H "X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' --data-binary "@$work/review.json" \
  http://127.0.0.1:8000/engineering/v1/briefs > "$work/brief.json"
jq -e --slurpfile review "$work/review.json" \
  '.brief_sha256 == $review[0].brief_sha256 and .draft_revision == $review[0].draft_revision' \
  "$work/brief.json" >/dev/null
```

```bash release-step=api-reference-job
work=${RELEASE_WORK:-/tmp/mirum-release}
jq '{brief_id, idempotency_key: ("rc1-reference:" + .brief_id)}' \
  "$work/brief.json" > "$work/job-request.json"
curl --fail-with-body -sS -H "X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN" \
  -H 'Content-Type: application/json' --data-binary "@$work/job-request.json" \
  http://127.0.0.1:8000/engineering/v1/jobs > "$work/job.json"
```

```bash release-step=api-reference-poll
work=${RELEASE_WORK:-/tmp/mirum-release}
job_id=$(jq -er '.job_id' "$work/job.json")
deadline=$((SECONDS + 450))
while (( SECONDS < deadline )); do
  curl --fail-with-body -sS --max-time 20 \
    -H "X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN" \
    "http://127.0.0.1:8000/engineering/v1/jobs/$job_id" > "$work/result.json"
  case $(jq -er '.lifecycle' "$work/result.json") in
    completed|cancelled|unavailable) break ;;
  esac
  sleep 2
done
jq -e --slurpfile brief "$work/brief.json" '
  .lifecycle == "completed" and .outcome == "accepted" and .delivery == "available"
  and .brief_sha256 == $brief[0].brief_sha256
  and [.attempts[] | [.candidate.specification.height_mm, .outcome]] ==
    [[5, "rejected"], [10, "rejected"], [15, "accepted"]]
  and .selected_candidate_sha256 == .attempts[-1].candidate.candidate_sha256
' "$work/result.json" >/dev/null
```

```bash release-step=api-reference-download
work=${RELEASE_WORK:-/tmp/mirum-release}
job_id=$(jq -er '.job_id' "$work/result.json")
url="http://127.0.0.1:8000/engineering/v1/jobs/$job_id/artifacts"
auth="X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN"
mkdir -p -- "$work/downloads"
cat > "$work/formats" <<'FORMATS'
zip package.zip
brief brief.json
spec selected_spec.json
source model.py
step model.step
glb preview.glb
attempts attempts.json
report report.json
markdown report.md
manifest manifest.json
FORMATS
jq -e '[.artifacts[] | select(. == true)] | length == 10' "$work/result.json" >/dev/null
: > "$work/fingerprints"
while read -r format filename; do
  file="$work/downloads/$filename"
  curl --fail-with-body -sS -H "$auth" -D "$work/headers" -o "$file" "$url/$format"
  actual=$(sha256sum < "$file")
  actual=${actual%% *}
  header=$(awk 'tolower($1) == "x-content-sha256:" {sub(/\r$/, "", $2); print $2}' "$work/headers")
  expected=$(jq -er --arg file "$filename" '
    .bundle | if $file == "package.zip" then .zip_sha256
    elif $file == "manifest.json" then .manifest_sha256
    else .payloads[] | select(.path == $file) | .sha256 end
  ' "$work/result.json")
  test "$actual" = "$header"
  test "$actual" = "$expected"
  printf '%s %s\n' "$format" "$actual" >> "$work/fingerprints"
  if [[ $format != zip ]]; then
    member=$(unzip -p "$work/downloads/package.zip" "$filename" | sha256sum)
    test "${member%% *}" = "$actual"
  fi
done < "$work/formats"
jq -e --slurpfile run "$work/result.json" '
  .job_id == $run[0].job_id and .brief_sha256 == $run[0].brief_sha256
  and .selected_candidate_sha256 == $run[0].selected_candidate_sha256
  and ([.payloads[] | {path, bytes, sha256}] == $run[0].bundle.payloads)
' "$work/downloads/manifest.json" >/dev/null
```

## Scoped AI setup

AI drafts are off by default. The optional beam proposer prepares a draft
for manual review. Finalization and execution require the same review as
typed input. [B13 run 5 and step 12](docs/plans/checked-engineering-v1/QUALIFICATION.md)
passed their bounded live checks. Final candidate qualification still depends
on the release evidence gates.

Use a dedicated key for one trusted local deployment. Set an approved
rolling 24-hour call cap and one in-flight call. The example cap is three
calls. App defaults with AI enabled are 200 calls and two in flight. A proposal
uses one provider call and may use one schema repair. Each counts against the
cap, which survives restart on the same volume. Each call has a 45-second
deadline and a 4,096-output-token cap. Prompts including instructions are
capped at 8,000 characters and structured replies at 16 KiB. Stop on a
provider failure and use the typed form. Keep this setup and real keys out of
the release CI walkthrough.

The backend shares `GEMINI_API_KEY_HEAVY` with other provider routes. The
override disables dragster interpretation. Use only **Checked design** and
its `/engineering/v1/` API with this key. Routes such as `/generate` and
`/cem/glider/requirements` can spend outside the checked proposal budget.

```bash release-manual=enable-ai
(
  set -euo pipefail
  set +x
  umask 077
  work=${RELEASE_WORK:-/tmp/mirum-release}
  mkdir -p -- "$work"
  cat > "$work/ai.compose.yml" <<'YAML'
services:
  backend:
    environment:
      MIRUM_ENGINEERING_AI_DRAFTS: "1"
      MIRUM_ENGINEERING_AI_DAILY_CALLS: "3"
      MIRUM_ENGINEERING_AI_CONCURRENCY: "1"
      MIRUM_DRAGSTER_INTERPRET_DAILY_CAP: "0"
      GEMINI_API_KEY_HEAVY: ${GEMINI_API_KEY_HEAVY:?required}
YAML
  # The key never touches the command line or shell history, but once exported it is in the
  # backend container's environment: anyone with access to the Docker socket can read it
  # (docker inspect, /proc/<pid>/environ). Disable AI drafts and revoke the key when done.
  read -rs -p 'Gemini key: ' GEMINI_API_KEY_HEAVY
  printf '\n'
  export GEMINI_API_KEY_HEAVY
  docker compose -f compose.release.yml stop -t 60 backend
  docker compose -f compose.release.yml -f "$work/ai.compose.yml" up -d --wait backend
)
```

In a new browser session, select **Checked design**, **AI-assisted draft**,
then **Propose draft**. Review the resulting fields before acknowledging
them. Recreate the backend from the base Compose file when the scoped
session ends.

```bash release-manual=disable-ai
docker compose -f compose.release.yml stop -t 60 backend
docker compose -f compose.release.yml up -d --wait --force-recreate backend
```

## Exact versions

| Component | Pin |
| --- | --- |
| Python, both images | 3.10.22, base `python:3.10-slim-bookworm@sha256:5be5aaecb962a41567bc8041f1e9e3432cb085b037fe057f9a562630b2e4b370` |
| CadQuery | 2.6.1 |
| OCP (`cadquery-ocp`) | 7.8.1.1.post1 |
| NumPy | 2.2.6 |
| google-genai | 2.8.0 |
| Streamlit | 1.39.0 |
| PicoGK | 26.2, native runtime hash in [picogk-runtime.sha256](picogk-builder/picogk-runtime.sha256) |

See the hashed [backend lock](requirements-release.lock), [frontend lock](frontend/requirements.lock)
and [viewer lock](frontend/viewer_assets/package-lock.json). The viewer assets
are served locally. The qualified AI settings use `gemini-4-hydrogen`,
temperature 0.4, low thinking and `engineering_extraction_v1` /
`engineering_proposal_v1`.

## Limits

| Limit | Release default |
| --- | --- |
| Backend | One API process, one replica and exclusive ownership of its volume |
| Checked job admission | One active job with no queue, six admissions per client per rolling hour in memory, plus ten job submissions per minute per client |
| Job deadline | 420 seconds from admission |
| Run storage | 32 MiB per run, 256 MiB for regen, including package and metadata |
| Total run storage | 512 MiB or 50 retained run reservations, whichever fills first |
| Retention | Six hours for bundles, seven days for compact job and idempotency state, four hours for unfinalized drafts by default |
| Request limits | 64 KiB HTTP body, 16 KiB typed input |

Compose uses health checks, an init process, read-only roots, dropped
capabilities and a `restart: "no"` policy for both services.

## Volume creation and ownership

Compose creates the `mirum_engineering` volume and normally names it
`<project>_mirum_engineering` in Docker. The backend mounts it at
`/tmp/mirum/engineering`. Docker initializes an empty volume from the image's
directory owned by UID/GID 10001. The backend runs as 10001:10001 and requires
the volume to have the same owner with group and other write bits clear.
Only one backend may hold the volume's `owner.lock` at a time.

The backend's `/tmp` and `/tmp/mirum` are tmpfs mounts.
`/tmp/mirum/work` is disposable scratch. Back up the whole engineering
volume, including SQLite state and package files.

## Backup and restore

Pause submissions and wait for active work before backing up. Stop the backend
so SQLite and the package directory stay consistent during the copy. Keep the
archive private. It can contain reviewed input and generated files. Restore a
trusted archive to a fresh volume, preserving numeric ownership and modes.

The backup step saves the volume name and archive in the work directory.
Keep the reference job ID in `result.json` and its known-good `fingerprints`
alongside the archive for this verification. Run `up -d --wait backend` with
the base Compose file after a backup-only operation. The restore step mounts
a fresh volume and checks the same ten SHA-256 fingerprints. In an actual
recovery, continue using the restored volume with `restore.compose.yml`.
Check the remaining AI budget before re-enabling AI, since a snapshot contains
the ledger as of backup time.

```bash release-step=backup-volume
work=${RELEASE_WORK:-/tmp/mirum-release}
compose=(docker compose -f compose.release.yml)
container=$("${compose[@]}" ps --quiet backend)
source_volume=$(docker inspect --format \
  '{{range .Mounts}}{{if and (eq .Type "volume") (eq .Destination "/tmp/mirum/engineering")}}{{.Name}}{{end}}{{end}}' \
  "$container")
test -n "$source_volume"
docker volume inspect "$source_volume" >/dev/null
printf '%s\n' "$source_volume" > "$work/source-volume"
"${compose[@]}" stop -t 60 backend
docker run --rm --network none --read-only --user 0:0 --entrypoint tar \
  --mount "type=volume,source=$source_volume,target=/volume,readonly" \
  mirum-backend:release --numeric-owner -C /volume -cpf - . > "$work/engineering.tar"
```

```bash release-step=restore-volume
work=${RELEASE_WORK:-/tmp/mirum-release}
job_id=$(jq -er '.job_id' "$work/result.json")
source_volume=$(cat "$work/source-volume")
compose=(docker compose -f compose.release.yml)
"${compose[@]}" stop -t 60 backend
restore_volume="$source_volume-restore-$(openssl rand -hex 8)"
if docker volume inspect "$restore_volume" >/dev/null 2>&1; then
  printf 'Restore target already exists: %s\n' "$restore_volume" >&2
  exit 1
fi
docker volume create "$restore_volume" > "$work/restore-volume"
docker run --rm -i --network none --read-only --user 0:0 --entrypoint tar \
  --mount "type=volume,source=$restore_volume,target=/volume" \
  mirum-backend:release --numeric-owner -C /volume -xpf - < "$work/engineering.tar"
cat > "$work/restore.compose.yml" <<YAML
volumes:
  mirum_engineering:
    external: true
    name: "$restore_volume"
YAML
"${compose[@]}" -f "$work/restore.compose.yml" up -d --wait --force-recreate backend
container=$("${compose[@]}" -f "$work/restore.compose.yml" ps --quiet backend)
mounted_volume=$(docker inspect --format \
  '{{range .Mounts}}{{if eq .Destination "/tmp/mirum/engineering"}}{{.Name}}{{end}}{{end}}' \
  "$container")
test "$mounted_volume" = "$restore_volume"
url="http://127.0.0.1:8000/engineering/v1/jobs/$job_id/artifacts"
while read -r format expected; do
  actual=$(curl --fail-with-body -sS -H "X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN" \
    "$url/$format" | sha256sum)
  test "${actual%% *}" = "$expected"
done < "$work/fingerprints"
```

## Restart and replacement

Use `stop -t 60` before restarting or replacing a backend. Compose sends
SIGTERM and waits up to 60 seconds before it can resort to SIGKILL. Graceful
shutdown requests cancellation of in-flight checked work and waits for
workers to exit. Work interrupted before terminal commit cannot become an
accepted package. Keep the same Compose project and volume across restarts
and container replacements.

This drill switches back to the original volume and retains the restored
copy. It stops and starts the backend, then replaces the container. It checks
the original volume and the fingerprints after each operation.

```bash release-step=restart-replacement
work=${RELEASE_WORK:-/tmp/mirum-release}
job_id=$(jq -er '.job_id' "$work/result.json")
source_volume=$(cat "$work/source-volume")
compose=(docker compose -f compose.release.yml)
url="http://127.0.0.1:8000/engineering/v1/jobs/$job_id/artifacts"
verify_retained() {
  local container volume format expected actual
  container=$("${compose[@]}" ps --quiet backend)
  volume=$(docker inspect --format \
    '{{range .Mounts}}{{if eq .Destination "/tmp/mirum/engineering"}}{{.Name}}{{end}}{{end}}' \
    "$container")
  test "$volume" = "$source_volume"
  while read -r format expected; do
    actual=$(curl --fail-with-body -sS -H "X-Mirum-Access-Token: $MIRUM_ACCESS_TOKEN" \
      "$url/$format" | sha256sum)
    test "${actual%% *}" = "$expected"
  done < "$work/fingerprints"
}
"${compose[@]}" -f "$work/restore.compose.yml" stop -t 60 backend
"${compose[@]}" up -d --wait --force-recreate backend
before=$("${compose[@]}" ps --quiet backend)
"${compose[@]}" stop -t 60 backend
"${compose[@]}" up -d --wait backend
test "$("${compose[@]}" ps --quiet backend)" = "$before"
verify_retained
"${compose[@]}" stop -t 60 backend
"${compose[@]}" up -d --wait --force-recreate backend
after=$("${compose[@]}" ps --quiet backend)
test -n "$after"
test "$after" != "$before"
verify_retained
```

## Expiry

Download needed bundles within six hours of publication or job completion,
whichever expiry comes first. Polling an expired job reports
`delivery: "expired"`. New download streams return HTTP 410. An existing
download lease can finish, after which cleanup can reclaim the files. Compact
job and idempotency state ages out after about seven days. A pruned job ID
returns HTTP 404. Backups preserve
the original expiry times. Release bundle and job retention have no runtime
override. The legacy `MIRUM_ARTIFACT_*` cleanup settings apply to a separate
artifact store.

## Recovery after a crash

Check the backend logs and start it again with `docker compose -f
compose.release.yml up -d --wait` on the same volume. Startup recovers SQLite,
rotates ownership for tagged workers, reaps surviving tagged workers and
discards staged or uncommitted accepted files. Interrupted jobs become
`unavailable`, or `cancelled` if cancellation was recorded, with outcome
`not_evaluated`. A committed package remains available until expiry.

Poll the original job ID first. To rerun an interrupted job, submit the saved,
still-valid finalized brief with a new idempotency key. If the brief expired,
prepare and review a new one. If the volume is damaged, restore a known-good
backup to a fresh volume and check its retained package fingerprints.

## Troubleshooting

If Compose refuses interpolation, export `MIRUM_ACCESS_TOKEN` in the shell
that runs Compose. For backend startup failures, inspect
`docker compose -f compose.release.yml logs backend` and find
`MIRUM_STARTUP_REFUSED code=…`.

| Code | Cause and action |
| --- | --- |
| `unknown_deployment` | The deployment value is unrecognized. Use the release image's `MIRUM_DEPLOYMENT=release`. |
| `engineering_volume_missing` | The data directory must be an exact mountpoint below `/tmp/mirum`. Restore the named volume mount. |
| `engineering_volume_owner` | Volume metadata is unreadable, the UID/GID differs from the backend's, or group or other write bits are set. Inspect the mount and restore ownership and safe modes with the backend stopped. |
| `engineering_volume_in_use` | Another process holds the owner lock. Stop that backend before starting a replacement on the same volume. |
| `multiple_workers` | A worker or replica setting requests something other than one. Restore the release command and keep `WEB_CONCURRENCY`, `UVICORN_WORKERS` and `MIRUM_API_REPLICAS` at one when set. Check worker flags and `GUNICORN_CMD_ARGS` too. |
| `scratch_not_tmpfs` | The scratch path is backed by a filesystem other than tmpfs. Use the release tmpfs mounts. |
| `scratch_overlap` | Scratch and persistent data are the same path or overlap. Restore the separate work and engineering directories. |
| `access_token_missing` | The token is absent or shorter than 32 characters. Generate and export the token before starting. |
| `access_token_weak` | The token contains whitespace or fewer than ten distinct characters. Generate a new token with OpenSSL. |
| `root_writable` | The container root filesystem is writable or its mount metadata is unavailable. Restore `read_only: true` and run on Linux containers. |
| `running_as_root` | The backend is running as root. Remove the user override and use UID/GID 10001. |

| HTTP status | Meaning and action |
| --- | --- |
| 401 | The access token is missing or wrong. Check the header or browser token. |
| 409 | The review revision or idempotency key conflicts, or artifacts are requested before commit. Fetch the latest record and review again, use the key with its original brief, or wait for the job to finish. |
| 410 | The record or artifacts expired. Use a saved download or prepare and review a new brief. |
| 429 | The active slot, client quota, rate limit or storage cap is full. Honor `Retry-After` when provided and check capacity before a new submission. Avoid automatic retries. |
| 503 | The requested runtime, provider or publication is unavailable, or the publication failed an integrity check. Check backend logs and restore from a verified backup if retained files are damaged. |

Check storage space, volume writeability and permissions after an interrupted
or failed write. Restart once the issue is corrected, then poll the original
job before deciding whether to resubmit.

## Rollback

Before upgrading, take a stopped-volume backup and record the previous image
IDs. Tag the saved images so the local Docker daemon can retain them when
the release tags move.

```bash release-manual=save-rollback-images
docker image inspect --format '{{.Id}}' mirum-backend:release mirum-frontend:release
docker tag mirum-backend:release mirum-backend:previous
docker tag mirum-frontend:release mirum-frontend:previous
```

Use the saved tags below, or the previous image IDs as the tag sources.
Keep the current volume if it is compatible with the previous images. If
compatibility is uncertain, restore the matching pre-upgrade backup to a
fresh volume and start with its override. Verify health and retained hashes.
Preserve all volumes throughout rollback. Never use `down -v`.

```bash release-manual=rollback
docker compose -f compose.release.yml stop -t 60
docker tag mirum-backend:previous mirum-backend:release
docker tag mirum-frontend:previous mirum-frontend:release
docker compose -f compose.release.yml up -d --wait --force-recreate
```

## Uninstall

Remove the stack while keeping the data volume. If you use a volume override,
include it with the base file.

```bash release-manual=uninstall
docker compose -f compose.release.yml down --timeout 60
```

**Data loss:** Removing an engineering volume permanently deletes its stored
drafts, finalized briefs, job history and packages. Verify the backup and the
exact Docker volume name before removing a volume you intend to discard.

```bash release-manual=remove-volume
read -r -p 'Exact engineering volume name to delete: ' volume
docker volume rm "$volume"
```
