# Fly.io deployment

Two apps in `iad` run the exact RC1 release images, built and pushed by CI:

| App | Config | Image | Exposure |
|---|---|---|---|
| `mirum-api` | `backend.fly.toml` | `ghcr.io/azrkiel/mirum-backend-fly` (`backend/Dockerfile` target `fly`) | Private only (Flycast `http://mirum-api.flycast`), no public IP |
| `mirum` | `frontend.fly.toml` | `ghcr.io/azrkiel/mirum-frontend` (`frontend/Dockerfile`, unchanged) | Public Streamlit UI at `https://mirum.fly.dev` |

Neither config has a `[build]` section: Fly never builds from source. The image is
always given at deploy time, by digest. Both apps stop when idle and start on the next
request, so the first request after idle waits for a cold start.

## Publish the images

Run the `Release publish` workflow (`.github/workflows/release-publish.yml`) from `main`
(Actions, Run workflow). It refuses any other ref. It:

1. builds the release images through the same action as `release-images.yml` (Python
   version, `pip check`, STEP smoke, regen smoke, frontend smoke);
2. builds the Fly backend (`backend-fly` bake target: `FROM release` plus a root-owned init script);
3. smoke-tests the Fly image in a privileged container (volume at `/data`, open-access
   opt-in, no token) and checks the negative cases (no volume: init refuses; no opt-in
   and no token: `access_token_missing`);
4. pushes `mirum-backend-fly:<sha>` and `mirum-frontend:<sha>` to GHCR with `GITHUB_TOKEN`
   only (no other secrets) and writes the pushed digests to the job summary and to the
   `release-publish` artifact (`release-publish.json`).

The first push creates the GHCR packages as private. Make both public (package settings)
or give Fly registry credentials before the first deploy.

## Volume (once)

Engineering storage lives on a 1 GB Fly volume mounted at `/data`:

```sh
fly volumes create mirum_engineering --size 1 --region iad -a mirum-api
```

The init script bind-mounts `/data/engineering` onto `/tmp/mirum/engineering` (the path the
release guards require) and sets its owner to uid/gid 10001, mode 0700. The backend supports
exactly one API process, so keep one machine: `fly scale count 1 -a mirum-api`.

## Deploy by digest

Take the digests from the workflow summary:

```sh
fly deploy -c deploy/fly/backend.fly.toml \
  --image ghcr.io/azrkiel/mirum-backend-fly@sha256:<backend-digest> --no-public-ips
fly deploy -c deploy/fly/frontend.fly.toml \
  --image ghcr.io/azrkiel/mirum-frontend@sha256:<frontend-digest>
```

First time only: create the apps (`fly apps create mirum-api`, `fly apps create mirum`),
allocate the private address (`fly ips allocate-v6 --private -a mirum-api`) and stage the
secrets (`fly secrets set -a mirum-api --stage GEMINI_API_KEY_HEAVY=... ADMIN_API_KEY=...`).
Do not set `MIRUM_ACCESS_TOKEN` (open access) and never set `MIRUM_ENGINEERING_AI_DRAFTS`.

## Rollback

Every deploy is an image digest. To roll back, redeploy the previous digest (keep the last
good pair from the earlier workflow summary), or list releases with
`fly releases -a mirum-api --image` and pass that image to `fly deploy --image`. The volume
is untouched by a rollback.

## Staging first

Rehearse on throwaway apps before touching production:

```sh
fly apps create mirum-api-staging
fly apps create mirum-staging
fly volumes create mirum_engineering --size 1 --region iad -a mirum-api-staging
fly ips allocate-v6 --private -a mirum-api-staging
fly secrets set -a mirum-api-staging --stage GEMINI_API_KEY_HEAVY=...
fly deploy -a mirum-api-staging -c deploy/fly/backend.fly.toml --image <backend@digest> --no-public-ips
fly deploy -a mirum-staging -c deploy/fly/frontend.fly.toml --image <frontend@digest> \
  --env BACKEND_URL=http://mirum-api-staging.flycast
fly logs -a mirum-api-staging   # expect MIRUM_STARTUP_OPEN_ACCESS and no MIRUM_STARTUP_REFUSED
```

