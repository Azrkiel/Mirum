# QA Stress Report — Propulsion Domain F — 2026-05-22

Stage-4 adversarial QA. Five mutations of the four reference prompts were run
live through the eval harness with `--strict-lvm` (Gemini, Docker), probing
scale extremes, edge counts, the Rao-table boundary, and out-of-envelope input.

## Summary
- Mutations generated: 5
- Passed: 4/5 (lvm median 4.7)
- Captured as a regression guard: 1 (a passing edge-count case that covers a new path)
- Genuine pipeline bugs found: 0

## Results
| id | axis | result | lvm | disposition |
| --- | --- | --- | --- | --- |
| F_qa_micro_conical_nozzle | scale floor (1 mm throat) | pass | 4.0 | drop (covered by reference) |
| F_qa_macro_rao_nozzle | scale ceiling (400 mm throat) | pass | 4.7 | drop (covered) |
| F_qa_max_expansion_rao | Rao table ceiling (ε=100) | pass | 5.0 | drop (covered by rao_table unit test) |
| F_qa_dense_cooling_channels | edge count (120 channels) | pass | 4.7 | **KEEP** — guards the batched-cut memory fix |
| F_qa_subcritical_expansion | out-of-envelope (ε=3 < table floor 5) | fail (lvm 2.3) | 2.3 | documented limitation (see below) |

## Added to prompts.json
- `F_qa_dense_cooling_channels` — regression_for=[F]. Passes (lvm 4.7) but kept
  because it exercises the batched helical-cut at 2× the reference channel count
  (120 vs 60); reverting the batching memory fix (`2a8adfa`) would OOM this
  against the 1.5 GB sandbox `RLIMIT_AS`. A passing guard, per the qa-stress
  "only keep a passing mutation if it covers a new code path" rule.

The other three passing mutations were dropped to avoid regression-set bloat
(scale/table behavior is already covered by the reference prompts + unit tests).

## Out-of-envelope finding (not a bug, not added)
`F_qa_subcritical_expansion` (Rao bell, ε=3) scored lvm 2.3 (below the strict
threshold) but **did not crash, time out, or raise** — it produced a valid but
stubby low-expansion solid. ε=3 is below the Rao table floor (5), so this is the
pipeline degrading gracefully on out-of-envelope input rather than a defect. Not
added as a regression entry (it would be a perpetually-red gate row with nothing
to "fix"); recorded here as a known limitation. If a future requirement needs
sub-ε=5 nozzles, extend `rao_table` or add a conical fallback for low ε.

## Robustness confirmed
Scale 1 mm → 400 mm, ε up to the table ceiling (100), and 120 helical channels
all produce valid, well-scored geometry. The 120-channel case peaking under the
sandbox limit confirms the batched-cut memory fix scales beyond the 60-channel
reference.
