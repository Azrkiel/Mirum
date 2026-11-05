# Copy-paste prompt: one-session deterministic wedge prototype

This prompt implements the smaller [one-session plan](2026-11-05-co2-dragster-deterministic-session.md), not the entire [original scope](2026-11-05-co2-dragster-scope.md).

Paste the following into a fresh coding session with access to the repository and a native CadQuery environment or working Docker daemon:

```text
Implement the deterministic CO2 dragster wedge prototype in Azrkiel/Mirum.

Planning branch: docs/co2-dragster-deterministic-session
Plan: docs/plans/2026-11-05-co2-dragster-deterministic-session.md
Repository: https://github.com/Azrkiel/Mirum

Read the plan and applicable AGENTS.md instructions first. Create an isolated
implementation branch named feat/co2-dragster-wedge from the planning branch;
do not use or merge the divergent integration/text2cad-hardening checkout.
The plan is the approved scope and implementation contract. Execute it in
this session using reasonable decisions within that contract. Do not stop
at proposing another plan. Ask only if a required dependency or substantive
conflict prevents progress, and state the concrete blocker.

Build one local Python CLI:
  python -m backend.dragsters.cli \
    --spec backend/dragsters/examples/wedge.json \
    --output-root output/dragsters

Deliver:
- Strict, versioned millimeter JSON parameters and the explicit example.
- One deterministic planar wedge body with a flat rear housing, one
  rear-open cartridge pocket, and two transverse axle bores.
- Native checks on the exported/reimported STEP: one valid solid, finite
  positive/expected volume, correct absolute bounds, and the intended cuts.
- A fresh result directory containing body.step, spec.json, and report.json.
- Report digests bound to the exact saved spec and STEP; no replacement
  of previous results when a new attempt fails.
- Focused spec, native geometry, and CLI lifecycle tests, including actual
  missing/wrong cuts, plus a concise local usage guide.

Follow the plan's exact example, parameter envelope, tolerances, field
semantics, file map, and failure behavior. Use Pydantic and CadQuery already
in the project. Use test-first cycles and demonstrate real native CAD;
missing-kernel skips or mocked geometry do not count as completion.

Keep it within one focused session. No LLM/provider calls, text extraction,
browser UI, GLB, API integration, sandbox-policy changes, extra styles,
wheels/assemblies, mass, race simulation, or manufacturing/compliance claims.
Do not change existing unrelated workflows. These dimensions are examples,
not a verified cartridge standard. JSON editing is the editing interface.

Verify the successful example, saved-JSON regeneration, invalid input, and
failed-attempt preservation. Run the focused tests and git diff --check.
Commit on the implementation branch; do not merge or deploy. Finish with
the branch/commit, changed files, exact test commands and counts/skips,
example artifact path, and any unresolved limits or environment blocker.
```

The shell example uses POSIX continuations. In PowerShell put the command on one line. A working native environment is a prerequisite; environment repair is not included in the one-session scope.
