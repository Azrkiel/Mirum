# V1 evidence validation

`engineering.policy.assess(candidate, evaluation)` returns an immutable
`PolicyDecision(available, outcome, checks, reasons)`. It accepts data only; no
AI text or discretionary pass override exists. `NativeReport` and its nested
physics, mesh, structural and per-check models reject unknown fields/nonfinite
numbers and unsupported scope or claims. B04 adds `report_sha256` to its mapping;
`report_digest(report)` hashes its exact canonical JSON content.

The policy revalidates candidate/brief/check/STEP associations, checks the native
report's source identities against the pinned checkout and dependency versions
against the running runtime, and requires the complete native structural checks.
It compares the actual native check prefix to the capability's mandatory order,
requires quantities and exact policy/spec limits, and checks numerical pass flags
against those comparisons. Measured geometry and mesh/solution fields must agree
with the per-check evidence. It never runs a new solver or re-creates geometry.

A native measured failure may stop evaluation: the preceding checks and failed
check must have evidence, with later mandatory checks explicitly not_evaluated.
This yields an available rejected candidate with typed measured reasons. Missing,
malformed, unknown-method, stale or contradictory evidence yields unavailable /
not_evaluated. It is not an ordinary candidate rejection and must stop search.
Native backend/inspection failure cannot be relabelled as a design rejection.

For a passing candidate, all 20 checks must pass. Captured source/STEP/preview
bytes must match the report hashes and sizes; source must be the fixed builder's
source for that exact spec. No source is rerun. Rejections cannot expose accepted
geometry. Geometry-only structural reports cannot acquire a beam claim; standards
remain not_evaluated and engineering_certified is false. Extra manufacturing or
certification report fields are rejected. Legacy geometry-only acceptance and
legacy engineering APIs remain separate and unchanged.

B04's adapter and B07's search both enforce this policy. B09 maps unavailable to
terminal unavailable / not_evaluated, advances only after an available rejection,
and maps selection to packaging / not_evaluated. Exhausting reviewed rejections
can commit completed / rejected. B10 must reassess the selected evaluation and
hash actual delivered payloads before B09 commits completed / accepted with the
complete bundle envelope. A passing candidate is not a published accepted run.

Hashes prove consistency inside this trusted local workflow, not authenticity
against an attacker able to rewrite every record. They do not prove physical
material properties, manufacturing release or standards conformity. B10 owns
manifest/report delivery identities and atomic publication; B13 owns full release
qualification. Replay must use the saved brief with the pinned verifier/runtime.
No migration/configuration change is required.
