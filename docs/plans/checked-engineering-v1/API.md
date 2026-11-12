# Checked engineering HTTP API

The `/engineering/v1` routes implement CONTRACT.md. Existing general CAD,
engineering/generate, glider and CEM routes retain their own contracts. The global
`MIRUM_ACCESS_TOKEN` gate applies to all checked routes. This remains trusted
local/controlled use; IDs are not a multi-tenant authorization boundary.

1. GET capabilities reports exact descriptors, runtime prerequisites and separate
   AI availability. Each job still requires actual native evidence.
2. POST drafts with `{"mode":"form","input":<BriefContent JSON>}`. The provider-free
   service returns draft, input and field-specific issues. The shared reference
   input is fixtures/engineering/brief-draft.json's content object.
   Responses also expose semantic brief/fixed-requirement SHA-256 values for
   complete content; capabilities supplies the exact review_fields list.
3. Alternatively POST drafts with `{"mode":"ai","input":{},"prompt":"..."}`.
   The existing bounded proposer creates an unreviewed revision. A 503 response
   includes the prepared draft ID and visible reasons for typed fallback.
4. GET drafts/{id}; PATCH with expected_revision and replacement input. CAS
   conflicts require reading and reviewing the latest revision.
5. POST briefs with the shared ReviewAcknowledgement JSON: exact draft ID,
   revision, semantic brief SHA-256, all REVIEW_FIELDS, reviewer and aware timestamp.
   JSON arrays/dates enter the shared strict JSON boundary; physical-number and
   Python-constructor validation is not weakened. GET briefs/{id} reads immutable
   reviewed content. No job starts at draft creation/edit/finalization.
6. POST jobs with only brief_id and a 1-128-character idempotency_key. It returns
   202 and a job identity. Same key/brief ID reuses the job; another brief conflicts.
   Poll GET jobs/{id}; POST jobs/{id}/cancel records cancellation before commit.
7. Job responses add delivery (pending/available/unavailable/expired), actual
   allowlisted artifact availability and artifact_expires_at. An expired job uses
   engineering_expired_run_v1 and has no input/attempt payload or downloadable CAD.
8. GET jobs/{id}/artifacts/{format} streams a complete validated publication under
   a lease, releasing it on completion/error/disconnect. MIME, attachment name,
   Content-Length and X-Content-SHA256 identify the actual returned bytes.

Formats: zip, brief, spec, source, step, glb, attempts, report, markdown, manifest.
Rejected diagnostics expose only brief/attempts/reports/manifest/ZIP. No client
filesystem path is accepted. Polling, cancellation and download never generate CAD.

Errors: 422 invalid/unsupported input, 409 review/state/idempotency conflict,
429 admission/rate limit (admission includes Retry-After), 410 known expired,
404 unknown ID/format, 503 unavailable preparation/runtime/publication. A started
job records subsequent execution failure in its terminal lifecycle. Historical
acceptance with missing/tampered files has unavailable delivery, never accepted
download bytes. Checked responses use Cache-Control: no-store.

Default rates per remote address: draft creation/job submission 10/minute,
draft/brief/capability edits and reads 30/minute, job polling 120/minute,
cancel 30/minute, downloads 60/minute. One active job/no queue and shared legacy
native admission still apply. AI calls remain one initial plus one schema repair
per claimed draft revision, with existing deadlines/size bounds.

Lifespan constructs one store/publisher/controller/service/shared ledger and closes
owned work before storage. Production Compose sets REQUIRE_VOLUME=1 and mounts
the application-owned data volume under /tmp/mirum, separate from scratch. A
missing mount, unsupported worker/replica setting or second owner rejects startup.
An explicit development data path without REQUIRE_VOLUME is permitted for tests
and local probes; it does not prove survival of container replacement. Typed use
does not require provider credentials. Live-provider compatibility remains open.
