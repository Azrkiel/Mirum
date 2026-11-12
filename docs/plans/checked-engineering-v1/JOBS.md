# Durable engineering lifecycle (B09)

`SQLiteStore(root)` exclusively owns a local Linux volume using `flock`, SQLite
WAL and FULL synchronous transactions. `SQLiteBriefRepository` implements the
integrated B05/B06 repository, including exact CAS, immutable retryable reviews,
and owner-bound proposal reservations in the finalization transaction.
`SQLiteProposalLedger` preserves unique revision claims and monotonically
consumed call budgets. Restart releases abandoned reservations and marks running
audits unavailable without deleting claims or resetting calls.

`JobController(store, publisher=None).submit(brief_id, idempotency_key)` admits
one job and reserves 32 MiB before starting work. It accepts only a stored,
validated, finalized brief. Identical key/brief-ID retries return the existing
job; another brief ID conflicts, even when its semantic digest matches.
`read`, `cancel`, `wait` and async `close` provide polling, durable cancellation,
owned completion and graceful shutdown. Read never executes work. The native
admission slot is shared with `/engineering/generate`; that route preserves its
existing within-request caller ordering and response format. Busy native or
storage admission raises `AdmissionBusy` with five-second retry information.

The admitted 420-second deadline includes a cleanup/packaging reserve. Existing
native subprocesses retain 30-second, 1536 MiB and captured-output bounds.
Cancellation stored before the final SQLite transaction wins; a terminal record
is immutable. Selection remains packaging/not_evaluated. Without a publisher,
selection terminates unavailable, never accepted.

B10 publisher interface: `recover()`, `cleanup()`, async
`publish(job_id, brief, SearchResult) -> BundleIdentity`,
`validate(job_id, BundleIdentity)` and `discard(job_id)`. Publish must reassess
captured evidence and atomically durably publish every required byte before
returning. Validate runs before the accepted transaction commits. B09's seam is
not evidence of publication. Diagnostic publication/leases/expiry belong to B10.

## Ownership and recovery

The controller tags owned subprocesses with a random volume-owner identity via
a context variable in the existing ProcessRunner. A new exclusive volume owner
reaps former tagged descendants before reconciling interrupted jobs as
unavailable (or cancelled when cancellation was durable). No process is resumed
and no missing evidence is invented. Normal cancel/deadline/shutdown awaits the
existing bounded ProcessRunner cleanup. Token-scrubbing arbitrary descendants
remain outside ProcessRunner's documented containment guarantee; trusted fixed
engineering workers do not scrub the owner tag. Recovery requires Linux `/proc`.

Only one controller may be constructed per SQLiteStore. Production uses one API
process, one replica, no reload and a local filesystem with reliable flock/fsync.
`WEB_CONCURRENCY`, `UVICORN_WORKERS` and `MIRUM_API_REPLICAS` values other than
`1` are rejected; another owner of the same volume is rejected by the lock.
Separate volumes do not provide distributed admission and are unsupported.

## Container replacement

Compose mounts `engineering_data` at `/tmp/mirum/engineering`, initialized and
owned by `caduser` in the backend image. It is separate from disposable
`/tmp/mirum/work`. Stop the old API container before starting its replacement;
reuse the same named volume. Do not use `docker compose down -v`. Never copy a
live SQLite database without the WAL or an SQLite backup. Back up the entire
quiescent volume, including artifacts. Container replacement must retain the
volume; removing it intentionally removes jobs, drafts, budgets and packages.

Rollback: stop the API, snapshot the volume, and run the matching source version.
Schema user_version is 1. Older code must not silently rewrite a newer database.
The HTTP lifecycle owns initialization/shutdown starting in B11; B09 exercises
these library interfaces directly. Full application qualification remains open.
