# Mirum CEM implementation and review artifacts

This branch contains the bounded glider CEM screening implementation, its tests,
the original roadmap, and the builder handoff. Start with
[`docs/implementation/cem-handoff.md`](../../docs/implementation/cem-handoff.md).

The workflow covers mission feasibility/gaps, process-aware search, mesh-derived
mass checks and re-trim, local uncertainty, versioned calibration and a UI/API.
The full roadmap is incomplete: novel-family discovery, PicoGK manufacturing,
full physical closure and advanced thermal/ridge environments remain pending.
The example bundle is screening geometry and templates, not fabrication approval.

| File | Purpose |
| --- | --- |
| `mirum-cem-handoff.md` | Copy of the builder handoff; the repository document above is authoritative |
| `implementation-status.md` | Implemented scope, limits, setup and API usage |
| `verification.md` | Historical verification evidence and its limits |
| `performance-report.json` | Exact retained example report, including model/runtime provenance |
| `glider-screening-example.zip` | Example STLs, templates, design and performance evidence |
| `glider-example.png`, `glider-example.pdf` | Views of the actual example mesh triangles |
| `mirum-cem.patch` | Portable earlier implementation snapshot based on `2e6a4eebe666955f3493d354e7d93cf0380312db` |

Prefer checking out this branch over applying the patch. The patch predates the
published handoff and artifact index; it is retained as a review/recovery asset.
The verification record's uncommitted-state wording describes the earlier test
session. Publishing this branch does not establish a fresh dependency-image
build, live-provider validation, physical flight validation or a production deployment.
