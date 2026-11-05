# Retained CEM dynamics-and-controls evidence

`mirum-dynamics-control-evidence.tar.gz` (identity in `manifest.json`) holds the
raw outputs behind [the dynamics-and-controls handoff](../../../implementation/cem-aircraft-dynamics-control.md)
and [its receipt](../../cem-aircraft-dynamics-control-results.json):

- `family-physics-b2b70cb/`: the fresh tailless +3-degree section audits
  (declaration written before native work, every native query, midpoint checks,
  `coordinate-audit.json` per grid, independent replay) and the resulting
  ledger assessment; `family-physics-b2b70cb.log`.
- `family-dynamics/`: per-family qualification-input inventories, every
  envelope declaration (written before evaluation) and its full envelope report,
  the banked tailless envelope, and `summary.json` (including the published
  747 reference check); `family-dynamics.log`.

Every envelope here is `blocked`; nothing in the archive establishes aircraft
qualification, physical validation, construction or verified gain. Verify the
SHA-256 before reuse; replay requires the same implementation identity
(recorded in each report) or it is rejected as stale.
