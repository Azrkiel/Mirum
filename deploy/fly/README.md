# Fly.io deployment

Two apps in `iad`:

| App | Config | Exposure |
|---|---|---|
| `mirum-api` | `backend.fly.toml` + `backend.Dockerfile` | Private only (Flycast `http://mirum-api.flycast`), no public IP |
| `mirum` | `frontend.fly.toml` + `frontend.Dockerfile` | Public Streamlit UI at `https://mirum.fly.dev` |

The backend rejects every request without `X-Mirum-Access-Token` matching the
`MIRUM_ACCESS_TOKEN` secret. The UI (`MIRUM_ACCESS_GATE=1`) asks the visitor
for that token and forwards it; the token is not stored in the frontend app.
Both apps stop when idle and start on the next request, so the first request
after idle waits for a cold start.

The backend image uses the same pinned CadQuery/OCP environment as
`Dockerfile.verify`, the offline native test gate.

## First deploy (from the repo root)

```sh
fly apps create mirum-api
fly apps create mirum
fly ips allocate-v6 --private -a mirum-api
fly secrets set -a mirum-api --stage \
  GEMINI_API_KEY_HEAVY=... MIRUM_ACCESS_TOKEN=... ADMIN_API_KEY=...
fly deploy . -c deploy/fly/backend.fly.toml --dockerfile deploy/fly/backend.Dockerfile --no-public-ips
fly deploy . -c deploy/fly/frontend.fly.toml --dockerfile deploy/fly/frontend.Dockerfile
```

Later deploys need only the two `fly deploy` lines. Rotate the access token
with `fly secrets set -a mirum-api MIRUM_ACCESS_TOKEN=...`.

## Limits

This is a trusted-user deployment. Generated CadQuery runs in a resource-bounded
subprocess, but it is not a hostile-code sandbox (see
`docs/implementation/phase1-api-ui-review.md`). Share the access token only with
people you trust to run code on the machine and spend the Gemini quota.
