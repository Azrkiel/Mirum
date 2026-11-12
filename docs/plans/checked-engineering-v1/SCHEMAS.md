# V1 reviewed brief and evidence schemas

`backend/engineering/contracts.py` contains provider-free, immutable Pydantic
records. Unknown fields, nonfinite values, numeric strings, booleans used as
numbers, unsupported units, capabilities and versions fail validation. Nested
contract instances are revalidated; collections are tuples. Use validated
constructors or `model_validate_json`, never `model_construct` or
`model_copy(update=...)` on untrusted input. Python constructors require strict
tuple/enum/datetime types; JSON decoders accept their JSON representations.
`model_json_schema()` exports the schemas without importing a provider or CAD.

## Records and review

| Record | Responsibility |
|---|---|
| `CapabilityReference` | Exact capability ID/version and numerical/resource policy versions; lookup never substitutes a newer version. |
| `FixedRequirements` | Normalized length, width, digital tolerance, force, displacement limit, material/allowable/reference and support/load assumptions. Thickness is separate. |
| `BriefContent` | `engineering_brief_v1`, capability reference, fixed requirements, fixed-design/bounded-thickness mode, normalized reviewed choices and all mandatory checks. |
| `BriefDraft` | `engineering_draft_v1`, draft ID/revision, optional complete content, clarification needs and field provenance. Incomplete input can be recorded with `content=null` and explicit clarification. |
| `ReviewAcknowledgement` | Exact draft ID/revision and semantic brief digest, reviewer/time and every acknowledged input/assumption in `REVIEW_FIELDS`. |
| `EngineeringBrief` | `engineering_brief_v1`, immutable finalized content, explicit full digests, matching review and provenance. |
| `CandidateSpec` | `engineering_candidate_v1`, complete reviewed meaning, one permitted `CantileverSpec`, fixed/brief/candidate identities. |
| `CheckResult` | `engineering_check_v1`, declared check ID/state, method/version, quantities/units, comparison limits, brief/candidate/STEP identities and non-passing reason. |
| `EngineeringRun` | `engineering_run_v1`, job identity, capability/policy references, fixed/brief identities, ordered attempts, lifecycle/outcome, timestamps, failures and external bundle envelope. |

No schema infers a missing force, material property or assumption.
`FixedRequirements.to_cantilever(height)` preserves the existing `CantileverSpec`
envelope; every thickness is validated before execution. Fixed-design content
has exactly one normalized height. Bounded content has one to four proposed
values; duplicates are removed and choices sorted before review. Invalid values
are rejected, not clipped. The complete required-check set cannot be weakened.

`EngineeringBrief.from_draft(draft, review, brief_id=...)` is a schema-level
binding operation, not a persistence or HTTP service. It refuses unresolved
clarifications, incomplete field origins, a partial acknowledgement, a changed
digest, another draft ID or another revision. AI origins remain `ai_proposed`
and references remain explicit; model guesses do not become supplied material
facts merely by parsing. Finalization requires the user's acknowledgement of
the exact proposed material values, source label and assumptions.

`BriefDraft.revised(content=..., provenance=..., clarifications=...)` always
increments the revision and returns an unreviewed draft. Even a semantically
identical edit invalidates the prior revision's review. B05 owns draft storage,
expected-revision conflicts and review collection; B11/B12 own transport/UI.
Serialized records are consistency checks, not signatures or proof that a
particular person clicked a control.

## Quantities and provenance

`Quantity(value=..., unit=...).normalized(target)` supports only:

| Target | Explicit input units and factors |
|---|---|
| mm | mm × 1; cm × 10; m × 1000; in × 25.4 |
| N | N × 1; kN × 1000 |
| MPa | Pa × 0.000001; MPa × 1; GPa × 1000 |

Conversion uses decimal representations of the finite input/factor before
returning a normalized float. For example, 1.001 m yields exactly the same
normalized float as 1001 mm. There is no arbitrary dimension rounding. Cross-
dimension conversion, psi, kg as force and implicit unit guesses are rejected.
Display and review the converted result; record original values and conversion
meaning in field provenance rather than silently inserting a proposal.

