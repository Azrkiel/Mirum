# Offline brief preparation

`engineering.briefs.BriefService(repository)` provides `validate`, `create`,
`read`, `export`, `edit`, `finalize` and `read_brief`. Inputs use B03's normalized
`BriefContent` JSON shape (mm, N, MPa), including explicit capability/policy
versions, material source, assumptions, mode and choices. No missing physical
input is filled in. B03 quantity/conversion helpers can prepare values for review;
kg cannot be supplied as force. No provider or native CAD is imported.

```python
import json
from pathlib import Path
from datetime import datetime, timezone
from engineering.briefs import BriefService, MemoryBriefRepository
from engineering.contracts import REVIEW_FIELDS, ReviewAcknowledgement

service = BriefService(MemoryBriefRepository())
payload = json.loads(Path('backend/tests/fixtures/engineering/brief-draft.json').read_text())['content']
prepared = service.create(payload)
draft = prepared.draft
# Display service.export(draft.draft_id), including normalized choices, origins,
# material/source and assumptions. Collect this acknowledgement from the user.
review = ReviewAcknowledgement(
    draft_id=draft.draft_id, draft_revision=draft.revision,
    brief_sha256=draft.content.brief_sha256, acknowledged_fields=REVIEW_FIELDS,
    reviewed_at=datetime.now(timezone.utc), reviewed_by='local-user')
brief = service.finalize(review)
```

The example is an explicit caller review seam, not evidence of a UI click.
`PreparedDraft` retains submitted `input_json`, the immutable `BriefDraft`, and
field-specific `InputIssue` records (`clarification_required` or `unsupported`).
Unknown fields/features are retained in the visible input and prevent complete
content. Incomplete input cannot finalize. JSON input is bounded to 16 KiB;
nonfinite/unserializable/oversized input fails before storage. Do not log it.

`edit(id, expected_revision=..., payload=...)` replaces the full input and always
increments revision, even with identical meaning. Review must identify the exact
current revision and all B03 review fields. Stale edit/finalize operations raise
`RevisionConflict` (B11 maps this to 409). Unknown IDs raise `KeyError`. Identical
finalize retries reuse the immutable brief; a different review of that same
revision conflicts. Later drafts cannot mutate an already saved brief. Consumers
execute only an explicitly selected finalized brief, never a draft.

`BriefRepository` owns atomic create, compare-and-swap replace and
`finalize_if_current` operations. Finalize must atomically check the current
draft, bind its content/provenance, and uniquely store `(draft_id, revision)` and
brief ID. It must revalidate serialized records on read. The locked memory
implementation demonstrates these guarantees, including concurrent edits and
edit-during-finalize. B09 must implement the same transaction semantics in durable
storage, plus retention/access/lifecycle controls. No durable storage exists yet.

The optional explicit `provenance` parameter is B06's trusted integration seam;
AI field origins remain proposals. Ordinary typed replacements record supplied
fields as user input. Provenance completeness and exact policy versions are
validated again at finalization and by B04 candidate creation. Public transport,
authentication and browser review belong to B11/B12. No migration is required.
