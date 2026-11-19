# Exact revision packages and retention

`BundlePublisher(store)` is B09's real publisher. The accepted path reassesses
every retained attempt using B08, verifies the selected brief/content/checks,
and writes all eight CONTRACT payloads, manifest.json and package.zip using the
captured source/STEP/GLB. No CAD builder or solver is called by publication.
The preview's measured-STEP association is rechecked by the existing policy.

`BundleManifest`/`ManifestPayload` provide the strict JSON schema. Each entry
contains a closed payload path, MIME type, byte count and full SHA-256. Manifest
and ZIP identities live only in the SQLite publication envelope and accepted
EngineeringRun.BundleIdentity. Reports carry job, brief and candidate identities,
native reports and their original report hashes, without circular archive hashes.
Human-readable reports preserve digital-model, material and certification limits.

Publication writes exclusive files into staging, fsyncs every file and directory,
renames within the volume, fsyncs the destination parent, records the envelope,
and validates the complete archive and unpacked payloads before the job's accepted
transaction. Cancellation before terminal commit wins and discards the package.
Rejected jobs publish a diagnostic ZIP containing brief/attempts/reports/manifest,
with no accepted source, STEP, preview or selected-spec download.

## Admission and downloads

Admission reserves 32 MiB, rejects a 51st retained artifact-bearing run, and bounds
reserved or actual volume use by 512 MiB. The publisher also checks free space
and the complete stored run (unpacked files, ZIP and a conservative 1 MiB metadata
allowance). Retained metadata must fit that allowance. Capacity never evicts an
unexpired result. Native work and scratch limits are unchanged.

`lease(job_id, format)` validates the whole publication and returns an owned file,
filename, MIME, size and digest. Formats are zip/brief/spec/source/step/glb/
attempts/report/markdown/manifest. The transport must hold the context until its
stream closes, including error/disconnect paths. A filename or filesystem path is
never accepted from a caller. Native accepted formats require a committed job.
The explicit envelope argument is only an internal publisher-test seam.

Expiry rejects new leases at six hours. Existing streams keep their files until
the last lease closes. Expiry removes packages and private brief/draft input,
compacts job state to `engineering_expired_run_v1`, and preserves known-ID/expiry
information and idempotency for seven days. Expired records contain identities,
timestamps and historical outcome, not physical input/attempt payloads. Reads of
known expired briefs/drafts raise ArtifactExpired (HTTP 410); unknown IDs are 404.
Unbound preparation input also has a six-hour lifetime. Call budgets are retained
until the seven-day record lifetime; no restart resets a claim.

Cleanup runs on job read/admission and startup; no request is required to make a
new lease reject an expired publication. The API consumer must also clean before
reading preparation records. SQLite secure_delete is enabled. This is application
retention, not a forensic erasure guarantee for storage snapshots/backups.

Startup discards staging and orphan/uncommitted accepted bundles. It never
recreates missing files. Missing/tampered committed files fail download validation;
the historical terminal outcome is immutable, but delivery is unavailable. The
API must surface this failure rather than return accepted bytes.

Engineering data is outside the legacy artifact runs root. Overlapping roots are
rejected; ordinary legacy TTL/cap sweeps cannot reach engineering bundles.
The database changes are additive under user_version=1. ExpiredRun is a new
consumer read shape; use its expired flag before accessing attempts or bundle.
Rollback requires the matching source and a quiescent volume snapshot; older B09
code cannot parse a compact B10 job record. No deployment or integration release
is implied by this library qualification.
