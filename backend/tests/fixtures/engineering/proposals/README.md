# E05 proposal corpora (offline)

Fixed corpora for the E05 AI proposers of the generator capabilities. Contract:
`backend/engineering/proposal_contract.py`. The beam corpus stays
`../ai-corpus-v1.json` (B13-qualified, hash-pinned by the live-eval script); do not edit it.

## Files

One file per capability, named `<capability_id>-v1.json`:
`co2_dragster-v1.json`, `regen_chamber-v1.json`, `glider_cem-v1.json`.
A corpus file is immutable once a live qualification cites its SHA-256; changes go in a `-v2` file.

## Shape

Same shape as `ai-corpus-v1.json`, plus `capability`:

```json
{
  "version": "e05-<capability_id>-v1",
  "capability": {"id": "...", "version": "...", "numerical_policy_version": "...", "resource_policy_version": "engineering_resources_v1"},
  "scope": "offline fake-provider corpus; live use only inside an owner-approved qualification job",
  "cases": [
    {
      "id": "R01",
      "kind": "complete | clarification | unsupported | out_of_family | adversarial",
      "prompt": "<user request, at most 2000 characters>",
      "expected_state": "proposed | clarification_required | unsupported",
      "expected_route": "<capability id the deterministic router returns, or null>",
      "expected_content": {"<reviewable field path>": "<value>"},
      "fixture_response": "<what the fake provider returns for this prompt; raw text or object>"
    }
  ]
}
```

## Rules

- At least 10 cases per capability, covering every `kind`. At least 3 `complete` cases.
- `out_of_family` names another registered capability or none. Its `expected_state` is
  `unsupported`, never a substituted proposal.
- `adversarial` covers prompt injection, requests for code, unsupported features (see the
  descriptor's `unsupported_features`) and values outside the spec schema. Expected state:
  `unsupported` or `clarification_required`, never `proposed` with a value the prompt did not state.
- `expected_content` lists only fields the prompt states. Everything else comes from the
  preset or the policy and is marked that way in provenance.
- No secrets and no real model replies. A `fixture_response` is hand-written.
