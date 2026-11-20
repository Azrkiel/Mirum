# E05 step 3: Zoo as an optional CAD backend, and a self-hosted Zoo/Leo-like backend

Status: read-only investigation, no integration. Owner answer Q6 (docs/plans/2026-11-20-wave-e.md): Zoo is in
scope only as access/licensing/cost/export-fidelity research; no Zoo key and no live call without the owner's yes.
All external facts below come from public pages read on 2026-11-20 and cite the URL. "Unverified" marks anything
that could not be confirmed from a public page that day. No Zoo or Leo account, key or API call was used.

## 1. Decision summary

- Documented need now: none. E05 step 3 says "only for a documented need"; no capability, task or failing case in
  the repo currently requires a Zoo-generated artifact.
- Recommendation: do not integrate Zoo in Wave E. Keep this document as the intake checklist.
- Evidence that would justify an integration: (a) a concrete request family no registered native capability can
  express and the owner wants it; (b) a written owner yes to an account and key; (c) commercial terms review
  (section 2.3, in particular the competing-product clause); (d) a fidelity test set (section 4) that passes.
- Long-term own backend (Q6): feasible as an adapter behind the existing capability contract; the interface is
  specified in section 5. It should start from open components, not by cloning Zoo's hosted engine.

## 2. Zoo (zoo.dev, formerly KittyCAD)

### 2.1 Access model
- Bearer-token API at api.zoo.dev. The "get text-to-CAD model" page shows
  `GET https://api.zoo.dev/user/text-to-cad/{id}` with `Authorization: Bearer $ZOO_API_TOKEN`, and states the caller
  must own the model. Source: https://zoo.dev/docs/developer-tools/api/ml/get-a-text-to-cad-response (read 2026-11-20).
  That page lists no response fields, status values or formats (unverified beyond that).
- A search result for the generate endpoint described `POST /ai/text-to-cad/{output_format}` with the output format
  in the path, with one docs variant marked deprecated. This comes from a search summary of
  https://docs.zoo.dev/docs/api/ml/generate-a-cad-model-from-text (search 2026-11-20); the page itself redirected to
  a 404 when fetched. Request body, current path and the format enum are unverified.
- Results are asynchronous: the same search summary reports a status endpoint with values around
  "completed" and "failed" (unverified; not read from the page).