`ReviewedMassToForce` is a separate explicit operation: positive mass, positive
gravity, a nonblank reference and literal boolean `acknowledged=true` are all
required. There is no default gravity. Attach it to `Provenance.force_conversion`
and mark the force origin `converted`; finalization checks that the normalized
force matches the recorded conversion. Ordinary force inputs remain N/kN.

`MaterialReference` stores modulus, allowable, source kind (`illustrative`,
`user_supplied`, `documented`) and a nonblank source/reference. These labels do
not certify material properties or add a safety factor. Field origins distinguish
user input, AI proposal, explicit conversion and policy. Provenance timestamps
must be timezone-aware. Clarifications and origins remain visible to consumers.

## Canonical identities

All digests are full lowercase SHA-256. Serialization uses the existing
`cem.core.canonical_json` convention: sorted keys, compact separators, ASCII
escaping and no nonfinite values. Validated float fields normalize integer/
float representations; choice order/duplicates and reference edge whitespace
are normalized before hashing.

| Identity | Covered meaning |
|---|---|
| `fixed_requirements_sha256` | Content schema version, full capability/policy reference, normalized fixed requirements including material/source and assumptions, complete mandatory check identifiers. |
| `brief_sha256` | Fixed meaning plus design mode and ascending deduplicated thickness choices. |
| `candidate_sha256` | Complete selected `CantileverSpec`, using the existing `specification_digest`; legacy identity is unchanged. Candidate envelope additionally pins complete brief meaning and both higher-level identities. |
| Artifact SHA-256 | Exact source/STEP/GLB/report/payload bytes, obtained by the runtime/publisher. |

IDs, timestamps, reviewer identity, input mode, field origins and conversion
provenance are outside semantic hashes. Equivalent normalized AI/form inputs
therefore have equal identities while retaining different histories. Material,
force, assumptions or versions change fixed meaning. Mode or choice changes
change brief meaning. A candidate outside the reviewed choices or with altered
fixed inputs cannot deserialize under the existing identities. Hashes prove
association, not external authenticity, engineering truth or physical validation.

## Outcome/evidence shapes and consumer responsibilities

`Lifecycle`: admitted, running, packaging, completed, unavailable, cancelled.
`Outcome`: accepted, rejected, not_evaluated. `CheckState`: passed, failed,
not_evaluated, unavailable. Non-passing checks need a reason. A passed check
requires measured quantities, matching-unit comparison limits and a STEP byte
identity; geometry cannot replace the mandatory beam checks.

`AttemptRecord` binds each check to its candidate/brief. An accepted attempt
requires all 20 mandatory checks passed. Required-stage unavailability cannot
be an ordinary rejected attempt. `EngineeringRun` records a contiguous prefix
of the ascending reviewed choices; only rejection permits a next attempt.
Acceptance commits only with completed lifecycle, a selected accepted candidate
and a complete bundle envelope. Completed rejection requires exhausting the
reviewed choices and cannot select or publish accepted CAD. Nonterminal and
unavailable/cancelled runs retain `not_evaluated`; the latter require a reason.

`ArtifactIdentity.path` is a closed payload-name allowlist, never a filesystem
path from a client. `BundleIdentity.payloads` contains the eight non-manifest
payloads. Manifest/ZIP hashes and byte sizes stay in the external envelope,
avoiding circular self-hashes. `stored_bytes` includes payloads, manifest, archive
and retained metadata and cannot exceed 32 MiB. Accepted run records reject
inconsistent checked-versus-delivered hashes. A publisher must measure actual
bytes and retained size; typed claims do not verify files by themselves.

B04 translates actual native worker reports into these records. B07 performs
the search; B08 determines actual evidence sufficiency and numerical verdicts;
B09 implements lifecycle ownership, cancellation, deadlines and persistence;
B10 verifies bytes and publishes atomically. These schemas do not execute any
of those services, implement a queue, authenticate reviews or certify physical
behavior. Existing legacy APIs/CEM types and digests remain separate/compatible.

Shared examples live in `backend/tests/fixtures/engineering/`: valid draft,
finalized brief, 15 mm candidate, admitted run and invalid-input mutations.
Accepted-run unit fixtures are explicitly synthetic and do not claim native
measurements; B01 retains the actual Linux/OCP evidence.