`-a` overrides the app name in the toml. Destroy the staging apps and volume afterwards.

## Release guards on Fly

The release image sets `MIRUM_DEPLOYMENT=release`, so the startup guards in
`backend/startup_checks.py` run. `compose.release.yml` satisfies them with docker options;
Fly Machines have none of those, so the `fly` stage entrypoint (`release-init.sh`,
installed as `/usr/local/bin/mirum-fly-init`) builds the same layout while still root and
fails closed on any error:

1. requires root and that `/data` is a mountpoint (the volume);
2. mounts tmpfs on `/tmp` (256 MB) and on `/tmp/mirum` (256 MB, owner 10001, mode 0700);
3. creates `/data/engineering` if missing (10001:10001, 0700, non-recursive, never a symlink);
4. bind-mounts it on `/tmp/mirum/engineering`;
5. remounts `/` read-only;
6. execs the server as 10001:10001 through `setpriv` with no supplementary groups, no
   capabilities and `no_new_privs`.

## Open access (trade-off)

The public deployment is open: no `MIRUM_ACCESS_TOKEN` on `mirum-api` and
`MIRUM_ACCESS_GATE=0` on the UI, so anyone can use the site and spend the Gemini quota,
bounded only by the per-IP rate limits.

The release token guard (`access_token_missing`) is relaxed only by the explicit opt-in
`MIRUM_OPEN_ACCESS=1` in `backend.fly.toml`, and only while the token is empty or unset. With
the opt-in the backend logs one `MIRUM_STARTUP_OPEN_ACCESS: ...` line at boot. A set but weak
token still fails (`access_token_weak`); any other opt-in value (`0`, `true`, `yes`) or no
opt-in behaves as before; every other guard is unchanged. To close the site again, remove
`MIRUM_OPEN_ACCESS`, set a strong `MIRUM_ACCESS_TOKEN` secret and `MIRUM_ACCESS_GATE = "1"`
in `frontend.fly.toml`.

## Per-visitor rate limits (trust boundary)

The Streamlit server makes every backend call, so the private backend sees one
peer address for all visitors. Without help, every "per-IP" slowapi limit is one
global bucket and a single visitor can exhaust it for everyone.

The frontend reads the visitor's address from the request headers (`Fly-Client-IP`,
else the first `X-Forwarded-For` hop) and sends it as `X-Mirum-Client-IP`. The
backend uses it as the limiter key only when `MIRUM_TRUSTED_CLIENT_HEADER=1`
(set in `backend.fly.toml`). The default is off, and then the header is ignored.

Turn it on ONLY when the backend is unreachable from the public internet, as in
this setup (private backend, public frontend). Anyone who can call the backend
directly could otherwise forge the header to dodge or poison the limits. If
`mirum-api` ever gets a public IP, or you run the backend publicly elsewhere,
leave the setting off. `docker-compose.yml` publishes port 8000, so it stays off
there.

## Limits and residuals

Generated CadQuery runs in a resource-bounded subprocess (AST allowlist, audit
hook, scrubbed env, memory and time caps), but it is not a hostile-code sandbox
(see `docs/implementation/phase1-api-ui-review.md`).

Residual: the frontend image on Fly is not read-only. Fly cannot mount the root
read-only without an init wrapper, and the frontend has no release guards that
require it. It runs as uid 10001 with a root-owned `/app`, but its root filesystem is
writable. The backend has a read-only root, enforced by the init script and the guard.

The Fly-specific boot path (read-only root remount, tmpfs, volume bind mount on a real
Machine) is proven in CI only in a privileged Docker container; the first Fly boot is the
real check (see Staging first).
