# deploy.ps1 — Sync local code to Oracle Cloud and restart the backend
# Usage (from the T2CIII project root):
#   .\deploy.ps1              # sync + restart + tail logs
#   .\deploy.ps1 -LogsOnly   # tail logs without syncing
#   .\deploy.ps1 -NoLogs     # sync + restart, return to prompt immediately

param(
    [switch]$LogsOnly,
    [switch]$NoLogs,
    [switch]$Build    # Pass -Build when Dockerfile or requirements.txt changed
)

$KEY    = "C:\Users\Kevin\Downloads\ssh-key-2026-04-21.key"
$REMOTE = "ubuntu@150.136.210.12"
$RDIR   = "~/T2CIII"

# ── Helpers ──────────────────────────────────────────────────────────────────

function Run-SCP {
    param([string]$Local, [string]$RemotePath)
    Write-Host "  scp $Local -> $REMOTE`:$RemotePath" -ForegroundColor Cyan
    scp -i $KEY -r $Local "${REMOTE}:${RemotePath}"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: scp failed (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

function Run-SSH {
    param([string]$Cmd)
    Write-Host "  ssh: $Cmd" -ForegroundColor Cyan
    ssh -i $KEY $REMOTE $Cmd
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: ssh command failed (exit $LASTEXITCODE)" -ForegroundColor Red
        exit $LASTEXITCODE
    }
}

# ── Main ─────────────────────────────────────────────────────────────────────

if (-not $LogsOnly) {
    Write-Host ""
    Write-Host "==> Syncing backend..." -ForegroundColor Yellow
    Run-SCP "C:\Users\Kevin\Developments\T2CIII\backend" "$RDIR/"

    Write-Host ""
    Write-Host "==> Syncing frontend..." -ForegroundColor Yellow
    Run-SCP "C:\Users\Kevin\Developments\T2CIII\frontend" "$RDIR/"

    Write-Host ""
    # Bind-mounts expose host UIDs into the container. caduser inside the
    # backend image is a system user (UID ~999) distinct from host ubuntu
    # (UID 1001). scp defaults ~/T2CIII/* to mode 0700, making /app
    # untraversable to caduser and causing uvicorn "Could not import
    # module 'main'" at startup. a+rX adds read+traverse for everyone
    # without granting write.
    Write-Host "==> Normalizing remote permissions..." -ForegroundColor Yellow
    Run-SSH "chmod -R a+rX $RDIR"

    Write-Host ""
    if ($Build) {
        Write-Host "==> Rebuilding and restarting backend container (--build)..." -ForegroundColor Yellow
        Run-SSH "cd $RDIR && docker compose up -d --build backend"
    } else {
        Write-Host "==> Restarting backend container..." -ForegroundColor Yellow
        Run-SSH "cd $RDIR && docker compose restart backend"
    }

    Write-Host ""
    Write-Host "==> Backend restarted. Uvicorn --reload will also pick up the new files." -ForegroundColor Green
}

if (-not $NoLogs) {
    Write-Host ""
    Write-Host "==> Tailing backend logs (Ctrl+C to stop)..." -ForegroundColor Yellow
    Write-Host ""
    # -f keeps following; runs interactively in the foreground
    ssh -i $KEY $REMOTE "cd $RDIR && docker compose logs -f backend"
}
