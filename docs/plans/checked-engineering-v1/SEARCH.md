# Reviewed deterministic search

`await engineering.search.search(finalized_brief, adapter=None, cancelled=None,
deadline=None, on_progress=None)` validates the entire immutable B03 brief and
the adapter's exact ascending candidate tuple before native work. The default
adapter is B04's fixed version registration. Execution never sorts, expands,
clips or edits reviewed choices; legacy `generate_with_feedback` remains separate
and retains caller ordering. No provider is imported or called.

`SearchResult.state` is `selected`, `rejected`, `unavailable` or `cancelled`.
`selected` means a candidate passed the adapter checks, pending policy and atomic
publication. It is not a completed/accepted `EngineeringRun`. B09 maps selection
to packaging; B08/B10 must validate evidence and publish before acceptance commits.
`rejected` means only no reviewed candidate passed, with no global feasibility
claim. A required-stage failure stops immediately as unavailable.

Results retain immutable `AttemptRecord` history plus corresponding adapter
`evaluations` containing native reports, checks, measurements and identities.
`selected` contains the selected evaluation's captured bytes. Non-selected
results strip artifact bytes from evaluations and expose diagnostics only.
Consumers must revalidate serialized records; byte mappings are an internal
publisher seam and cannot be returned directly through a public JSON endpoint.

`cancelled()` is a synchronous predicate; `deadline` is an absolute finite
`time.monotonic()` value. Without one, the library gets a 420-second budget from
entry. B09 must pass the deadline established at admission, including packaging
and cleanup reserve. The predicate/deadline are checked before/after progress
and evaluation, and polled during active work. Control stops cancel and await the
owned task so existing ProcessRunner cleanup completes. External `Task.cancel()`
also cleans up and propagates `CancelledError`. No new process runner or solver
is introduced. Hooks do not implement durable cancel/terminal-commit ownership.

`on_progress(SearchProgress)` is an async observer: `starting` precedes each
candidate, `attempt` carries its typed record, then normal completion emits
`selected` or `rejected`. Cancellation/unavailability is returned in the result;
no terminal observer is awaited after a stop. Observers are bounded by the same
controls and cannot initiate another candidate. Observer failure stops the run.
Events describe tentative execution, never permission to commit acceptance.

Native reference/all-fail/fixed/early-stop tests execute B04's real adapter.
Cancellation, unavailable-stage and observer tests use explicitly injected async
seams or replay actual recorded native results; they are not proof of durable
job cancellation/restart. B09/B13 own native-process/API lifecycle qualification.
No storage/configuration migration is required.
