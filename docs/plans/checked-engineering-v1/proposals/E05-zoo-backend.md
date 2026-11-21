# E05 step 3: Zoo as an optional CAD backend, and a self-hosted Zoo/Leo-like backend

Status: read-only investigation, no integration. Owner answer Q6 (docs/plans/2026-11-20-wave-e.md): Zoo is in
scope only as access/licensing/cost/export-fidelity research; no Zoo key and no live call without the owner's yes.
All external facts below come from public pages and the public OpenAPI spec read on 2026-11-20 and cite the URL.
Claims first marked "unverified" were re-checked on 2026-11-20 (second pass) and are now tagged VERIFIED,
CORRECTED, or still "unverified" with a reason. No Zoo or Leo account, key or API call was used.

## 1. Decision summary

- Documented need now: none. E05 step 3 says "only for a documented need"; no capability, task or failing case in
  the repo currently requires a Zoo-generated artifact.
- Recommendation: do not integrate Zoo in Wave E. Keep this document as the intake checklist.
- Evidence that would justify an integration: (a) a concrete request family no registered native capability can
  express and the owner wants it; (b) a written owner yes to an account and key; (c) the owner reads the Zoo terms
  (section 2.3, in particular the benchmarking/competing-product clause) or gets written permission; (d) a fidelity
  test set (section 4) that passes.
- Long-term own backend (Q6): feasible as an adapter behind the existing capability contract; the interface is
  specified in section 5. It should start from open components, not by cloning Zoo's hosted engine.
- Premise change found 2026-11-20: the one-shot REST generate endpoint is gone from the live spec. Generation is
  now a WebSocket agent session that returns KCL only; STEP needs a second, billed step (section 2.4).

## 2. Zoo (zoo.dev, formerly KittyCAD)

### 2.1 Access model
- VERIFIED: bearer-token API at https://api.zoo.dev. "Authentication to the API is performed via Bearer Token Auth.
  Provide your API key as the token value." (https://zoo.dev/docs/developer-tools/api/authentication). Header is
  `Authorization: Bearer $ZOO_API_TOKEN`. Base URL confirmed by `DEFAULT_BASE_URL = "https://api.zoo.dev"` in
  https://raw.githubusercontent.com/KittyCAD/kittycad.py/main/kittycad/client.py. Tokens are created at
  https://zoo.dev/account/developer or `POST /user/api-tokens`.
- VERIFIED: `GET /user/text-to-cad/{id}` requires authentication and "The user must be the owner of the text-to-CAD
  model." (OpenAPI spec at https://api.zoo.dev/, which serves the spec JSON per
  https://zoo.dev/docs/developer-tools/api; cross-checked against
  https://raw.githubusercontent.com/KittyCAD/kittycad.rs/main/spec.json).
- CORRECTED (was: "that page lists no response fields, status values or formats"): the spec schema
  `TextToCadResponse` has `id, user_id, conversation_id, prompt, model (cad|kcl|kcl_iteration), model_version,
  output_format, outputs (map path -> base64), code, kcl_version, status, error, feedback, created_at, updated_at,
  started_at, completed_at`. Source: https://api.zoo.dev/.
