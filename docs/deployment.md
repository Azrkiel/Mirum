# Deploying Mirum to Oracle Cloud

How the hosted instance is configured, and how a merge to `main` reaches it.

## Topology

```
        internet
            │
            ▼  :80 / :443
   ┌─────────────────────┐
   │  caddy              │  TLS + HTTP basic auth
   └─────────┬───────────┘
             │  internal Docker network
             ▼
   ┌─────────────────────┐
   │  frontend (8501)    │  Streamlit — no host port on 0.0.0.0
   └─────────┬───────────┘
             │  http://backend:8000  + X-API-Key
             ▼
   ┌─────────────────────┐
   │  backend  (8000)    │  FastAPI — loopback only
   │   └── sandbox       │  executes LLM-generated CadQuery
   └─────────────────────┘
```

Only Caddy is exposed publicly. `backend` and `frontend` publish on
`127.0.0.1` so you can still curl them from a shell on the box, but they are
unreachable from the internet.

Caddy sits behind the `production` Compose profile, so local development
(`docker compose up`) runs just the two app services with no basic-auth
setup required. Production uses `docker compose --profile production up -d`.
Omitting the profile on the server fails safe — the site is simply down
rather than exposed without a password.

> Publishing a container port as `8000:8000` binds `0.0.0.0` **and bypasses
> ufw** — Docker installs its own iptables rules ahead of ufw's chains. A
> firewall rule is not sufficient to keep a published port private; the
> `127.0.0.1:` prefix in `docker-compose.yml` is what actually does it.

## Security model

Mirum executes LLM-authored Python by design. Three layers keep that from
being an open remote-code-execution service:

| Layer | Mechanism | Where |
|---|---|---|
| Public edge | HTTP basic auth, TLS | `Caddyfile` |
| Service boundary | `X-API-Key` shared secret; backend not internet-routable | `backend/main.py`, `docker-compose.yml` |
| Execution | AST allowlist, banned builtins, 1.5 GB `RLIMIT_AS`, ≤60 s timeout, no API key in subprocess env | `backend/compiler.py` |

The sandbox is the last line, not the only one. `/run`, which takes an
arbitrary script directly, is disabled unless `MIRUM_ENABLE_RUN_ENDPOINT=1`.

## One-time server setup

SSH to the instance, then:

```bash
# 1. Replace the scp'd directory with a real git checkout.
#    Keep the old one until the new deploy is verified.
mv ~/T2CIII ~/T2CIII.bak
git clone https://github.com/Azrkiel/Mirum.git ~/T2CIII
cd ~/T2CIII

# 2. Restore the environment file (it is gitignored, so it is not in the repo).
cp ~/T2CIII.bak/.env .env        # or: cp .env.example .env && edit

# 3. Fill in the new required values.
#    - MIRUM_API_KEY            openssl rand -hex 32
#    - MIRUM_BASIC_AUTH_HASH    docker run --rm caddy:2-alpine \
#                                 caddy hash-password --plaintext 'your-password'
#    - MIRUM_SITE_ADDRESS       ":80", or your domain for automatic HTTPS
nano .env

# 4. Bring it up. --profile production adds the Caddy edge proxy; without
#    it nothing is published publicly (both app services bind loopback).
docker compose --profile production up -d --build

# 5. Verify.
curl -fsS http://127.0.0.1:8000/health            # {"status":"ok"}
curl -si  http://127.0.0.1:8501 | head -1          # Streamlit responds locally
curl -si  http://127.0.0.1/     | head -1          # 401 from Caddy — auth is live
```

A private repo needs a deploy key instead of the HTTPS clone above:
`ssh-keygen -t ed25519 -f ~/.ssh/mirum_deploy -N ""`, add the `.pub` under
repo Settings → Deploy keys (read-only), then clone via
`git@github.com:Azrkiel/Mirum.git`.

### OCI network rules

Ingress must be open for the public ports in **two** places — the VCN
security list *and* the instance firewall. Oracle's Ubuntu images ship with
restrictive iptables, which is the usual reason a correctly-configured
security list still times out:

```bash
sudo iptables -I INPUT -p tcp --dport 80  -j ACCEPT
sudo iptables -I INPUT -p tcp --dport 443 -j ACCEPT
sudo netfilter-persistent save
```

Ports 8000 and 8501 should **not** be opened in the security list.

## GitHub Actions deployment

`.github/workflows/deploy.yml` runs on every push to `main` (and on demand
via *Actions → Deploy to Oracle Cloud → Run workflow*, which offers a
force-rebuild checkbox). It opens an SSH connection and pipes
`scripts/deploy-remote.sh` to the server, which:

1. fetches and `git reset --hard origin/main` (idempotent; leaves `.env` alone),
2. rebuilds the image **only** if a `Dockerfile`, `requirements.txt`, or
   `docker-compose.yml` changed — a conda + OpenCASCADE rebuild is slow, and
   application code is bind-mounted so ordinary edits just need a recreate,
3. `chmod -R a+rX` (the container's `caduser` must be able to traverse `/app`),
4. recreates containers and polls `/health` for up to 5 minutes,
5. prunes dangling images after a rebuild.

The build happens **on the box**, which sidesteps CPU-architecture mismatch
between the Actions runner and the instance (relevant if you are on an
Ampere A1 ARM shape).

### Required repository secrets

Settings → Secrets and variables → Actions:

| Secret | Required | Value |
|---|---|---|
| `OCI_HOST` | yes | Instance public IP |
| `OCI_SSH_KEY` | yes | Full contents of the private key, including the BEGIN/END lines |
| `OCI_KNOWN_HOSTS` | recommended | Output of `ssh-keyscan <ip>`. Without it the workflow falls back to trust-on-first-use and warns. |
| `OCI_USER` | no | Defaults to `ubuntu` |
| `OCI_DEPLOY_DIR` | no | Defaults to `~/T2CIII` |

The workflow targets a `production` environment — create it under Settings →
Environments to require a manual approval before any deploy runs.

### Rollback

```bash
cd ~/T2CIII
git log --oneline -10
git reset --hard <good-sha>
docker compose --profile production up -d --force-recreate
```

Re-running the workflow returns the box to `origin/main`, so also revert the
bad commit on GitHub or the next push will redeploy it.

## Operations

```bash
docker compose logs -f backend      # tail
docker compose ps                   # status + health
docker compose restart backend      # restart one service
curl -fsS http://127.0.0.1:8000/health
```

`deploy.ps1` still works for pushing uncommitted local changes during
debugging. Note that it scp's files over the git checkout, so the box will
show dirty state until the next Actions deploy resets it — fine for
iterating, but commit anything you want to keep.

### Things worth knowing

- **Telemetry is on tmpfs by default.** The few-shot retrieval index is
  rebuilt from `critic_loop.jsonl` at startup, so with the default path the
  learned corpus is destroyed on every restart. Set `MIRUM_TELEMETRY_DIR` to
  a mounted volume to keep it.
- **Disk fills quickly.** Each conda/OpenCASCADE image rebuild leaves
  multi-GB layers behind. The deploy script prunes dangling images after a
  build; `docker system df` shows the damage if things get tight.
- **Certificates live in the `caddy_data` volume.** Don't `docker compose
  down -v` on a domain deployment unless you want to re-provision them and
  risk Let's Encrypt rate limits.