- Rate limits: not stated on https://zoo.dev/pricing or https://zoo.dev/api-pricing (read 2026-11-20). Unverified.
- Zoo exposes an MCP server; hosted operations count as external API usage
  (https://zoo.dev/api-pricing, read 2026-11-20).

### 2.2 Cost
- https://zoo.dev/pricing (read 2026-11-20): plan prices render as "Loading..." in the fetched copy; states that
  Zookeeper reasoning and billable API activity draw from the same monthly usage credit, Design Studio CAD modelling
  stays unlimited, pay-as-you-go is opt-in.
- https://zoo.dev/api-pricing (read 2026-11-20): "Start free, then pay-as-you go"; "Some plans include monthly API
  usage; allowances vary by plan"; failed calls are not charged; only successful billable operations consume usage.
  No per-call or per-minute figure appeared; it points to /zoo-pricing and billable-calls docs (not read).
- Cost per Text-to-CAD call: unverified. Needed before any budget decision.

### 2.3 Terms and licensing
- Source: https://zoo.dev/terms-and-conditions (read 2026-11-20, via page summary; re-read the full text before any
  decision, no legal-grade read was done).
  - Outputs: customer "own[s] and retain[s] all right, title and interest (if any) in the Outputs" (Section 7(a)),
    subject to Section 7(b). The "(if any)" qualifier means ownership of AI-generated geometry is not guaranteed.
  - Training: Zoo may use Customer Materials to "train and improve Zoo's artificial intelligence and machine
    learning models"; opt-out only "if your subscription tier allows" (Sections 7(b), 5(b)). Prompts or reference
    designs sent to Zoo could train its models by default. Mirum's brief/evidence payloads must not be sent
    without owner review of this.
  - Competing products: the customer may not use the Services for "benchmarking or competitive analysis" of
    competitive products, nor develop or sell anything that "could, directly or indirectly, compete with the
    Services" (Section 3(b)). Mirum is itself text-to-CAD, so this clause needs counsel review, and it bears
    directly on using Zoo as a baseline, a teacher model, or a benchmark (for example in BenchCAD-style evals).
  - Aquarium community terms differ (https://zoo.dev/aquarium-terms-of-use, via search summary) and are not the
    API terms.
- Open source: the modeling-app (Zoo Design Studio) repo is MIT, "Copyright (c) 2023 The Zoo Authors"
  (https://github.com/KittyCAD/modeling-app and its LICENSE file; read 2026-11-20). KCL: the language is implemented
  in the Rust crate kcl-lib (https://docs.rs/crate/kcl-lib/latest, search listing); its licence was not confirmed.
  A page at https://zoo-industries.pages.dev/open-source was reported (search snippet) to claim MIT/Apache across
  repos; the domain looks unofficial, so unverified.
  Geometry engine: the modeling app streams video from a hosted engine over WebSockets and Zoo documents an "Engine
  API" to a CAD kernel on remote computers (https://zoo.dev/docs/developer-tools/engine-api, search snippet). No
  public statement that the engine source is available was found. Treat it as closed and hosted: unverified.

### 2.4 Text-to-CAD API shape and outputs
- Inputs: a text prompt (field names unverified). Outputs: Zoo says results are B-rep surfaces rather than point
  clouds and a STEP file can be imported into other CAD tools (search summary, 2026-11-20). CLI export formats are
  listed as fbx, glb, gltf, obj, ply, step, stl, and a later CLI adds kcl
  (https://zoo.dev/docs/developer-tools/cli/manual/zoo_ml_text-to-cad_export, search summary; the page 404'd on
  fetch). A third-party test lists FBX, GLB, glTF, OBJ, PLY, STL, STEP, KCL
  (https://www.wevolver.com/article/we-tested-7-text-to-cad-tools-are-they-actually-useful-for-engineers, search
  snippet, not read in full). A file-conversion API also exists
  (https://zoo.dev/docs/api/file/convert-cad-file-with-defaults, listed in search, not read).
- Export fidelity: STEP is B-rep; the other formats are meshes or scene formats (general format properties, not
  Zoo claims). The Zoo documentation I could reach states no unit convention, tolerance, tessellation setting,
  determinism guarantee or model-version pinning. All unverified; they must be measured (section 4).
- KCL: KCL source is the editable representation. A KCL output is the only form where "editable" has a defensible
  meaning, and only if it rebuilds to the same geometry.

## 3. Leo (getleo.ai)
- Product: "engineering design co-pilot" on a "Large Mechanical Model"; accepts text, sketches, images, CAD files;
  knowledge Q&A over PLM data, search over 120M+ vendor parts, calculations with a code interpreter; Onshape
  integration through a PTC partnership (https://www.getleo.ai/llms.txt, read 2026-11-20). Coverage of assembly
  generation: https://www.engineering.com/leo-ai-can-now-generate-full-cad-assemblies/ (search listing only).
- Public API/SDK/developer docs: none found (llms.txt does not mention one). Pricing: not listed. Conclusion: Leo
  is a product, not an integrable backend. No public API; private or enterprise access is unverified.

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

Additional blockers: external data egress (prompts leave the machine; training default, section 2.3), outputs
likely vary across model versions (unverified), third-party outage, cost budget, no Zoo key permitted (Q6).

## 5. Self-hosted Zoo/Leo-like backend: interface Mirum would need

Principle: Mirum treats it as an untrusted artifact producer; acceptance stays with Mirum checks.

1. Request: typed spec in (versioned `spec_schema`, explicit units, requested output formats, seed, limits), plus
   the optional reviewed brief. Not a free prompt alone.
2. Response: primary artifact + generated source (CadQuery script or KCL) + manifest. The source is the editable
   representation; artifact and source must agree (rebuild test, item 5).
3. Async job API: `POST /jobs` (returns a job id; `Idempotency-Key` header, same key + same body returns the same
   job), `GET /jobs/{id}` (queued|running|succeeded|failed|cancelled, structured error code), `DELETE /jobs/{id}`
   cancel, artifact fetch by id with size and sha256. Timeouts and retry rules declared. Zoo's documented model is
   also asynchronous (section 2.1, partly unverified). Align with the repo's docs/plans/checked-engineering-v1/JOBS.md.
4. Export formats: STEP (B-rep), STL/OBJ/PLY/glTF (mesh, with declared tessellation tolerance), source file. Each
   file labelled `brep` or `mesh`; a mesh is never presented as editable CAD.
5. Deterministic rebuild: same source + same backend version + same seed gives geometry identical within a
   declared tolerance (byte-stable STEP only after a declared normalisation). Provide `POST /rebuild` taking source only.
6. Fidelity tests (Mirum CI, fixture set): volume, bbox, surface area, watertightness, unit round-trip (mm, m,
   inch), STEP re-import, mesh vs B-rep deviation. Tolerances come from policy.
7. Provenance in the manifest: backend name and version, model id/version, prompt hash (not the prompt when
   private), spec hash, source hash, seed, kernel version, timestamps, input/output sha256.
8. Resource limits: CPU/RAM/wall-clock per job, output bytes, triangle count, concurrent jobs; refusal codes aligned
   with the descriptor's `runtime_limits` and `ResourcePolicy`.
9. Component licensing (general knowledge, not re-read on 2026-11-20; verify before adoption): OpenCascade (OCCT)
   is LGPL 2.1 with an exception; CadQuery is Apache-2.0; build123d is Apache-2.0; KCL licence unconfirmed
   (section 2.3). Mirum already runs CadQuery in its sandbox. An open kernel stack avoids Zoo's competing-product
   and training terms. Leo-like features (PLM Q&A, vendor-part search) are separate products and not part of a CAD
   backend interface.
10. Registering as a capability adapter: implement the `adapter` named by the descriptor; add a
    `GeneratorDescriptor` (own id, version, claim, limits); register payload kinds; add the proposer mapping for
    `ai_proposer`; no change to existing descriptors or to backend/compiler.py allowlists.

## 6. Open questions and next step

Questions for the owner:
1. Is there a concrete family or request that needs Zoo (the documented need)? If not, close step 3 as "no
   integration".
2. Is sending prompts or briefs to a third party acceptable, given the default training clause?
3. Is the competing-product clause acceptable for a text-to-CAD product, or is Zoo excluded and the long-term path
   the own backend only?
4. Monthly budget cap if Zoo is ever used.
5. Should the own backend start as a CadQuery-based service (section 5) or wait?

Next concrete step, no live call: with the owner's yes only, (a) read the current Zoo API reference pages by hand
and fill the unverified items (request body, format enum, status values, rate limits, per-call cost, KCL licence);
(b) get legal review of terms sections 3(b) and 7; (c) draft a spec-only `zoo_text_to_cad` descriptor and a
fixture-based fidelity test plan. Implementation and any Zoo key need a separate written owner yes.

## Sources (read 2026-11-20)
- Read as pages: https://zoo.dev/pricing, https://zoo.dev/api-pricing, https://zoo.dev/terms-and-conditions,
  https://zoo.dev/docs/developer-tools/api/ml/get-a-text-to-cad-response, https://github.com/KittyCAD/modeling-app
  (and its LICENSE), https://www.getleo.ai/llms.txt
- Search summaries only (page fetch failed or not attempted): Zoo generate-a-CAD-model-from-text and CLI export
  docs, Wevolver test, Aquarium terms, kcl-lib on docs.rs, Engine API page, engineering.com Leo article.
