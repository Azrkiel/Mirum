# F01 baseline interface inventory

Base: main at 456e622 (456e6220df4962f150ba2be4aa84d3ecb2671fac). Date: 2026-11-21. Task: [F01](tasks/F01-scope-and-reuse.md); decisions: [F01-DECISIONS.md](F01-DECISIONS.md).

Scope: the implemented engineering backend under backend/engineering/ that Phase 3 builds on: capability descriptors (capabilities.py), adapters, proposers and grounding, brief/contract models (contracts.py, briefs.py, spec.py, plate_spec.py, regen_spec.py, proposal_contract.py), policy and limit records, job/bundle/store schema (jobs.py, bundles.py, store.py, admission.py) and HTTP routes (api_v1.py under /engineering/v1, plus the legacy /engineering and /engineering/regen routes).

Method: produced by a read-only Codex worker (gpt-6.2-sol) reading the source at the base SHA; no code was run. The phase manager checked every row mechanically (each `file:line` exists at the base SHA and the row's symbol appears within three lines of it: 251 of 251 references) and spot-checked version strings by hand (for example `engineering_brief_v1` at contracts.py:135, `capability_router_v1` at router.py:14, `euler_bernoulli_beam_fem_v1` at capabilities.py:101). Line numbers are valid at 456e622 only; later code moves them.

Columns: Area (capability, adapter, proposer, contract-model, brief, job, bundle, store, route, policy/limit), exact symbol or `METHOD /path`, kind, definition `file:line`, literal version or schema strings (`none` if the symbol has none), notes.

## Inventory

| Area | Name | Kind | File:line | Version / schema string | Notes |
|---|---|---|---|---|---|
| capability | `CapabilityDescriptor` | Pydantic model | `backend/engineering/capabilities.py:94` | `1.0.0`, `euler_bernoulli_beam_fem_v1` | Beam descriptor. Modes `fixed_design`, `bounded_thickness`. |
| capability | `CAPABILITY` / `rectangular_cantilever` | Descriptor instance | `backend/engineering/capabilities.py:133` | `1.0.0`, `beam_numerical_v1`, `engineering_resources_v1` | `checked_design` in job API. `CantileverAdapter`, AI `BriefProposer`, no rules proposer. |
| capability | `GeneratorDescriptor` | Pydantic model | `backend/engineering/capabilities.py:195` | none | Claims `geometry_checks_only`, `screening_only`, `checked_design`. Certification `False`, standards `not_evaluated`. |
| capability | `ConditionalCheck` | Pydantic model | `backend/engineering/capabilities.py:172` | none | Adds a check when a specification leaf equals a value. |
| capability | `DRAGSTER` / `co2_dragster` | Descriptor instance | `backend/engineering/capabilities.py:466` | `2.0.0`, `co2_dragster_numerical_v1`, `engineering_resources_v1` | `geometry_checks_only`. `DragsterAdapter`, `dragster_proposer.RulesProposer` and `.AiProposer`. |
| capability | `REGEN` / `regen_chamber` | Descriptor instance | `backend/engineering/capabilities.py:510` | `1.0.0`, `regen_chamber_numerical_v1`, `engineering_resources_v1` | `geometry_checks_only`. `RegenAdapter`, `regen_proposer.AiProposer`. Run budget 256 MiB. |
| capability | `GLIDER` / `glider_cem` | Descriptor instance | `backend/engineering/capabilities.py:555` | `1.0.0`, `glider_cem_numerical_v1`, `engineering_resources_v1` | `screening_only`. `GliderAdapter`, `glider_proposer.AiProposer`. Construction controls conditional checks. |
| capability | `PLATE` / `rectangular_plate` | Descriptor instance | `backend/engineering/capabilities.py:601` | `1.0.0`, `plate_numerical_v1`, `engineering_resources_v1` | `checked_design`. `PlateAdapter`, `PlateBrief` specification, no proposers. |
| capability | `GENERATORS` | Registry tuple | `backend/engineering/capabilities.py:632` | none | `DRAGSTER`, `REGEN`, `GLIDER`, `PLATE`. |
| capability | `list_capabilities` | Registry entry point | `backend/engineering/capabilities.py:635` | none | Returns beam first, then `GENERATORS`. |
| capability | `get_generator` | Registry lookup | `backend/engineering/capabilities.py:640` | none | Generator ID lookup returning a descriptor or `None`. |
| capability | `get_capability` | Versioned registry lookup | `backend/engineering/capabilities.py:644` | `beam_numerical_v1`, `engineering_resources_v1` | Matches ID and all three versions. Listed literals are default policies. |
| capability | `GeneratorDescriptor.parse_spec` | Schema dispatch | `backend/engineering/capabilities.py:225` | none | Resolves `spec_schema` and validates via the JSON boundary. |
| capability | `GeneratorDescriptor.required_checks` | Check selection | `backend/engineering/capabilities.py:232` | none | Base checks plus matching conditional checks. |
| capability | `GeneratorDescriptor.review_fields` | Review field selection | `backend/engineering/capabilities.py:236` | none | Normalized leaves prefixed with `specification.`. |
| capability | `GeneratorDescriptor.refuse` | Refusal dispatch | `backend/engineering/capabilities.py:239` | none | Calls descriptor refusal hook where supplied. |
| capability | `GeneratorDescriptor.diagnostic_payloads` | Property | `backend/engineering/capabilities.py:262` | none | Descriptor payloads ending in `.json`. |
| adapter | `resolve` | Lazy import lookup | `backend/engineering/capabilities.py:658` | none | Resolves `module:attribute`. |
| adapter | `adapter_installed` | Availability probe | `backend/engineering/capabilities.py:664` | none | Probes the adapter module via `find_spec`. |
| adapter | `get_adapter` | Versioned adapter factory | `backend/engineering/capabilities.py:672` | none | Direct beam factory or lazy descriptor import and construction. |
| adapter | `CapabilityAdapter` | Protocol | `backend/engineering/capabilities.py:720` | none | `descriptor`, `candidates`, async `evaluate`, generator-only `measurements`. |
| adapter | `create_runtime` | Runtime factory | `backend/engineering/api_v1.py:144` | none | Injects generator runner getters, adds plate, builds AI only with `MIRUM_ENGINEERING_AI_DRAFTS`. |
| adapter | `JobController._adapter_for` | Runtime lookup | `backend/engineering/jobs.py:228` | none | Uses injected generator map or beam override. Missing generator raises `UnavailableCapability`. |
| adapter | `search` | Async execution entry point | `backend/engineering/search.py:74` | none | Resolves default adapter and requires candidates to match the finalized brief. |
| adapter | `CantileverAdapter` | Adapter class | `backend/engineering/cantilever.py:61` | none | Descriptor `CAPABILITY`. |
| adapter | `CantileverAdapter.candidates` | Candidate entry point | `backend/engineering/cantilever.py:64` | none | One `CandidateSpec` per reviewed beam height. |
| adapter | `CantileverAdapter.evaluate` | Async evaluation entry point | `backend/engineering/cantilever.py:68` | none | Runs deterministic builder, binds evidence and gates with `assess`. |
| adapter | `report_checks` | Evidence translation | `backend/engineering/cantilever.py:17` | `rectangular_cantilever_v1`, `independent_step_beam_verifier_v1` | Scope and method literals. `method_version` is a hash of verifier implementation identities. |
| adapter | `generate_verified` | Async beam service | `backend/engineering/service.py:18` | none | Builds approved spec and verifies captured STEP in bounded subprocess. Returns `accepted` or `rejected`. |
| adapter | `approved_candidates` | Legacy beam candidate entry point | `backend/engineering/service.py:77` | none | Up to four unique explicit heights, in caller order. |
| adapter | `generate_with_feedback` | Async legacy beam execution | `backend/engineering/service.py:87` | none | Tries approved heights in order and stops at first acceptance. |
| adapter | `main` | Beam subprocess entry point | `backend/engineering/worker.py:9` | none | Validates spec JSON, invokes `verify_step`, writes report JSON. |
| adapter | `DragsterAdapter` | Adapter class | `backend/engineering/dragster_adapter.py:271` | none | Optional runner getter. |
| adapter | `DragsterAdapter.candidates` | Candidate entry point | `backend/engineering/dragster_adapter.py:278` | none | One reviewed generator candidate. |
| adapter | `DragsterAdapter.measurements` | Measurement entry point | `backend/engineering/dragster_adapter.py:286` | none | Parses captured report bytes. |
| adapter | `DragsterAdapter.evaluate` | Async evaluation entry point | `backend/engineering/dragster_adapter.py:289` | none | Runs original generator with required evidence. |
| adapter | `parse_report` | Report parser | `backend/engineering/dragster_adapter.py:95` | none | Extracts closed dragster check measurements. |
| adapter | `read_bounded` | Artifact reader | `backend/engineering/dragster_adapter.py:157` | none | Reads bounded captured files. |
| adapter | `revalidate` | Specification validator | `backend/engineering/dragster_adapter.py:174` | `2` | Requires schema 2, registered style and absent stock. |
| adapter | `build_evaluation` | Evidence assembly | `backend/engineering/dragster_adapter.py:202` | `2`, `co2_dragster_numerical_v1` | Numeric report schema and check method version. |
| adapter | `failure_reason` | Failure translation | `backend/engineering/dragster_adapter.py:261` | none | Emits fixed text with recognized check IDs when present. |
| adapter | `RegenAdapter` | Adapter class | `backend/engineering/regen_adapter.py:315` | none | Optional runner getter. |
| adapter | `RegenAdapter.candidates` | Candidate entry point | `backend/engineering/regen_adapter.py:322` | none | One reviewed generator candidate. |
| adapter | `RegenAdapter.measurements` | Measurement entry point | `backend/engineering/regen_adapter.py:330` | none | Parses captured report bytes. |
| adapter | `RegenAdapter.evaluate` | Async evaluation entry point | `backend/engineering/regen_adapter.py:342` | none | Uses regen runner and captured geometry evidence. |
| adapter | `regen_provenance` | Provenance builder | `backend/engineering/regen_adapter.py:78` | `engineering_resources_v1` | Records runtime and builder identities with resource budget. |
| adapter | `with_provenance` | Evidence enrichment | `backend/engineering/regen_adapter.py:97` | none | Adds regen runtime provenance. |
| adapter | `parse_report` | Report parser | `backend/engineering/regen_adapter.py:173` | none | Extracts closed regen check measurements. |
| adapter | `read_bounded` | Artifact reader | `backend/engineering/regen_adapter.py:223` | none | Reads bounded captured files. |
| adapter | `rejected_report_bytes` | Diagnostic serializer | `backend/engineering/regen_adapter.py:240` | none | Encodes rejected report for diagnostics. |
| adapter | `revalidate` | Specification validator | `backend/engineering/regen_adapter.py:245` | none | Rechecks descriptor refusal and spec schema. |
| adapter | `build_evaluation` | Evidence assembly | `backend/engineering/regen_adapter.py:255` | `regen_chamber_numerical_v1` | Binds captured report and typed checks. |
| adapter | `GliderAdapter` | Adapter class | `backend/engineering/glider_adapter.py:359` | none | Optional runner argument. |
| adapter | `GliderAdapter.candidates` | Candidate entry point | `backend/engineering/glider_adapter.py:367` | none | One reviewed generator candidate. |
| adapter | `GliderAdapter.measurements` | Measurement entry point | `backend/engineering/glider_adapter.py:381` | `glider_cem_evaluation_v1` | Reads the adapter-owned report schema. |
| adapter | `GliderAdapter.evaluate` | Async evaluation entry point | `backend/engineering/glider_adapter.py:386` | none | Calls CEM discovery and design. |
| adapter | `assemble_evaluation` | Evidence assembly | `backend/engineering/glider_adapter.py:203` | `glider_cem_evaluation_v1`, `glider_cem_checks_v1` | Evaluation schema and check method version. |
| adapter | `PlateAdapter` | Adapter class | `backend/engineering/plate_adapter.py:287` | none | Optional runner and stage injection. |
| adapter | `PlateAdapter.candidates` | Candidate entry point | `backend/engineering/plate_adapter.py:296` | none | One `GeneratorCandidate` wraps a `PlateBrief`. |
| adapter | `PlateAdapter.measurements` | Measurement entry point | `backend/engineering/plate_adapter.py:307` | `plate_generator_report_v1` | Parses captured generator report. |
| adapter | `PlateAdapter.evaluate` | Async evaluation entry point | `backend/engineering/plate_adapter.py:337` | none | Tries reviewed plate thicknesses within one generator candidate. |
| adapter | `canonical_brief` | Serializer | `backend/engineering/plate_adapter.py:72` | none | Canonical `PlateBrief` bytes. |
| adapter | `check_data` | Check translation | `backend/engineering/plate_adapter.py:134` | none | Derives per-check state, measurements and reason from an attempt. |
| adapter | `parse_report` | Report parser | `backend/engineering/plate_adapter.py:148` | `plate_generator_report_v1` | Checks generator report schema before extracting measurements. |
| adapter | `read_bounded` | Artifact reader | `backend/engineering/plate_adapter.py:158` | none | Reads bounded worker files. |
| adapter | `build_evaluation` | Evidence assembly | `backend/engineering/plate_adapter.py:180` | `plate_generator_report_v1`, `plate_numerical_v1` | Report schema and check method version. |
| adapter | `worker_main` | Plate subprocess entry point | `backend/engineering/plate_adapter.py:275` | none | Stages and verifies a `PlateSpec` and writes result files. |
| proposer | `make_proposer` | Beam AI factory | `backend/engineering/proposer.py:338` | none | Returns `CapabilityProposers` when enabled and `None` when disabled. |
| proposer | `CapabilityProposers` | Dispatcher class | `backend/engineering/proposer.py:351` | none | Forwards beam AI and resolves rules by capability. |
| proposer | `CapabilityProposers.propose` | Async forwarding entry point | `backend/engineering/proposer.py:366` | none | Delegates to beam AI proposer. |
| proposer | `CapabilityProposers.rules` | Rules dispatch entry point | `backend/engineering/proposer.py:369` | none | Delegates to `rules_proposer`. |
| proposer | `rules_proposer` | Rules factory | `backend/engineering/proposer.py:373` | none | Lazy descriptor `rules` lookup. Available with AI off. |
| proposer | `BriefProposer` | Beam AI proposer class | `backend/engineering/proposer.py:395` | `engineering_extraction_v1`, `engineering_proposal_v1` | Uses a provider, ledger, `BriefService` and call budget. |
| proposer | `BriefProposer.propose` | Async proposal entry point | `backend/engineering/proposer.py:406` | `engineering_extraction_v1`, `engineering_proposal_v1` | Revision-bound beam draft update. |
| proposer | `provider_schema` | Provider schema entry point | `backend/engineering/proposer.py:100` | `engineering_proposal_v1` | Builds provider schema for `ProposalResponse`. |
| proposer | `ProposalProvider` | Provider protocol | `backend/engineering/proposer.py:148` | none | `available`, `settings`, async `generate`. |
| proposer | `GeminiProposalProvider` | Provider class | `backend/engineering/proposer.py:164` | none | Implements the proposal provider boundary. |
| proposer | `GeminiProposalProvider.generate` | Async provider entry point | `backend/engineering/proposer.py:178` | none | Sends structured generation request and returns `ProviderReply`. |
| proposer | `ProposedMaterial` | Pydantic model | `backend/engineering/proposer.py:54` | none | Optional `source_kind`: `illustrative`, `user_supplied`, `documented`. |
| proposer | `ProposedRequirements` | Pydantic model | `backend/engineering/proposer.py:61` | none | Optional beam requirements with fixed units and assumptions literals. |
| proposer | `ProposalFields` | Pydantic model | `backend/engineering/proposer.py:72` | `engineering_brief_v1` | Optional beam fields. Modes `fixed_design`, `bounded_thickness`. |
| proposer | `ProposalResponse` | Pydantic model | `backend/engineering/proposer.py:84` | `engineering_proposal_v1` | States `proposed`, `clarification_required`, `unsupported`. |
| proposer | `ProviderSettings` | Pydantic model | `backend/engineering/proposer.py:133` | none | Provider `gemini` or `fixture`. |
| proposer | `ProviderReply` | Pydantic model | `backend/engineering/proposer.py:142` | none | Structured provider reply wrapper. |
| proposer | `ValidationErrorLocation` | Pydantic model | `backend/engineering/proposer.py:240` | none | Audited validation error location. |
| proposer | `ValidationFailure` | Pydantic model | `backend/engineering/proposer.py:245` | none | Audited attempt failure. |
| proposer | `validation_failure` | Audit record builder | `backend/engineering/proposer.py:254` | none | Creates a bounded validation-failure record. |
| proposer | `ProposalAudit` | Pydantic model | `backend/engineering/proposer.py:267` | `engineering_extraction_v1`, `engineering_proposal_v1` | Status `running`, `proposed`, `clarification_required`, `unsupported`, `unavailable`, `cancelled`, `conflict`. |
| proposer | `ProposalResult` | Pydantic model | `backend/engineering/proposer.py:317` | none | States `proposed`, `clarification_required`, `unsupported`, `unavailable`, `cancelled`. |
| proposer | `RulesProposer` | Dragster rules proposer class | `backend/engineering/dragster_proposer.py:61` | none | Deterministic style/preset selection. |
| proposer | `RulesProposer.propose` | Rules proposal entry point | `backend/engineering/dragster_proposer.py:64` | none | Returns `(payload, issues, provenance)`. |
| proposer | `DragsterProposal` | Dataclass | `backend/engineering/dragster_proposer.py:84` | none | States `proposed`, `clarification_required`, `unsupported`, `unavailable`. |
| proposer | `AiProposer` | Dragster AI proposer class | `backend/engineering/dragster_proposer.py:92` | `dragster_interpret_v1` | Budget and optional interpreter injection. |
| proposer | `AiProposer.propose` | Async dragster proposal entry point | `backend/engineering/dragster_proposer.py:96` | `dragster_interpret_v1` | Interprets prompt and returns `DragsterProposal`. |
| proposer | `make_ai_proposer` | Dragster AI factory | `backend/engineering/dragster_proposer.py:137` | none | Returns `None` when disabled. |
| proposer | `RegenFields` | Pydantic model | `backend/engineering/regen_proposer.py:47` | none | Sparse regen extraction fields. |
| proposer | `RegenResponse` | Pydantic model | `backend/engineering/regen_proposer.py:63` | none | States `proposed`, `clarification_required`, `unsupported`. Preset `small`, `reference` or `None`. |
| proposer | `provider_schema` | Provider schema entry point | `backend/engineering/regen_proposer.py:77` | none | Builds provider schema for `RegenResponse`. |
| proposer | `RegenProposal` | Dataclass | `backend/engineering/regen_proposer.py:121` | none | Generator proposal fields plus `reason_id`. |
| proposer | `AiProposer` | Regen AI proposer class | `backend/engineering/regen_proposer.py:144` | `regen_extraction_v1` | Budget and optional provider injection. |
| proposer | `AiProposer.propose` | Async regen proposal entry point | `backend/engineering/regen_proposer.py:184` | `regen_extraction_v1` | Returns a conforming `RegenProposal`. |
| proposer | `GliderProposal` | Dataclass | `backend/engineering/glider_proposer.py:51` | none | Generator proposal fields plus `reason_id`. |
| proposer | `AiProposer` | Glider AI proposer class | `backend/engineering/glider_proposer.py:90` | `glider_requirements_sparse_v1` | Budget and optional provider injection. |
| proposer | `AiProposer.propose` | Async glider proposal entry point | `backend/engineering/glider_proposer.py:113` | `glider_requirements_sparse_v1` | Returns a conforming `GliderProposal`. `base_spec` is ignored. |
| proposer | `GliderSparseFields` | Pydantic model | `backend/engineering/glider_extraction.py:76` | none | Optional mission fields. |
| proposer | `GliderSparseResponse` | Pydantic model | `backend/engineering/glider_extraction.py:101` | none | Out-of-family boolean, sparse fields, questions and unsupported features. |
| proposer | `field_kinds` | Grounding map builder | `backend/engineering/glider_extraction.py:159` | none | Derives grounding field types for the objective. |
| proposer | `provider_schema` | Provider schema entry point | `backend/engineering/glider_extraction.py:165` | none | Builds provider schema for `GliderSparseResponse`. |
| proposer | `extract_sparse` | Async extraction entry point | `backend/engineering/glider_extraction.py:227` | `glider_requirements_sparse_v1` | Budgeted structured glider extraction. |
| proposer | `ENVELOPE_VERSION` | Version constant | `backend/engineering/proposal_contract.py:39` | `capability_proposal_v1` | Declared constant. No version member in `CapabilityProposal`. |
| proposer | `ProposalState` | Literal alias | `backend/engineering/proposal_contract.py:42` | none | `proposed`, `clarification_required`, `unsupported`, `unavailable`. |
| proposer | `CapabilityProposal` | Runtime-checkable protocol | `backend/engineering/proposal_contract.py:47` | none | `state`, `payload`, `issues`, `provenance`, `reasons`. |
| proposer | `CapabilityAiProposer` | Runtime-checkable protocol | `backend/engineering/proposal_contract.py:57` | none | Async `propose(prompt, base_spec=None)`. |
| proposer | `reference` | Capability reference builder | `backend/engineering/proposal_contract.py:66` | none | Emits ID, capability version, numerical version and resource version. |
| proposer | `conform` | Proposal validation entry point | `backend/engineering/proposal_contract.py:72` | none | Validates envelope, descriptor reference, state, specification and provenance constraints. |
| proposer | `ai_proposer` | Generator AI factory | `backend/engineering/proposal_contract.py:112` | none | Lazy `ai` registry lookup. Returns `None` when disabled. |
| proposer | `GateRefusal` | Dataclass | `backend/engineering/family_gate.py:18` | none | Uses `unsupported` or `clarification_required`. |
| proposer | `family_gate` | Deterministic proposal gate | `backend/engineering/family_gate.py:24` | none | Refuses a different family and asks for clarification on an ambiguous match. |
| proposer | `NumberField` | Grounding dataclass | `backend/engineering/grounding.py:59` | none | Numeric dimension and unit. |
| proposer | `ChoiceField` | Grounding dataclass | `backend/engineering/grounding.py:65` | none | Choice values and synonyms. |
| proposer | `number_tokens` | Grounding entry point | `backend/engineering/grounding.py:158` | none | Returns parsed numeric prompt tokens. |
| proposer | `is_grounded_number` | Grounding entry point | `backend/engineering/grounding.py:163` | none | Matches prompt numbers with unit conversion. |
| proposer | `is_grounded_choice` | Grounding entry point | `backend/engineering/grounding.py:185` | none | Matches a choice against prompt synonyms. |
| proposer | `ungrounded_fields` | Grounding entry point | `backend/engineering/grounding.py:199` | none | Returns proposed fields without supporting prompt evidence. |
| contract-model | `ContractModel` | Pydantic base model | `backend/engineering/contracts.py:37` | none | Strict, frozen, extra fields forbidden, finite values, always revalidates instances. |
| contract-model | `CapabilityReference` | Pydantic model | `backend/engineering/contracts.py:42` | none | Validates ID and three dynamic version fields against registry. |
| contract-model | `Quantity` | Pydantic model | `backend/engineering/contracts.py:56` | none | Units `mm`, `cm`, `m`, `in`, `N`, `kN`, `Pa`, `MPa`, `GPa`. |
| contract-model | `ReviewedMassToForce` | Pydantic model | `backend/engineering/contracts.py:75` | none | Explicitly acknowledged mass-to-force conversion. |
| contract-model | `MaterialReference` | Pydantic model | `backend/engineering/contracts.py:99` | none | `source_kind`: `illustrative`, `user_supplied`, `documented`. |
| contract-model | `FixedRequirements` | Pydantic model | `backend/engineering/contracts.py:106` | none | Units `mm_N_MPa`, assumption `ideal_clamp_end_force_linear_elastic`. |
| contract-model | `FieldOrigin` | Pydantic model | `backend/engineering/contracts.py:237` | none | Origin `user_supplied`, `ai_proposed`, `converted`, `policy`. |
| contract-model | `Provenance` | Pydantic model | `backend/engineering/contracts.py:250` | none | `input_mode`: `form`, `ai`. |
| contract-model | `CandidateSpec` | Pydantic model | `backend/engineering/contracts.py:346` | `engineering_candidate_v1` | Beam specification bound to brief and candidate digests. |
| contract-model | `GeneratorCandidate` | Pydantic model | `backend/engineering/contracts.py:374` | `engineering_generator_candidate_v1` | Generator brief content and candidate digest. |
| contract-model | `CheckState` | String enum | `backend/engineering/contracts.py:400` | none | `passed`, `failed`, `not_evaluated`, `unavailable`. |
| contract-model | `Measurement` | Pydantic model | `backend/engineering/contracts.py:422` | none | Named numeric value with closed unit vocabulary. |
| contract-model | `ComparisonLimit` | Pydantic model | `backend/engineering/contracts.py:429` | none | `le`, `ge`, `eq`, `abs_le`. |
| contract-model | `CheckResult` | Pydantic model | `backend/engineering/contracts.py:444` | `engineering_check_v1` | Beam check, method identity, measurements, limits and evidence. |
| contract-model | `GeneratorCheckResult` | Pydantic model | `backend/engineering/contracts.py:483` | `engineering_generator_check_v1` | Check evidence includes capability ID and candidate association. |
| contract-model | `CantileverSpec` | Pydantic model | `backend/engineering/spec.py:11` | none | Family `rectangular_cantilever`, units `mm_N_MPa`, assumption `ideal_clamp_end_force_linear_elastic`. |
| contract-model | `PlateMaterialReference` | Pydantic model | `backend/engineering/plate_spec.py:77` | none | `source_kind`: `illustrative`, `user_supplied`, `documented`. |
| contract-model | `PlateRequirements` | Pydantic model | `backend/engineering/plate_spec.py:88` | none | Family `rectangular_plate`, units `mm_N_MPa`, edge condition `simply_supported` or `clamped`. |
| contract-model | `PlateSpec` | Pydantic model | `backend/engineering/plate_spec.py:139` | none | `PlateRequirements` plus candidate thickness. |
| contract-model | `RegenChamberSpec` | Pydantic model | `backend/engineering/regen_spec.py:22` | none | Family `regen_chamber`, units `mm`, assumption `geometry_only_no_gas_dynamics_thermal_or_structural_analysis`. |
| contract-model | `GliderCapabilitySpec` | Pydantic model | `backend/engineering/capabilities.py:266` | none | Mission requirements, calibration digest and `max_objective_value` selection rule. |
| contract-model | `SPEC_V2_ADAPTER` | Pydantic type adapter | `backend/dragsters/spec_v2.py:215` | `2` | Dragster descriptor target. Styles `wedge`, `teardrop`, `narrow_waist`. |
| contract-model | `DraftCreate` | Pydantic model | `backend/engineering/api_v1.py:35` | none | `form`, `ai`, `rules`. Defaults to beam and form. |
| contract-model | `DraftEdit` | Pydantic model | `backend/engineering/api_v1.py:44` | none | Expected revision and input. |
| contract-model | `ReviewInput` | Validation alias | `backend/engineering/api_v1.py:64` | none | Validates `ReviewAcknowledgement` through its JSON boundary. |
| contract-model | `EngineeringRequest` | Pydantic model | `backend/engineering/api.py:29` | none | Legacy beam spec and up to four candidate heights. |
| contract-model | `BrowserSpec` | Pydantic model | `backend/engineering/regen_web.py:40` | none | Legacy regen browser specification. |
| brief | `BriefContent` | Pydantic model | `backend/engineering/contracts.py:134` | `engineering_brief_v1` | Beam content. `fixed_design` or `bounded_thickness`, one to four reviewed heights. |
| brief | `GeneratorBriefContent` | Pydantic model | `backend/engineering/contracts.py:178` | `engineering_generator_brief_v1` | Normalized specification with all defaults and exact required checks. |
| brief | `AnyContent` | Discriminated union | `backend/engineering/contracts.py:234` | `engineering_brief_v1`, `engineering_generator_brief_v1` | Discriminates nested content by `schema_version`. |
| brief | `BriefDraft` | Pydantic model | `backend/engineering/contracts.py:265` | `engineering_draft_v1` | Revisioned, optional content, clarifications and provenance. |
| brief | `ReviewAcknowledgement` | Pydantic model | `backend/engineering/contracts.py:285` | none | Review binds draft revision, brief digest and acknowledged fields. |
| brief | `EngineeringBrief` | Pydantic model | `backend/engineering/contracts.py:301` | `engineering_brief_v1` | Finalized outer record wrapping beam or generator content. |
| brief | `InputIssue` | Pydantic model | `backend/engineering/briefs.py:31` | none | `clarification_required`, `unsupported`. |
| brief | `PreparedDraft` | Pydantic model | `backend/engineering/briefs.py:37` | none | Wraps `BriefDraft`, serialized input and issues. |
| brief | `BriefService` | Service class | `backend/engineering/briefs.py:140` | none | Validates drafts, tracks revisions and finalizes reviewed briefs. |
| brief | `PlateBrief` | Pydantic model | `backend/engineering/plate_spec.py:153` | none | `fixed_design` or `bounded_thickness`, with sorted unique candidate thicknesses. |
| job | `EngineeringRun` | Pydantic model | `backend/engineering/contracts.py:578` | `engineering_run_v1` | Durable job record with lifecycle, outcome, attempts and optional bundle. |
| job | `Lifecycle` | String enum | `backend/engineering/contracts.py:407` | none | `admitted`, `running`, `packaging`, `completed`, `unavailable`, `cancelled`. |
| job | `Outcome` | String enum | `backend/engineering/contracts.py:416` | none | `accepted`, `rejected`, `not_evaluated`. |
| job | `AttemptRecord` | Pydantic model | `backend/engineering/contracts.py:513` | none | Ordinal, candidate, outcome, checks and failures. |
| job | `ExpiredRun` | Pydantic model | `backend/engineering/jobs.py:21` | `engineering_expired_run_v1` | Compact record with `expired=True`. |
| job | `compact_run` | Record projection | `backend/engineering/jobs.py:34` | `engineering_expired_run_v1` | Reduces run record to expiry metadata. |
| job | `parse_run` | Record parser | `backend/engineering/jobs.py:39` | `engineering_expired_run_v1`, `engineering_run_v1` | Dispatches between compact and full job records. |
| job | `TERMINAL` | Lifecycle set | `backend/engineering/jobs.py:18` | none | `completed`, `cancelled`, `unavailable`. |
| job | `Admitted` | Named tuple | `backend/engineering/jobs.py:49` | none | Job, brief and slot owner. |
| job | `JobController` | Execution controller | `backend/engineering/jobs.py:59` | none | Admission, idempotency, cancellation, deadlines and terminal commit. |
| job | `JobSubmit` | Pydantic model | `backend/engineering/api_v1.py:51` | none | Brief ID and idempotency key. |
| job | `job_view` | HTTP record projection | `backend/engineering/api_v1.py:375` | none | Delivery states `pending`, `available`, `unavailable`, `expired`. Adds capability claim. |
| job | `SearchProgress` | Pydantic model | `backend/engineering/search.py:20` | none | Phases `starting`, `attempt`, `selected`, `rejected`, `unavailable`, `cancelled`. |
| job | `SearchResult` | Dataclass | `backend/engineering/search.py:28` | none | States `selected`, `rejected`, `unavailable`, `cancelled`. |
| job | `JobRunner` | Legacy regen runner | `backend/engineering/regen_web.py:64` | none | Statuses `queued`, `running`, `accepted`, `rejected`, `cancelled`, `unavailable`. |
| bundle | `ArtifactIdentity` | Pydantic model | `backend/engineering/contracts.py:434` | none | Allowed payload path, digest and byte count. |
| bundle | `BundleIdentity` | Pydantic model | `backend/engineering/contracts.py:550` | none | Payload, manifest and archive identities. Enforces stored byte constraints. |
| bundle | `ManifestPayload` | Pydantic model | `backend/engineering/bundles.py:54` | none | `ArtifactIdentity` with a closed MIME vocabulary. |
| bundle | `BundleManifest` | Pydantic model | `backend/engineering/bundles.py:59` | `engineering_manifest_v1` | Kind `accepted` or `diagnostic`. |
| bundle | `FORMATS` | Format registry | `backend/engineering/bundles.py:24` | none | Standard download format keys map to filenames and MIME types. |
| bundle | `GENERATOR_FORMATS` | Format registry | `backend/engineering/bundles.py:39` | none | Eight generator-specific download formats. |
| bundle | `ALL_FORMATS` | Format registry | `backend/engineering/bundles.py:49` | none | Merges standard and generator download formats. |
| bundle | `Download` | Lease dataclass | `backend/engineering/bundles.py:112` | none | Owns a validated download and releases its lease on close. |
| bundle | `BundlePublisher` | Publication class | `backend/engineering/bundles.py:145` | none | Writes, validates, leases and expires bundles. |
| bundle | `BundlePublisher._generator_reports` | Report producer | `backend/engineering/bundles.py:226` | `engineering_report_v1` | Generator report uses descriptor ID as scope. |
| bundle | `BundlePublisher._reports` | Report producer | `backend/engineering/bundles.py:255` | `engineering_report_v1` | Beam report uses geometry scope and embeds native report evidence. |
| bundle | `BundlePublisher._publish` | Manifest and publication writer | `backend/engineering/bundles.py:284` | `engineering_manifest_v1` | Writes manifest, archive and publication metadata. |
| store | `BriefRepository` | Protocol | `backend/engineering/briefs.py:47` | none | Draft CRUD, finalization, revision checks and proposal claims. |
| store | `MemoryBriefRepository` | Repository class | `backend/engineering/briefs.py:64` | none | In-memory implementation of the brief repository. |
| store | `ProposalLedger` | Protocol | `backend/engineering/proposer.py:283` | none | `claim`, `save`, `read` for proposal audits. |
| store | `MemoryProposalLedger` | Ledger class | `backend/engineering/proposer.py:289` | none | In-memory proposal audit ledger. |
| store | `SQLiteStore` | Store class | `backend/engineering/store.py:24` | `1` | SQLite `user_version` accepts `0` or `1`, sets `1`. Single owner with WAL. |
| store | `BundlePublisher.__init__` | Publication table initializer | `backend/engineering/bundles.py:146` | none | Creates `publications` table with `job`, `record`, `expires`. |
| store | `PreparationQuota` | Quota class | `backend/engineering/store.py:116` | none | Enforces draft and brief quotas and unfinalized draft expiry. |
| store | `SQLiteBriefRepository` | Repository class | `backend/engineering/store.py:193` | none | Persists prepared drafts, reviewed briefs and proposal ownership. |
| store | `SQLiteProposalLedger` | Ledger class | `backend/engineering/store.py:288` | `engineering_proposal_v1` | Persists `ProposalAudit` and generator call spend. |
| store | `GeneratorSpendBudget` | Budget wrapper | `backend/engineering/store.py:348` | none | Records reserved generator spend for restart continuity. |
| policy/limit | `NumericalPolicy` | Pydantic model | `backend/engineering/capabilities.py:44` | `beam_numerical_v1` | Beam geometry and FEA thresholds. |
| policy/limit | `ResourcePolicy` | Pydantic model | `backend/engineering/capabilities.py:71` | `engineering_resources_v1` | Job, subprocess, AI, storage and retention budgets. |
| policy/limit | `PolicyReference` | Pydantic model | `backend/engineering/capabilities.py:153` | none | Pinned value with source and optional importable owner. |
| policy/limit | `GeneratorDescriptor.expected_limits` | Limit construction | `backend/engineering/capabilities.py:245` | none | Builds typed closed limits from descriptor pins and reviewed spec. |
| policy/limit | `_LIMIT_TABLE` | Limit dispatch registry | `backend/engineering/capabilities.py:420` | none | One limits function per generator capability. |
| policy/limit | `refuse_dragster` | Descriptor refusal hook | `backend/engineering/capabilities.py:424` | `1`, `2` | Refuses numeric schema 1 and non-null stock. Checked specification requires 2. |
| policy/limit | `refuse_regen` | Descriptor refusal hook | `backend/engineering/capabilities.py:431` | none | Refuses voxel size below `REGEN_MIN_VOXEL_SIZE_MM`. |
| policy/limit | `refuse_glider` | Descriptor refusal hook | `backend/engineering/capabilities.py:437` | none | Refuses thermal/ridge environment and `all` or unsupported discovery families. |
| policy/limit | `GLIDER_SUPPORTED_FAMILIES` | Policy constant | `backend/engineering/capabilities.py:149` | none | `conventional`, `t_tail`, `v_tail`. |
| policy/limit | `REGEN_MIN_VOXEL_SIZE_MM` | Policy constant | `backend/engineering/capabilities.py:150` | none | Checked capability floor of 0.4 mm. |
| policy/limit | `validate_beam_request` | Preparation validator | `backend/engineering/capabilities.py:688` | none | Validates scope and returns normalized ascending approved options. |
| policy/limit | `PLATE_NUMERICAL_V1` | Policy mapping | `backend/engineering/plate_spec.py:12` | `plate_numerical_v1` | Plate requirements, gates, tolerances and FE settings. |
| policy/limit | `FAMILY_VERSION` | Family version constant | `backend/engineering/plate_spec.py:66` | `1.0.0` | Plate family version used in native report. |
| policy/limit | `SCOPE` | Native report scope constant | `backend/engineering/plate_verify.py:22` | `rectangular_plate_v1` | Plate verification report scope. |
| policy/limit | `evaluate_static_guards` | Plate policy entry point | `backend/engineering/plate_spec.py:194` | none | Evaluates pre-execution plate guards. |
| policy/limit | `first_failed_guard` | Plate policy entry point | `backend/engineering/plate_spec.py:211` | none | Returns first failed guard ID or `None`. |
| policy/limit | `Stage` | Pydantic model | `backend/engineering/policy.py:26` | none | `CheckState` and optional reason. |
| policy/limit | `Standards` | Pydantic model | `backend/engineering/policy.py:31` | none | State `not_evaluated`. |
| policy/limit | `BeamSolution` | Pydantic model | `backend/engineering/policy.py:36` | none | Beam solution and equilibrium measurements. |
| policy/limit | `PhysicsEvidence` | Pydantic model | `backend/engineering/policy.py:49` | `euler_bernoulli_beam_fem_v1` | Optional physics method, mesh and benchmark evidence. |
| policy/limit | `WorkerCheck` | Pydantic model | `backend/engineering/policy.py:64` | none | Worker state, measurements, limits and reason. |
| policy/limit | `NativeGeometry` | Pydantic model | `backend/engineering/policy.py:71` | `structural_solid_set_v1` | Native geometry scope literal. |
| policy/limit | `NumericalEvidence` | Pydantic model | `backend/engineering/policy.py:83` | none | Reports numerical tolerances and guards. |
| policy/limit | `NativeReport` | Pydantic model | `backend/engineering/policy.py:90` | `rectangular_cantilever_v1` | Beam worker scope. Runtime versions and implementation digests are dynamic. |
| policy/limit | `PolicyDecision` | Pydantic model | `backend/engineering/policy.py:112` | none | Availability, outcome, typed checks and reasons. |
| policy/limit | `report_digest` | Evidence hashing | `backend/engineering/policy.py:22` | none | Report identity digest. |
| policy/limit | `assess` | Policy entry point | `backend/engineering/policy.py:286` | none | Assesses beam evidence and dispatches generator evidence to `assess_generator`. |
| policy/limit | `assess_generator` | Policy entry point | `backend/engineering/generator_policy.py:122` | none | Recomputes pinned comparisons, reparses captured measurements and checks artifact identities. |
| policy/limit | `AiCallBudget` | Budget class | `backend/engineering/limits.py:8` | none | Rolling spend and in-flight limits. Each provider call, including repair, consumes a unit. |
| policy/limit | `run_in_thread_owned` | Ownership helper | `backend/engineering/limits.py:62` | none | Handles an orphaned result when the awaiting coroutine is cancelled. |
| policy/limit | `AdmissionBusy` | Admission exception | `backend/engineering/admission.py:8` | none | Default retry after five seconds. |
| policy/limit | `ClientQuotaExceeded` | Admission exception | `backend/engineering/admission.py:12` | none | Per-client quota or turn refusal maps to HTTP 429. |
| policy/limit | `ClientFairness` | Admission policy class | `backend/engineering/admission.py:19` | none | Rolling hourly quota and recent-refusal turn rule. |
| policy/limit | `NativeSlot` | Admission class | `backend/engineering/admission.py:81` | none | Single owner with immediate refusal when busy. |
| policy/limit | `native_slot` | Shared admission instance | `backend/engineering/admission.py:98` | none | Shared by checked jobs and legacy beam endpoint. |
| policy/limit | `limit_body` | HTTP body limit | `backend/engineering/api_v1.py:67` | none | `MIRUM_ENGINEERING_MAX_BODY_BYTES` applies before model parsing. |
| route | `RouteDecision` | Routing dataclass | `backend/engineering/router.py:21` | none | Capability ID, reason and assurance. |
| route | `route` | Prompt router | `backend/engineering/router.py:102` | `capability_router_v1` | Five IDs. Single match `checked_design`. Zero or multiple matches `unverified_geometry`. |
| route | `GET /engineering/v1/capabilities` | HTTP handler | `backend/engineering/api_v1.py:210` | `v1` | `capabilities`. Descriptor metadata, availability and AI flag. |
| route | `POST /engineering/v1/drafts` | HTTP handler | `backend/engineering/api_v1.py:231` | `v1` | `create_draft`. Form, flagged AI or registered rules. Success 201. |
| route | `GET /engineering/v1/drafts/{draft_id}` | HTTP handler | `backend/engineering/api_v1.py:339` | `v1` | `read_draft`. Prepared draft with digests. |
| route | `PATCH /engineering/v1/drafts/{draft_id}` | HTTP handler | `backend/engineering/api_v1.py:344` | `v1` | `edit_draft`. Revision-checked input update for the existing capability. |
| route | `POST /engineering/v1/briefs` | HTTP handler | `backend/engineering/api_v1.py:363` | `v1` | `finalize_brief`. Validated review finalizes a brief. Success 201. |
| route | `GET /engineering/v1/briefs/{brief_id}` | HTTP handler | `backend/engineering/api_v1.py:372` | `v1` | `read_brief`. Returns finalized brief. |
| route | `POST /engineering/v1/jobs` | HTTP handler | `backend/engineering/api_v1.py:407` | `v1` | `submit_job`. Client-scoped idempotency, admit then start. Success 202. |
| route | `GET /engineering/v1/jobs/{job_id}` | HTTP handler | `backend/engineering/api_v1.py:420` | `v1` | `read_job`. Full or expired record and delivery status. |
| route | `POST /engineering/v1/jobs/{job_id}/cancel` | HTTP handler | `backend/engineering/api_v1.py:426` | `v1` | `cancel_job`. Requests cancellation and returns job view. |
| route | `GET /engineering/v1/jobs/{job_id}/artifacts/{format}` | HTTP handler | `backend/engineering/api_v1.py:432` | `v1` | `download`. Streams leased artifact with `X-Content-SHA256`. |
| route | `POST /engineering/generate` | Legacy HTTP handler | `backend/engineering/api.py:48` | none | `engineering_generate`. Typed beam request and base64 STEP/GLB on acceptance. |
| route | `GET /engineering/regen/config` | Legacy HTTP handler | `backend/engineering/regen_web.py:314` | none | `config`. Runner readiness, examples, notice and `BrowserSpec` JSON schema. |
| route | `POST /engineering/regen/jobs` | Legacy HTTP handler | `backend/engineering/regen_web.py:320` | none | `generate`. Validates `BrowserSpec` and submits to legacy runner. Success 202. |
| route | `GET /engineering/regen/jobs/{job_id}` | Legacy HTTP handler | `backend/engineering/regen_web.py:338` | none | `status`. Returns legacy job and artifact URLs. |
| route | `GET /engineering/regen/jobs/{job_id}/artifacts/{name}` | Legacy HTTP handler | `backend/engineering/regen_web.py:343` | none | `artifact`. Serves allowlisted accepted results. |

## Divergences and how Phase 3 resolves them

F01 is docs-only, so no existing code is changed here. Each divergence found at 456e622 gets a Phase 3 rule so that new code does not design on top of it; any fix to existing B/E code is a separate, owner-approved change.

| # | Divergence (worker finding, checked by the manager at the cited lines) | Evidence | Phase 3 rule and owner |
|---|---|---|---|
| V1 | The prompt router emits assurance `checked_design` for every single match and the main route event passes it through, while the v1 job API uses the descriptor claim (`geometry_checks_only` for dragster and regen, `screening_only` for glider). | `backend/engineering/router.py:114`, `backend/main.py:752`, `backend/engineering/api_v1.py:380`, `backend/engineering/capabilities.py:503`, `:544`, `:586` | F05/F08 take the claim from the capability descriptor (or the v1 job record), never from the router assurance. Reported to the lead as a possible labelling issue in existing routing. |
| V2 | Generator discovery reports `available` from module presence; job execution needs an adapter registered in the runtime map (`create_runtime` adds plate, the caller supplies the others). | `backend/engineering/api_v1.py:140`, `:164`, `backend/engineering/jobs.py:228` | F03 `probe()` is the readiness signal; metadata or importability alone is not readiness (FRAMEWORK.md KernelAdapter rule). |
| V3 | Legacy beam evaluates candidate heights in caller order and stops at first acceptance; the reviewed v1 beam sorts and deduplicates heights before execution. | `backend/engineering/service.py:77`, `:94`, `backend/engineering/contracts.py:144` | F05 lowers MEL beam programs only to the reviewed v1 path (P07 reference case); the legacy order is not a Phase 3 target. |
| V4 | `RegenChamberSpec` accepts voxel sizes down to 0.1 mm; the checked descriptor refuses sizes below 0.4 mm. | `backend/engineering/regen_spec.py:39`, `backend/engineering/capabilities.py:150`, `:431` | F03 qualifies the PicoGK adapter on the descriptor envelope (refusal below 0.4 mm); the wider spec bound is not a qualified envelope. |
| V5 | The proposal contract documents one origin per reviewable field and references carrying the prompt SHA-256, prompt version and `unverified proposal`; `conform` checks origin uniqueness and the marker only. | `backend/engineering/proposal_contract.py:19`, `:99` | F09 AI proposals for MEL must check completeness, SHA-256 and version explicitly rather than rely on `conform`. Reported to the lead (E05 area). |
| V6 | The beam descriptor still describes V1 exports as contracts for later consumers although the v1 artifact route exists. | `backend/engineering/capabilities.py:127`, `backend/engineering/api_v1.py:432` | Descriptive text only; no Phase 3 dependency. F08 documents the actual artifact route. |
