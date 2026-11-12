# Fly.io deployment

Two apps in `iad`:

| App | Config | Exposure |
|---|---|---|
| `mirum-api` | `backend.fly.toml` + `backend.Dockerfile` | Private only (Flycast `http://mirum-api.flycast`), no public IP |
| `mirum` | `frontend.fly.toml` + `frontend.Dockerfile` | Public Streamlit UI at `https://mirum.fly.dev` |

The public deployment is open: `MIRUM_ACCESS_TOKEN` is unset on `mirum-api`
and the UI runs with `MIRUM_ACCESS_GATE=0`, so visitors need no token. Per-IP
rate limits (slowapi) still apply. To put the gate back, set the
`MIRUM_ACCESS_TOKEN` secret and `MIRUM_ACCESS_GATE = "1"` in
`frontend.fly.toml`; the backend then rejects requests without a matching
`X-Mirum-Access-Token`, and the UI asks the visitor for it.
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

Later deploys need only the two `fly deploy` lines. (`MIRUM_ACCESS_TOKEN` is
optional; leave it out for open access.)

## Limits

Generated CadQuery runs in a resource-bounded subprocess (AST allowlist, audit
hook, scrubbed env, memory and time caps), but it is not a hostile-code sandbox
(see `docs/implementation/phase1-api-ui-review.md`). With the gate off, any
visitor can run generations and spend the Gemini quota, bounded only by the
per-IP rate limits.
