# Retained CEM panel-distribution evidence

These archives publish the raw task evidence previously retained only in the
cloud workspace. `manifest.json` records their exact SHA256 identities and sizes;
both match the committed section receipt. The integrated archive contains the
final evidence after incorporating the aircraft-physics update; the earlier
archive preserves the pre-integration snapshot.

The archives contain native inputs/outputs, failed and interrupted cases,
declarations, solver traces, boundary-layer diagnostics, native build/source
provenance and setup helpers. They do not restore unavailable historical raw
archives or establish passing section/aircraft qualification. Read
[the qualification matrix](../../../implementation/cem-panel-distribution-continuation.md)
and [the receipt](../../cem-panel-distribution-results.json).

The source and report commit is `7121188`. Its final regression passed 635 tests,
with no failures or skips and two warnings. This delivery only publishes existing
artifacts; no numerical code, evidence bytes or admission flags were changed.