- CORRECTED (was: `POST /ai/text-to-cad/{output_format}` with unverified body, path and enum): that endpoint is no
  longer in the live spec (no path starts with `/ai`; same in the kittycad.rs spec). That is why
  https://zoo.dev/docs/developer-tools/api/ml/generate-a-cad-model-from-text returns 404. Remaining text-to-CAD REST
  routes are read/feedback only: `GET /user/text-to-cad`, `GET /user/text-to-cad/{id}`,
  `POST /user/text-to-cad/{id}` (feedback). Generation goes through the Zookeeper agent WebSocket, listed in the spec
  as `GET /ws/ml/copilot` (the billing page also lists `/ws/ml/copilot` and `/ws/ml/reasoning/{id}`). The official
  Python tutorial (https://zoo.dev/docs/developer-tools/tutorials/beginner-onboarding-py) connects to
  `wss://api.zoo.dev/ws/ml/zookeeper`; that path is not in the spec. Both path names are recorded; treat the WS path
  as unstable and re-check before coding.
- CORRECTED (was: asynchronous with status values "completed"/"failed"): the stored REST record has `ApiCallStatus`
  exactly queued, uploaded, in_progress, completed, failed (spec). But new work is streamed over the WebSocket, not
  polled. Server messages: `conversation_id`, `delta`, `tool_output`, `error`, `access_denied`, `end_of_stream`
  (with started_at, completed_at, id, conversation_id, whole_response). Zoo requires a heartbeat: `{ type: "ping" }`
  about every 5 s and a `{ pong: {} }` reply check. The prompt id is `end_of_stream.id`; the stored record can be
  fetched later with `GET /user/text-to-cad/{id}`. Sources: https://api.zoo.dev/ and the tutorial above.
- Unverified: rate limits and concurrency caps. No numeric limit appears in the spec, the pricing pages, the billing
  page or the auth page (https://api.zoo.dev/, https://zoo.dev/api-pricing,
  https://zoo.dev/docs/developer-tools/api/billing). Only gates found: billing denial codes on the WS
  (`missing_payment_method, payment_method_failed, billing_threshold_reached, pay_as_you_go_disabled,
  upgrade_downgrade_abuse, admin`) and plan allowances (section 2.2). Ask Zoo if concurrency matters.
- VERIFIED: Zoo exposes an MCP server; "Zoo MCP has no separate fee, but hosted Zoo operations count as external API
  usage." (https://zoo.dev/api-pricing).

### 2.2 Cost
- CORRECTED (was: cost per call unverified, plan prices "Loading..."): cost is usage-metered per second, not per
  call. https://zoo.dev/api-pricing: "Simple API pricing at $0.0083 per second"; "No Charge for Failed Calls"; "Only
  successful billable API operations and sessions consume usage". https://zoo.dev/blog/per-second-billing: $0.50 per
  minute, billed per second ($1/120 per second), rounded half-up to the cent per call; calls under 10 s billed from
  2025-10-14.
- Billable endpoints (https://zoo.dev/docs/developer-tools/api/billing, 12 usage-based): `GET /ws/ml/copilot`,
  `GET /ws/modeling/commands`, `POST /file/*` conversion/mass/volume and similar, `POST /ml/kcl/completions`,
  `POST /execution/inputs`. No usage charge: `GET /user/text-to-cad*`, `GET /ws/ml/reasoning/{id}`. A turn costs
  elapsed seconds x $0.0083, plus engine time for the STEP export (`/ws/modeling/commands`).
- Latency per turn: unverified. The only datum is a Jan 2024 blog example ("took just under a minute", about $0.50)
  for the old endpoint (https://zoo.dev/blog/turning-on-billing-for-text-to-cad). Predates Zookeeper. Measure.
- VERIFIED (plan table, read 2026-11-20 at https://zoo.dev/zoo-pricing, rendered via Firecrawl):

  | Plan | Price | Allowance | Training |
  |---|---|---|---|
  | Free | $0 | 20 min/mo ($10 credit) | Zoo can train on your data; plan card says no opt-out, compare table says "Yes, Opt-out Available" (the two conflict) |
  | Plus | $20/mo | about 400 min ($200 credit) | manual opt-out |
  | Pro | $99/mo | 2,500 min ($1,245 credit) | manual opt-out |
  | Team | $399/mo/user | 10,000 min ($4,980 credit) | "Data excluded from training by default"; service accounts / org-wide API keys |
  | Enterprise | custom | custom | custom ML models, SAML, dedicated manager |

  Allowance is shared across Zookeeper, billable API calls and MCP, and renews on the monthly anniversary (UTC).
  Pay-as-you-go is opt-in ("$0.0083 per second"); otherwise "usage pauses when the allowance runs out". Design
  Studio CAD modelling stays unlimited (https://zoo.dev/pricing, read 2026-11-20). The $0.0083/s and $10-per-20-min
  figures are Zoo's rounded constants.

### 2.3 Terms and licensing
- Source: https://zoo.dev/terms-and-conditions (read 2026-11-20 via Firecrawl direct quote; section numbers were not
  shown in the fetched copy, so the section numbers below are unconfirmed). No legal-grade read was done.
  - VERIFIED, Outputs: "Subject to Section 7(b), you own and retain all right, title and interest (if any) in the
    Outputs." The "(if any)" qualifier means ownership of AI-generated geometry is not guaranteed.
  - VERIFIED, Training: Zoo may use Customer Materials "to train and improve Zoo's artificial intelligence and
    machine learning models"; "If you do not want Zoo to use your Customer Materials to train ... and only if your
    subscription tier allows, you may opt out". Tier detail from https://zoo.dev/zoo-pricing: Free trains on your
    inputs by default; Plus/Pro opt out manually; Team excluded by default. Prompts or reference designs sent to Zoo
    could train its models. Mirum's brief/evidence payloads must not be sent without owner review of this.
  - VERIFIED (wording), Competing products: restriction (viii) bars use of the Services, Documentation or other Zoo
    Confidential Information "for benchmarking or competitive analysis with respect to competitive or related
    products or services, or to develop, commercialize, license or sell any product, service or technology that
    could, directly or indirectly, compete with the Services." Also (iii) no reverse engineering, (iv) no resale.
    This doc earlier cited Section 3(b); that number is unconfirmed (the fetched copy has no section labels).
  - Aquarium community terms differ (https://zoo.dev/aquarium-terms-of-use, via search summary) and are not the
    API terms. Not re-read.
- Risk for Mirum: Mirum is a text-to-CAD product, and the owner plans an own Zoo/Leo-like backend (Q6).
  Benchmarking Zoo against Mirum (for example in BenchCAD-style evals), using Zoo as a baseline or teacher model, or
  using Zoo to help build that own backend may breach restriction (viii). Any integration or comparison needs the
  owner to read the terms, or obtain written permission from Zoo, first. Until then, no Zoo call of any kind,
  including "just a quick comparison". Legal review is still needed.
- Open source:
  - VERIFIED: modeling-app (Zoo Design Studio) is MIT, "Copyright (c) 2023 The Zoo Authors"
    (https://raw.githubusercontent.com/KittyCAD/modeling-app/main/LICENSE; GitHub API licence key `mit`).
  - VERIFIED (resolved, was "not confirmed"): kcl-lib is MIT (`name = "kcl-lib"`, `version = "0.2.197"`,
    `license = "MIT"` in https://raw.githubusercontent.com/KittyCAD/modeling-app/main/rust/kcl-lib/Cargo.toml).
  - VERIFIED: the Zoo CLI (https://github.com/KittyCAD/cli) and kittycad.py (https://github.com/KittyCAD/kittycad.py)
    are MIT (GitHub API licence key).
  - Unverified: https://zoo-industries.pages.dev/open-source (search snippet claimed MIT/Apache). Not re-checked;
    domain looks unofficial. Not needed now that the official repos are confirmed MIT.
  - VERIFIED, geometry engine is proprietary and hosted: "leverages Zoo's proprietary geometry engine"
    (https://zoo.dev/press/zoo-introduces-zookeeper); "leveraging our proprietary geometry engine"
    (https://zoo.dev/enterprise). The Engine API is remote only. No source release and no on-prem offering found
    (the Enterprise plan advertises custom ML models and a customized security review, no on-prem claim:
    https://zoo.dev/zoo-pricing). Terms (iii) forbid reverse engineering. The open kcl-lib calls the hosted engine
    over WebSocket.

### 2.4 Text-to-CAD API shape and outputs
- CORRECTED, inputs: the agent WebSocket takes a client message
  `{"type":"user","content":"<prompt>","project_name":"...","current_files":{"main.kcl":""}}`. `user` fields:
  content (required), type (required), active_file, additional_files, correlation_id, current_files,
  engine_api_call_id, forced_tools, mode, model, project_name, project_snapshot, reasoning_effort, source_ranges
  (https://api.zoo.dev/). Tools: edit_kcl_code, text_to_cad, mechanical_knowledge_base, web_search.
- CORRECTED, outputs: KCL only. The spec description for `GET /ws/ml/copilot`: "ONLY KCL IS RETURNED. It's a common
  misunderstanding that Zookeeper returns models in formats such as STEP and STL directly. This is incorrect. All
  outputs are in KCL, which can then be fed into the Engine API (specifically the `exec_kcl_project` command) or the
  Zoo CLI (`zoo kcl snapshot ...`)." (https://api.zoo.dev/). STEP therefore needs a second step: execute the KCL on
  Zoo's hosted engine and export, via the modeling-commands WebSocket (`GET /ws/modeling/commands`), the `zoo-kcl`
  Python package, or `zoo kcl export -t step`. That step is also billed.
- CORRECTED, formats and CLI: the format enum (`FileExportFormat`) is fbx, glb, gltf, obj, ply, step, stl, rhino
  (spec). The CLI no longer has `zoo ml text-to-cad` (its docs page 404s, removed); `zoo ml` has only `zoo ml kcl`
  -> `zoo ml kcl copilot` (https://zoo.dev/docs/developer-tools/cli/manual/zoo_ml,
  https://zoo.dev/docs/developer-tools/cli/manual/zoo_ml_kcl). Export is `zoo kcl export` with
  `-t/--output-format` fbx|glb|gltf|obj|ply|step|stl and `--deterministic` ("any date or time information will be
  replaced with a fixed value") (https://zoo.dev/docs/developer-tools/cli/manual/zoo_kcl_export). `rhino` is in the
  API enum but not the CLI list. CLI `--host` accepts a custom API host; that is a dev option, not a published
  self-host. A third-party test lists FBX, GLB, glTF, OBJ, PLY, STL, STEP, KCL
  (https://www.wevolver.com/article/we-tested-7-text-to-cad-tools-are-they-actually-useful-for-engineers, search
  snippet only, not re-read; superseded by the spec).
- VERIFIED: a file-conversion API exists: `POST /file/conversion/{src_format}/{output_format}` and
  `POST /file/conversion`, usage-based (https://api.zoo.dev/, billing page above).
- Unverified: "B-rep surfaces, not point clouds" for text-to-CAD output itself. Not re-sourced; the engine's STEP
  export defaults to AP242 (`StepSchema` default `ap242`, spec `OutputFormat3d`), which supports B-rep for the
  export step only.
- CORRECTED in part, units/determinism/versioning (was: no unit convention, tolerance, determinism or version pin):
  - Units: STEP export defaults to `units: "m"` (meters); STL defaults to mm. Coordinates default to forward = -Y,
    up = +Z ("KittyCAD co-ordinate system"). KCL has its own default units; set them explicitly (spec).
  - Versioning: `model_version` (string) and `kcl_version` (nullable) are recorded on each `TextToCadResponse`, so
    they can be logged. No request-side field pins a model version, and the spec has no seed field (0 matches for
    "seed"). The `user` message can set `mode` (ids from a `list_modes` message), `model` (enum gpt5_nano,
    gpt5_mini, gpt5_codex, gpt5, o3_mini, azure_*) and `reasoning_effort` (low, medium, high, xhigh).
  - Determinism: LLM output is not deterministic. Only KCL-to-geometry export has a `--deterministic` timestamp
    option.
  - Unverified: tolerance and tessellation settings. Not documented in the spec or docs read.
- KCL: KCL source is the editable representation. A KCL output is the only form where "editable" has a defensible
  meaning, and only if it rebuilds to the same geometry.

### 2.5 How to integrate (if the owner approves a key)
Nothing here is permitted yet. No key, account, token creation or live call without the owner's written yes, and
section 2.3 "Risk for Mirum" must be cleared first. State as of 2026-11-20; the WS path may change.

1. Auth. A human creates an API token at https://zoo.dev/account/developer. REST: `Authorization: Bearer <token>`
   to https://api.zoo.dev, HTTPS only. WebSocket: the spec says send a first message
   `{"type":"headers","headers":{"Authorization":"Bearer <token>"}}`; the tutorial sends the header in the handshake.
   Use the handshake header, fall back to the first-message form. Token in env var `ZOO_API_TOKEN`; never in the
   sandbox that runs generated CadQuery. Org keys need the Team plan or higher; for many users Zoo says each user
   supplies their own key.
2. Request sequence (no single REST call exists):
   1. Open `wss://api.zoo.dev/ws/ml/copilot` (spec; tutorial uses `/ws/ml/zookeeper`). Optional query
      `replay=true&conversation_id=<uuid>` to continue; iteration = another `user` message with the latest files in
      `current_files`.
   2. Authenticate; send ping about every 5 s and require pong.
   3. Send the `user` message (section 2.4). Optional `forced_tools:["text_to_cad"]`, `mode`, `reasoning_effort`.
   4. Read frames until `end_of_stream`. Take `tool_output.result.outputs["main.kcl"]` (type `text_to_cad` or
      `edit_kcl_code`; plain-text KCL map, not base64). Handle `error` and `access_denied` (billing). Keep
      `conversation_id` and `end_of_stream.id`.
   5. Optional: `GET /user/text-to-cad/{id}` for the stored record (`status`, `model_version`, `kcl_version`, `code`);
      `POST /user/text-to-cad/{id}?feedback=...`.
3. KCL -> STEP export. Execute the KCL on Zoo's engine: `GET /ws/modeling/commands` (`export` command,
   `OutputFormat3d` with `type:"step"`, `units`, `coords`), or `zoo-kcl` (`kcl.execute_code_and_export(main_kcl,
   kcl.FileExportFormat.Step)`; zoo-kcl 0.3.197, https://pypi.org/pypi/zoo-kcl/json), or CLI
   `zoo kcl export -t step --deterministic`. Official REST SDK is `kittycad` 1.13.0 (MIT, Python >=3.11,
   https://pypi.org/pypi/kittycad/json); the tutorial uses raw `websockets` for the prompt.
4. Units. STEP default is meters. Always pass explicit `units` on export and convert to mm in the adapter
   (x 1000 if a meters file is received), then check bbox against the spec's units before accepting.
5. Where it plugs into Mirum. As an `adapter` behind a new descriptor (section 4), run as an async job per section 5
   (`POST /jobs`, `GET /jobs/{id}`): the WS session and the export are the job body, with a timeout and
   cancellation that closes the socket. Store KCL as source of truth with `model_version`, `kcl_version`,
   conversation id and prompt hash. Re-import the STEP in Mirum and run the mandatory checks (watertight, bbox,
   volume, units); do not trust Zoo status. Cache by prompt hash; never regenerate silently, since output cannot be
   seeded or version-pinned.
6. Cost guard. Per-second billing on both the agent and engine WebSockets; failed calls free. A generate+export turn
   of 1 to 3 minutes is roughly $0.50 to $1.50 pay-as-you-go (estimate; measure via `GET /user/api-calls`). Set a
   monthly cap before any use (section 6, question 4).

## 3. Leo (getleo.ai)
- Product: "engineering design co-pilot" on a "Large Mechanical Model"; accepts text, sketches, images, CAD files;
  knowledge Q&A over PLM data, search over 120M+ vendor parts, calculations with a code interpreter; Onshape
  integration through a PTC partnership (https://www.getleo.ai/llms.txt, read 2026-11-20). Coverage of assembly
  generation: https://www.engineering.com/leo-ai-can-now-generate-full-cad-assemblies/ (search listing only).
- Public API/SDK/developer docs: none found (llms.txt does not mention one). Pricing: not listed. Conclusion: Leo
  is a product, not an integrable backend. No public API; private or enterprise access is unverified. Not
  re-checked in the 2026-11-20 second pass (Zoo only).

## 4. Fit with Mirum's contract

Mirum contract (repo): `GeneratorDescriptor` in backend/engineering/capabilities.py requires id, version,
`spec_schema`, `base_checks`, `conditional_checks`, `unsupported_features`, `numerical` and `runtime_limits` policy
references, `primary_artifact`, `payloads`, `adapter`, `claim` (`geometry_checks_only` | `screening_only`),
`engineering_certified: Literal[False]`, `standards: not_evaluated`, `limitations`. E05 proposals conform through
backend/engineering/proposal_contract.py (`conform`, `ai_proposer`).

Rules for any Zoo-backed capability:
- It gets its own descriptor (for example id `zoo_text_to_cad`), never an extension of an existing family's claim.
  Initial claim: `geometry_checks_only`. `engineering_certified` stays False by type.
- `spec_schema` is the typed request Mirum sends (prompt hash, format, units), not free text passed through.
- Mandatory checks run on the imported artifact in Mirum, not on Zoo's say-so: watertight/manifold, bbox and
  volume within declared tolerance of the spec, units, nonzero volume, size/triangle limits. `unsupported_features`
  lists what Zoo output cannot be checked for.
- Fidelity is declared and tested: round-trip tests (STEP -> Mirum import -> volume/bbox/area vs Zoo's own mesh
  export) with explicit tolerances registered as policy, not set silently in the adapter.

Mapping to the E05 accepted end result (tasks/E05-broader-design-generation.md):

| Bullet | What Zoo integration would need |
|---|---|
| Every proposal names a supported capability/version and valid schema | Zoo capability registered with a version; proposer output goes through `conform`; Zoo prompts are derived from the validated spec. |
| Changing backend does not broaden acceptance scope | Cross-backend claim test: the same spec via native and Zoo adapter yields the same or narrower claim and the same mandatory check set; Zoo cannot make a check pass that the native path would refuse. |
| Conversions declare tested fidelity, no editable-CAD claim without evidence | Conversion record: source format, target format, tolerance, measured deviation. "Editable" only for KCL that rebuilds to the same geometry in tests; STEP from Zoo is "B-rep import", not editable feature history. |
| Capability-specific mandatory checks still control every result | Checks live in the descriptor and run on the imported artifact; Zoo job success is not an acceptance signal. |

Additional blockers: external data egress (prompts leave the machine; Free plan trains on inputs, section 2.3),
outputs vary across runs and model versions with no seed or version pin (section 2.4; `model_version` is only
logged), the benchmarking/competing-product clause (section 2.3), third-party outage, cost budget, no Zoo key
permitted (Q6).

## 5. Self-hosted Zoo/Leo-like backend: interface Mirum would need

Principle: Mirum treats it as an untrusted artifact producer; acceptance stays with Mirum checks.

1. Request: typed spec in (versioned `spec_schema`, explicit units, requested output formats, seed, limits), plus
   the optional reviewed brief. Not a free prompt alone.
2. Response: primary artifact + generated source (CadQuery script or KCL) + manifest. The source is the editable
   representation; artifact and source must agree (rebuild test, item 5).
3. Async job API: `POST /jobs` (returns a job id; `Idempotency-Key` header, same key + same body returns the same
   job), `GET /jobs/{id}` (queued|running|succeeded|failed|cancelled, structured error code), `DELETE /jobs/{id}`
   cancel, artifact fetch by id with size and sha256. Timeouts and retry rules declared. Zoo's generation is
   session-based over a WebSocket, with stored records pollable by REST (queued|uploaded|in_progress|completed|
   failed; section 2.1), so a Zoo adapter wraps the session in one job. Align with the repo's
   docs/plans/checked-engineering-v1/JOBS.md.
4. Export formats: STEP (B-rep), STL/OBJ/PLY/glTF (mesh, with declared tessellation tolerance), source file. Each
   file labelled `brep` or `mesh`; a mesh is never presented as editable CAD.
5. Deterministic rebuild: same source + same backend version + same seed gives geometry identical within a
   declared tolerance (byte-stable STEP only after a declared normalisation). Provide `POST /rebuild` taking source
   only. Zoo cannot offer the seed part (section 2.4), which is a reason to own this layer.
6. Fidelity tests (Mirum CI, fixture set): volume, bbox, surface area, watertightness, unit round-trip (mm, m,
   inch), STEP re-import, mesh vs B-rep deviation. Tolerances come from policy.
7. Provenance in the manifest: backend name and version, model id/version, prompt hash (not the prompt when
   private), spec hash, source hash, seed, kernel version, timestamps, input/output sha256.
8. Resource limits: CPU/RAM/wall-clock per job, output bytes, triangle count, concurrent jobs; refusal codes aligned
   with the descriptor's `runtime_limits` and `ResourcePolicy`.
9. Component licensing: KCL (kcl-lib) is MIT, verified 2026-11-20 (section 2.3). The Zoo engine is proprietary and
   hosted, so KCL execution needs Zoo's engine; a Mirum-owned path remains CadQuery/OCCT. Still general knowledge,
   not re-read on 2026-11-20, verify before adoption: OpenCascade (OCCT) is LGPL 2.1 with an exception; CadQuery is
   Apache-2.0; build123d is Apache-2.0. Mirum already runs CadQuery in its sandbox. An open kernel stack avoids
   Zoo's competing-product and training terms. Leo-like features (PLM Q&A, vendor-part search) are separate
   products and not part of a CAD backend interface.
10. Registering as a capability adapter: implement the `adapter` named by the descriptor; add a
    `GeneratorDescriptor` (own id, version, claim, limits); register payload kinds; add the proposer mapping for
    `ai_proposer`; no change to existing descriptors or to backend/compiler.py allowlists.

## 6. Open questions and next step

Questions for the owner:
1. Is there a concrete family or request that needs Zoo (the documented need)? If not, close step 3 as "no
   integration".
2. Is sending prompts or briefs to a third party acceptable, given the training clause (Free plan trains by
   default)?
3. Has the owner read the Zoo terms (restriction (viii), benchmarking and competing products)? Is that clause
   acceptable for a text-to-CAD product that plans its own backend, or is Zoo excluded, or does Zoo give written
   permission? Until answered, no comparison or benchmark runs against Zoo.
4. Monthly budget cap if Zoo is ever used ($0.0083 per second).
5. Should the own backend start as a CadQuery-based service (section 5) or wait?

Next concrete step, no live call: with the owner's yes only, (a) re-read the Zoo spec to confirm the WS path
(`/ws/ml/copilot` vs `/ws/ml/zookeeper`) and fill the remaining unverified items (rate limits, per-turn latency,
tessellation tolerance, B-rep claim for text-to-CAD output, terms section numbers); (b) get legal review of the
terms restrictions and section 7; (c) draft a spec-only `zoo_text_to_cad` descriptor and a fixture-based fidelity
test plan. Implementation and any Zoo key need a separate written owner yes.

## Sources (read 2026-11-20)
- OpenAPI spec: https://api.zoo.dev/ (JSON, v0.1.0, read 2026-11-20);
  https://raw.githubusercontent.com/KittyCAD/kittycad.rs/main/spec.json (read 2026-11-20)
- API docs: https://zoo.dev/docs/developer-tools/api (read 2026-11-20),
  https://zoo.dev/docs/developer-tools/api/authentication (read 2026-11-20),
  https://zoo.dev/docs/developer-tools/api/billing (read 2026-11-20),
  https://zoo.dev/docs/developer-tools/agent-api (read 2026-11-20),
  https://zoo.dev/docs/developer-tools/api/ml/get-a-text-to-cad-response (read 2026-11-20),
  https://zoo.dev/docs/developer-tools/api/ml/generate-a-cad-model-from-text (404 on 2026-11-20)
- Tutorial: https://zoo.dev/docs/developer-tools/tutorials/beginner-onboarding-py (read 2026-11-20)
- Pricing and blog: https://zoo.dev/pricing, https://zoo.dev/api-pricing, https://zoo.dev/zoo-pricing,
  https://zoo.dev/blog/per-second-billing, https://zoo.dev/blog/turning-on-billing-for-text-to-cad (all read
  2026-11-20)
- Terms: https://zoo.dev/terms-and-conditions (read 2026-11-20, direct quote, no section numbers);
  https://zoo.dev/aquarium-terms-of-use (search summary only)
- CLI manual: https://zoo.dev/docs/developer-tools/cli/manual/zoo_ml,
  https://zoo.dev/docs/developer-tools/cli/manual/zoo_ml_kcl,
  https://zoo.dev/docs/developer-tools/cli/manual/zoo_kcl_export (read 2026-11-20)
- Repos and licences: https://github.com/KittyCAD/modeling-app,
  https://raw.githubusercontent.com/KittyCAD/modeling-app/main/LICENSE,
  https://raw.githubusercontent.com/KittyCAD/modeling-app/main/rust/kcl-lib/Cargo.toml,
  https://github.com/KittyCAD/kittycad.py, https://raw.githubusercontent.com/KittyCAD/kittycad.py/main/kittycad/client.py,
  https://github.com/KittyCAD/cli (all read 2026-11-20)
- Packages: https://pypi.org/pypi/kittycad/json (1.13.0), https://pypi.org/pypi/zoo-kcl/json (0.3.197) (read
  2026-11-20)
- Engine: https://zoo.dev/press/zoo-introduces-zookeeper, https://zoo.dev/enterprise (search snippets, 2026-11-20)
- Leo: https://www.getleo.ai/llms.txt (read 2026-11-20); https://www.engineering.com/leo-ai-can-now-generate-full-cad-assemblies/
  (search listing only)
- Third-party, search snippet only: https://www.wevolver.com/article/we-tested-7-text-to-cad-tools-are-they-actually-useful-for-engineers
- Not found on 2026-11-20: any page with numeric rate limits; any on-prem offer; any seed or model-pin request field.
