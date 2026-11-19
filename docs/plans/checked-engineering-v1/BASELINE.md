# B01 native baseline recipe

Run from the checked-out `engineering/checked-design-v1` branch. No `.env`,
provider key or paid call is needed. Docker must use Linux containers.
Keep evidence outside the checkout; do not commit generated CAD or full logs.

## Build and test (PowerShell)

```powershell
$repo = (Get-Location).Path
$evidence = 'C:/Users/jiang/mirum-evidence/B01' # choose a local evidence directory
New-Item -ItemType Directory -Force $evidence | Out-Null
docker build --progress=plain -f Dockerfile.verify -t mirum-checked-b01:baseline .
docker image inspect mirum-checked-b01:baseline --format '{{.Id}}'
docker run --rm --network none --mount "type=bind,source=$repo,target=/workspace,readonly" mirum-checked-b01:baseline python -m pytest -q -p no:cacheprovider --tb=short backend/tests/test_engineering_spec.py backend/tests/test_engineering_fea.py backend/tests/test_engineering_native.py backend/tests/test_engineering_service.py backend/tests/test_engineering_api.py
docker run --rm --network none --mount "type=bind,source=$repo,target=/workspace,readonly" --mount "type=bind,source=$evidence,target=/evidence" mirum-checked-b01:baseline python scripts/checked_engineering_baseline.py /evidence (git rev-parse HEAD)
```

The probe starts the real application on container loopback port 8765 and uses
HTTP, rather than FastAPI TestClient. It checks invalid dimensions (422), the
50 N reference search (reject 5 and 10 mm; accept 15 mm), and all-candidate
physics rejection without STEP/GLB. It reimports the delivered STEP, reads and
renders the actual GLB, runs `pip check`, and writes a runtime/artifact manifest.
`--network none` permits container loopback but prevents a provider request.
Existing API tests use deterministic provider fixtures; native workers and the
loopback probe are real. Inspect `preview.png` as well as the measurements.

## Local HTTPS scanning prerequisite

On the B01 Windows host, Avast intercepts PyPI TLS. An unmodified build failed
with `CERTIFICATE_VERIFY_FAILED`. Supply the **installed, trusted host root**
through a local Dockerfile and BuildKit secret. Do not disable TLS verification
or commit the host certificate. This changes build trust, not package pins or
application source. Other hosts should use the unmodified recipe above.

```powershell
$cert = Get-ChildItem Cert:/LocalMachine/Root | Where-Object Subject -Match 'Avast' | Select-Object -First 1
if (-not $cert) { throw 'Installed HTTPS scanning root not found' }
$pem = "-----BEGIN CERTIFICATE-----`n" + [Convert]::ToBase64String($cert.RawData, [Base64FormattingOptions]::InsertLineBreaks) + "`n-----END CERTIFICATE-----`n"
[IO.File]::WriteAllText("$evidence/local-ca.pem", $pem)
$recipe = [IO.File]::ReadAllText("$repo/Dockerfile.verify")
$recipe = $recipe.Replace('RUN npm ci', 'RUN --mount=type=secret,id=verification_ca NODE_EXTRA_CA_CERTS=/run/secrets/verification_ca npm ci')
$recipe = $recipe.Replace('COPY requirements-linux.lock /tmp/requirements-linux.lock', "RUN --mount=type=secret,id=verification_ca cp /run/secrets/verification_ca /usr/local/share/ca-certificates/local-verification.crt && update-ca-certificates`nENV PIP_CERT=/etc/ssl/certs/ca-certificates.crt`nCOPY requirements-linux.lock /tmp/requirements-linux.lock")
[IO.File]::WriteAllText("$evidence/Dockerfile.verify.local-ca", $recipe)
docker build --progress=plain --secret "id=verification_ca,src=$evidence/local-ca.pem" -f "$evidence/Dockerfile.verify.local-ca" -t mirum-checked-b01:baseline .
```

## Budgets and interpretation

Native acceptance and engineering verification each run in bounded subprocesses
with a 30 second deadline, 1,536 MiB address-space limit, and 1 MiB captured-output
limit. The initial legacy engineering route admits one active request and allows
two HTTP requests per minute; its semaphore can wait and is not the future V1
no-queue admission policy. Its candidate order remains caller supplied.

The new V1 policy plans at most four candidates, a 420 second overall deadline,
and a 32 MiB stored-run cap. B01 measures a small reference workload; it does not
prove worst-case feasibility across the whole permitted geometry envelope, or
implement V1 storage, admission, cleanup, cancellation or packaging. B02 records
these budgets as versioned policy; B09/B10/B13 must enforce and qualify them.

STEP/GLB SHA-256 values identify delivered bytes. Independent exports may include
timestamps. Replay compares verdicts and physical measurements within numerical
tolerances, not raw file equality. B01 establishes digital beam-model behavior,
with illustrative material inputs; physical testing, manufacturing release and
standards conformity remain unevaluated.
