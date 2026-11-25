# K51 program retention and archive recovery

Mirum admits at most 256 active programs and applies the existing per-client program, staged-revision, operation, and payload limits. Retiring an idle program frees its active slots. Historical program IDs and decisions remain in a durable, operator-managed archive. Public program enablement remains a separate release decision.

## Configure durable capture

Mount an application-owned, access-controlled archive volume outside the engineering artifact volume. Set `MIRUM_ENGINEERING_PROGRAM_ARCHIVE_DIR` to its existing mount path on the API process. Use a persistent mount, since release scratch paths use tmpfs. The archive volume is retained by the operator and needs its own capacity and backup policy.

When configured, Mirum saves and syncs a complete replay in `.captures/$PROGRAM_ID/` after delivered verification and before moving the accepted head. If the replay cannot be saved and verified, the revision fails with `archive_unavailable` and the prior accepted head stays current. Startup recovery follows the same rule. A rollback shares its original evidence revision's capture.

For older publications, or when archival was unconfigured, use the program owner's authorized connection to capture each original published revision before its delivery expires. The normal delivery TTL is six hours.

From the repository root on Linux, using the pinned release environment:

```sh
PYTHONPATH=backend python -m engineering.program.replay capture "$REVISION_ID" "$CAPTURE_PATH"
PYTHONPATH=backend python -m engineering.program.replay verify "$CAPTURE_PATH"
```

The capture uses `MIRUM_API_URL` and the existing access-token configuration. It contains exact MEL, ProgramIR, execution lock, check and operation plans, and the published delivery ZIP with its reviewed brief, reports, and artifacts. Retain the complete, verified capture. The existing v0.1 replay size limits apply.

## Retire an idle program

Stop the API process for this volume and run as the engineering volume owner. The store's exclusive owner lock prevents maintenance against a volume the service still has open. Use an existing, access-controlled archive directory on durable storage outside the engineering volume.

```sh
PYTHONPATH=backend python -m engineering.program.retention retire \
  "$ENGINEERING_VOLUME" "$PROGRAM_ID" "$ARCHIVE_ROOT"
```

Retirement collects the saved captures automatically. Add `--replay PATH` for each original publication that predates automatic capture or needs a separate archived copy. Never-published programs need no replay. Retirement requires at least seven days since the last revision or program update and refuses any staged or running revision. After staging expiry has settled a row, its updated timestamp starts a new idle period.

The command validates captures against the stored decisions, writes `history.json` and the exact replay ZIPs under `$ARCHIVE_ROOT/$PROGRAM_ID`, and syncs the files and directory before deleting active program, revision, journal, and delivered-evidence rows in one SQLite transaction. The archive retains revision IDs, journal entries, decisions, and evidence. Once the database commit succeeds, the command removes the program's redundant staging captures. The existing job and publication cleanup still owns transient jobs and bundles. The SQLite schema version and active journal's append-only guards are preserved.

If retirement is interrupted, rerun the same command. A complete installed archive is verified and reused, including when the captures are no longer in the original staging location. A failed transaction leaves active rows and their guards intact. A missing capture, archive mismatch, or corruption leaves the active program in place. For an older accepted revision whose exact capture and delivery are both unavailable, retirement is blocked. An operator can remove abandoned `.retire-*` staging directories after confirming no maintenance process is using the archive root.

## Restore an exact replay

```sh
PYTHONPATH=backend python -m engineering.program.retention restore \
  "$ARCHIVE_ROOT/$PROGRAM_ID" "$REVISION_ID" "$RESTORED_ZIP"
PYTHONPATH=backend python -m engineering.program.replay verify "$RESTORED_ZIP" --measure
```

Restore validates the stored decision and digest, then writes the exact replay ZIP to a new path. A restored rollback resolves to its original evidence revision. Native measurement needs the reviewed lock and compatible native dependencies. Offline recovery does not reactivate the archived program or consume admission capacity.
