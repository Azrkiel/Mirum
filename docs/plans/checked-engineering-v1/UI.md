# Checked beam workflow

Choose **Checked beam** in the existing Streamlit workflow selector. Typed form
preparation works without provider credentials. The reference values are labelled
illustrative, and every physical value, source and assumption must be reviewed.
The force is N in +Z at X=L; the ideal clamp is X=0. Kg is not silently converted
to N. Geometry, material source, limits, normalized thickness set and assumptions
remain visible before the exact acknowledgement/finalize action.

Save draft prepares a revision. Finalize reviewed brief binds its exact ID,
revision and server digest; Run reviewed beam checks submits only that immutable
brief. AI proposes data through the same service and never executes geometry.
Missing/ambiguous fields remain empty or have visible questions. Unsupported
requirements block saving/finalization; only an explicit new request leaves those
requirements behind. Typed edits merge existing unknown inputs, preserving them
as review blockers rather than silently dropping them.

Edits invalidate launch confirmation while preserving the old job for cancellation
and inspection. Another tab's update blocks editing/review until Load latest
revision. Query parameters carry draft/brief/job IDs, so a refresh reconstructs
the durable state. Duplicate Run clicks use the same brief-bound idempotency key.
The result card always identifies its own job and semantic brief; it explicitly
labels an old result when the current draft has changed.

The preview reads the actual captured GLB, checks its accepted payload digest,
transforms glTF Y-up back to CAD XYZ and renders the mesh locally with Plotly.
Clamp/force annotations use that job's selected spec, never current editor values.
No CDN viewer or new CAD execution is used. Every offered file is fetched through
the authenticated backend lease, byte-checked and cached by job/manifest/format.
Polling checks current delivery before showing cached previews/downloads. Expired
or unavailable delivery clears the job cache and withholds accepted files. An
already downloaded client copy cannot be revoked by server expiry.

Rejected, cancelled, unsupported, unavailable and expired results have distinct
actions/messages. Rejections offer only diagnostic downloads. The report and all
accepted payloads identify one revision. Scope remains digital beam checks;
manufactured behavior, physical material qualification, manufacturing release and
standards conformity are not evaluated.

## Local native browser verification

Use B01's Linux image with source mounted read-only. Create a named engineering
volume owned by UID/GID 10001. Mount it at /tmp/mirum/engineering and mount an
owned /tmp/mirum tmpfs with uid=10001,gid=10001,mode=0700. The latter is necessary:
Docker's automatically created parent of a nested volume is otherwise root-owned
and prevents scratch creation. The backend Dockerfile pins the same UID/GID and
Compose config supplies the matching tmpfs. Legacy scratch remains disposable.

Run scripts/checked_engineering_ui_server.py with host ports bound only to
127.0.0.1 (8501 frontend, 8000 backend). It excludes provider credentials and
owns graceful termination of both servers. Reuse the named volume across restart.
The exact tested Docker/browser commands are retained in B12's handoff/evidence.

The browser probe requires a local Playwright package and Chromium executable:
PLAYWRIGHT_MODULE, PLAYWRIGHT_EXECUTABLE, CHECKED_CONTAINER (the owned test
container), optionally CHECKED_UI_URL/CHECKED_API_URL. Run:
`node scripts/checked_engineering_browser_probe.cjs <evidence-directory> <tested-SHA>`.
It blocks external browser requests and verifies real native reference selection,
refresh, ten downloaded byte hashes, two-tab/edit invalidation, no-provider typed
fallback, unsupported HTTP-seeded input, cancellation, native rejection, actual
OS publication write failure and accelerated persisted expiry. The expiry case
does not claim a six-hour wall-clock wait. Restart the test server after source
changes; imported Streamlit modules may remain cached on mounted filesystems.

Windows verification may need the installed HTTPS-scanner CA for npm, as B01
documents for Docker builds. Supply NODE_EXTRA_CA_CERTS using that trusted local
certificate; never disable TLS verification or commit the certificate. No user
provider credential or prompt belongs in browser evidence.
