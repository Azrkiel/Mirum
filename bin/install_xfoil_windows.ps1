# install_xfoil_windows.ps1
# ──────────────────────────────────────────────────────────────────────────────
# Downloads XFOIL 6.99 for Windows from the official MIT source and installs
# xfoil.exe into T2CIII/bin/ so the pipeline can find it automatically.
#
# Run from a PowerShell prompt (no admin required):
#   cd C:\Users\Kevin\Developments\T2CIII
#   .\bin\install_xfoil_windows.ps1
#
# After running, set the env var (or update backend/config.py XFOIL_BIN):
#   $env:MIRUM_XFOIL_BIN = "C:\Users\Kevin\Developments\T2CIII\bin\xfoil.exe"
# ──────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"

$BinDir   = Split-Path -Parent $MyInvocation.MyCommand.Path
$ZipPath  = Join-Path $BinDir "XFOIL6.99.zip"
$ExePath  = Join-Path $BinDir "xfoil.exe"

Write-Host "XFOIL Windows Installer" -ForegroundColor Cyan
Write-Host "Target directory: $BinDir"

# ── Step 1: Download ──────────────────────────────────────────────────────────
$Url = "https://web.mit.edu/drela/Public/web/xfoil/XFOIL6.99.zip"
Write-Host "`nDownloading XFOIL 6.99 from MIT..." -ForegroundColor Yellow
Write-Host "  URL: $Url"

try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $Url -OutFile $ZipPath -UseBasicParsing
    $SizeMB = [math]::Round((Get-Item $ZipPath).Length / 1MB, 2)
    Write-Host "  Downloaded: $SizeMB MB" -ForegroundColor Green
} catch {
    Write-Error "Download failed: $_"
    Write-Host @"
`nManual fallback:
  1. Open https://web.mit.edu/drela/Public/web/xfoil/ in a browser
  2. Download XFOIL6.99.zip
  3. Extract xfoil.exe into: $BinDir
"@
    exit 1
}

# ── Step 2: Extract xfoil.exe ─────────────────────────────────────────────────
Write-Host "`nExtracting xfoil.exe..." -ForegroundColor Yellow
Add-Type -AssemblyName System.IO.Compression.FileSystem
$Zip = [System.IO.Compression.ZipFile]::OpenRead($ZipPath)
$Entry = $Zip.Entries | Where-Object { $_.Name -ieq "xfoil.exe" } | Select-Object -First 1

if (-not $Entry) {
    # Some zip layouts put the exe at the top level, some in a subfolder
    $Entry = $Zip.Entries | Where-Object { $_.FullName -match "xfoil\.exe$" } | Select-Object -First 1
}

if (-not $Entry) {
    $Zip.Dispose()
    Write-Error "xfoil.exe not found in the ZIP. Zip contents:`n$($Zip.Entries | Select-Object -ExpandProperty FullName)"
    exit 1
}

[System.IO.Compression.ZipFileExtensions]::ExtractToFile($Entry, $ExePath, $true)
$Zip.Dispose()

$SizeKB = [math]::Round((Get-Item $ExePath).Length / 1KB, 1)
Write-Host "  Extracted: xfoil.exe ($SizeKB KB)" -ForegroundColor Green

# ── Step 3: Clean up zip ──────────────────────────────────────────────────────
Remove-Item $ZipPath -Force
Write-Host "  Removed ZIP."

# ── Step 4: Quick smoke test ──────────────────────────────────────────────────
Write-Host "`nRunning smoke test..." -ForegroundColor Yellow
try {
    $TestOut = echo "QUIT" | & $ExePath 2>&1
    if ($TestOut -match "XFOIL" -or $LASTEXITCODE -eq 0) {
        Write-Host "  xfoil.exe responds correctly." -ForegroundColor Green
    } else {
        Write-Host "  Warning: unexpected output (may still work)." -ForegroundColor Yellow
        Write-Host "  Output: $($TestOut | Select-Object -First 3)"
    }
} catch {
    Write-Host "  Warning: smoke test inconclusive: $_" -ForegroundColor Yellow
}

# ── Step 5: Print env var instructions ───────────────────────────────────────
Write-Host @"

────────────────────────────────────────────────────────────
  XFOIL installed successfully.

  Path: $ExePath

  To make the pipeline use it, set the env var:
    `$env:MIRUM_XFOIL_BIN = "$ExePath"

  Or add it permanently (user scope):
    [System.Environment]::SetEnvironmentVariable(
        'MIRUM_XFOIL_BIN', '$ExePath', 'User')

  Or edit backend/config.py line:
    XFOIL_BIN: str = _os.environ.get("MIRUM_XFOIL_BIN", "xfoil")
  to:
    XFOIL_BIN: str = r"$ExePath"
────────────────────────────────────────────────────────────
"@ -ForegroundColor Cyan
