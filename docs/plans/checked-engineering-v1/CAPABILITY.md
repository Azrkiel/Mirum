# Rectangular cantilever capability 1.0.0

`engineering.capabilities.CAPABILITY` is the immutable registered descriptor.
Metadata imports Pydantic and the strict CEM identity helpers, without importing
CAD, NumPy, agents or a provider SDK. `list_capabilities()` returns only this
descriptor. `get_capability(id, version, numerical_policy_version=...,
resource_policy_version=...)` resolves exact versions and raises
`UnsupportedCapability` with `as_dict() == {'state': 'unsupported', 'reason': ...}`
when unavailable. There is no fallback to a different policy or family.

## Supported meaning

One homogeneous, solid rectangular prism, positive XYZ datum, length along X,
ideal clamp at X=0 and static transverse +Z end force at X=L. Units are mm, N,
MPa. Both section dimensions must be at most one tenth of the length. Preserve
the `CantileverSpec` limits, independent STEP measurements, digital tolerances,
small displacement/strain envelope, FEA mesh convergence, analytic benchmark and
equilibrium. `CAPABILITY.required_checks` lists all 20 mandatory native/design/
beam checks. A valid B-rep alone does not establish beam acceptance.

Every feature in `CAPABILITY.unsupported_features` has a rejection fixture:
holes, fillets, extra bodies, varying cross-section, other supports or loads,
shear deformation, fatigue, buckling, contact, thermal analysis, general 3D
stress, physical material qualification, manufacturing variation and standards
certification. Unregistered features and unknown specification fields also fail.
Material and allowable inputs require an explicit user-approved reference; their
presence does not certify stock properties. Standards remain `not_evaluated` and
`engineering_certified` is always false.

## Preparation and selection

`validate_beam_request(specification, mode='fixed_design', ...)` checks the legacy
typed spec and rejects unsupported support/load/feature combinations. Fixed mode
uses the specification's single thickness and disallows an additional list.
`bounded_thickness` requires one to four proposed choices, validates all of them,
and returns an ascending, deduplicated tuple of valid `CantileverSpec` objects.
Invalid values are rejected, never clipped. Downstream preparation displays this
normalized set for review. Execution may only use the finalized set and must stop
at the first passing choice: **smallest passing thickness in the reviewed set**.
There is no global optimization claim or permission to change physical inputs.

This preparation helper does not execute or finalize a brief. B03 supplies the
immutable brief/candidate identities. B04 implements `CapabilityAdapter`:

```python
descriptor: CapabilityDescriptor
def candidates(brief: EngineeringBrief) -> tuple[CandidateSpec, ...]: ...
async def evaluate(candidate: CandidateSpec) -> dict: ...
```

The evaluation mapping adapts the existing service evidence and bytes; missing
required evidence cannot produce acceptance. The original `cem.core.CEM`
protocol and glider signatures remain unchanged. Legacy `approved_candidates`
and `/engineering/generate` keep caller ordering and existing response shapes.

## Pinned policies and availability

`beam_numerical_v1` records existing verifier thresholds, including 10:1 minimum
slenderness, 2% span displacement, 0.5% strain, 1e-6 mm topology/datum budgets,
and 1e-7 relative mesh/analytic/equilibrium thresholds. The descriptor records
the remaining geometric guards and input bounds from the existing verifier.

`engineering_resources_v1` records every CONTRACT.md compute/AI/storage/retention
default. Native stages retain 30 s, 1,536 MiB and 1 MiB captured output. V1 allows
four attempts, one job/no queue, 420 s overall, 32 MiB/run, 50 artifact-bearing
runs, 512 MiB storage, six-hour bundles and seven-day compact state. AI gets one
call plus one repair, 45 s each, 8,000 prompt characters and 16 KiB structured
responses. Deployment is one API process/replica with durable application-owned
storage, separate disposable native scratch and shared legacy native admission.

B01's three-candidate reference search took 27.47 s; its rejected search took
24.47 s. These establish reference feasibility in the pinned Linux runtime,
not worst-case performance across the full input envelope. B09/B10/B13 enforce
and qualify the declared limits, recovery, leases, expiry and coexistence.

The descriptor lists allowed target exports, including the future ZIP. Metadata
is not a native-availability probe or a promise that later API/package work is
implemented. Native availability must be checked by the adapter/consumer; an
unavailable mandatory stage must stop execution, never downgrade to geometry-only
acceptance. No provider is needed for metadata or typed beam execution.
