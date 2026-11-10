# Mirum dark workspace

Approved in chat: dark workspace with electric blue accents, restrained density,
related controls together, concise labels, custom line symbols, no emojis or gradients.

## Layout

Keep the existing Streamlit application and API contracts. A persistent sidebar
contains the Mirum mark and four workflows: Text to CAD, Dragster, Glider Search,
Glider Engineering. A compact page header identifies the active task. Each page
groups requirements in bordered panels. The Text to CAD and Dragster pages put
inputs beside a larger preview. Results group model, analysis, parts and code;
parameters and refinement stay beside the result they affect. Advanced inputs
and detailed evidence remain available behind clearly named disclosures.

## Visual system

Charcoal canvas (#0c111b), slate panels (#121b2a), visible borders (#2b3950),
near-white text (#edf3ff), muted text (#a0aec2), electric blue (#386bff),
and distinct accessible success/error states. Use readable system sans type,
tabular numerical values, 6–10 px corners, and consistent 8 px spacing increments.
Draw the Mirum mark and empty-state geometry with inline SVG. No decorative
marketing blocks, invented metrics, emojis, or gradients. Mobile layouts stack
controls before the preview and keep navigation accessible.

## Scope and invariants

Apply the same palette and concise copy to all four workflows, the embedded glider
explorer, Babylon model viewer, and standalone regen generator page. Preserve all
inputs, range/units validation, provider options, progress, cancellation, downloads,
JSON import/export, analysis charts, verification scope and uncertainty statements.
Do not change backend contracts or turn screening estimates into certification.
Failure or interrupted work must retain the last accepted model and artifacts.

## Acceptance

Verify navigation persistence, primary actions, invalid input, retained results,
and download grouping with existing regression tests and targeted UI tests.
Inspect the running app in a browser at desktop and narrow sizes. New visual
styles must keep keyboard focus, text contrast, and disabled states legible.
