# M1 editable plate acceptance

Mirum staged, reviewed and published two programs with `editable_plate@1.0.0` through its normal HTTP routes. The 80 mm deliveries were byte-identical. Both edits changed only the reviewed width to 100 mm. The fixed requirements digest holds height, thickness, intent and datum reference constant across revisions. The adapter runs the pinned research worker in a bounded subprocess. [Worker PR and standalone replay](https://github.com/Azrkiel/mirum-kernel/pull/2)

| Reviewed program | Delivered envelope (mm) | X centers / pitch (mm) | Side margins (mm) | STEP SHA-256 |
| --- | --- | --- | --- | --- |
| Edge-driven, initial | 80 × 50 × 6 | ±30 / 60 | 10 / 10 | `add48d2105891ba73ba2bc0ad5f52a3ce4f725a89c0001a95cd749da5104d4d2` |
| Datum-driven, initial | 80 × 50 × 6 | ±30 / 60 | 10 / 10 | `add48d2105891ba73ba2bc0ad5f52a3ce4f725a89c0001a95cd749da5104d4d2` |
| Edge-driven, edited | 100 × 50 × 6 | ±40 / 80 | 10 / 10 | `b60e1f0e97e10c9d8ad33e042d2fe71d9cbe9671b4106d7d32d9c717c518b771` |
| Datum-driven, edited | 100 × 50 × 6 | ±30 / 60 | 20 / 20 | `928cec2cba06551c99e82ec2159f250646a79ebb4f1c94812d5c9d0d057a766b` |

All four rereads measured four Ø6 mm holes at the listed X positions and Y ±15 mm. Each bore axis is +Z and spans Z 0–6 mm. Coordinates are in the right-handed `plate.datum` frame, whose origin is the center of the lower XY face. The side planes are X ± half-width. The checks use absolute tolerances of 1e-6 mm for lengths and positions, 1e-8 for the axis and full-turn span, and 1e-5 mm³ for volume. Each authored bore must match exactly one measured bore and each boundary plane must be unique.

The delivered verifier hashes and leases the job's actual bytes, reconstructs the check plan from reviewed ProgramIR and execution lock, then starts a fresh OCCT reader process. It binds its verdict to the revision, measured STEP digest and artifact manifest. Successful promotion also requires the independently reread report to match the delivered report byte for byte. The evidence record contains the reviewed revision and plan identifiers, report and package digests, bore observations and refusal checks. [Machine-readable run evidence](evidence/editable-plate.json)

## Refusal and recovery outcomes

| Case | Observed result |
| --- | --- |
| Width expressed in newtons | `MEL-S005` at `width` |
| 24 mm width overlaps the reviewed hole placement | `MEL-S021` at `width` |
| Unbound datum reference | `MEL-S020` at `datum_ref` |
| Changed fixed height with an otherwise valid edit | `requirements_changed` |
| Real 100 mm datum-driven STEP substituted for the reviewed edge-driven STEP, with consistent artifact hashes | `delivered_check_failed` with `plate.feature_missing`. Promotion refused before the substituted STEP became downloadable. |
| Valid STEP with a coaxial counterbore | `delivered_binding_ambiguous` with `plate.ambiguous_feature`. Promotion refused. |
| Run with a stale parent, or staging again from the stale parent | `stale_parent` |
| Cancel a reviewed width edit, or interrupt admission after the durable link | The prior head, downloaded STEP, report and evidence were preserved. Recovery terminalized the interrupted revision as cancelled. |
| Rebuild after interruption, retry the same accepted revision, then attempt a second edited revision from the same parent | One child published. Its predecessor still served its original STEP, report and evidence. |

The standalone kernel run also refused a STEP containing two datum candidates and replayed the geometry oracle at all nine widths for both intents. The product run captured and freshly remeasured the edited edge plate's replay archive.

## Replay on the release pin

Product main at checkout was `b08fd9ea8a7f6a586766c2521f7deb83ce391b44`. During testing, the F09 live-workflow PR advanced main to `b65136b162f0fd2ff1f2bae7b6a79169e381cccc`. Its changes are confined to F09. [Mirum #145](https://github.com/Azrkiel/Mirum/pull/145)

Native acceptance ran at `6a62998419f504f067cdfe3ba45be77a85efaa00` on Debian 13 Linux x86-64, CPython 3.10.22, CadQuery 2.6.1, cadquery-ocp 7.8.1.1.post1, Pydantic 2.9.2, FastAPI 0.115.0 and pytest 8.3.3. The product `requirements-release.lock` SHA-256 was `9ffe8a24069acd5776f4186a8393162a53819de7322f533b016939b6aedf7eed`.

Research main was `1da2197afce7bd576a0d2d101d6baf1ae2649524`. The unmodified worker snapshot is from `6a68e99c67b99375f2a49a794f5d44464c6307e3`. The research study ran on CPython 3.12.14, CadQuery 2.7.0 and cadquery-ocp 7.8.1.1.post1. Its 18 round trips passed and it measured the same geometry for the four acceptance cases. The worker's STEP digests matched exactly across these two toolchains. [Research run and commands](https://github.com/Azrkiel/mirum-kernel/blob/m1/editable-plate-worker/docs/editable-plate.md)

Run from the product repository root in Bash. Use a fresh directory for the acceptance evidence. Pytest owns the `--basetemp` directory and replaces its content on a subsequent run.

```sh
python3.10 -m venv .venv-m1
. .venv-m1/bin/activate
python -m pip install --require-hashes --no-deps -r requirements-release.lock
python -m pip check
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1
unset GEMINI_API_KEY_HEAVY GEMINI_API_KEY_PRO GEMINI_API_KEY GOOGLE_API_KEY

python -m pytest -q -ra backend/tests/test_program_editable_plate_native.py \
  backend/tests/test_language_semantic.py --basetemp /tmp/mirum-m1-acceptance
PYTHONPATH=backend python -m engineering.program.replay verify \
  /tmp/mirum-m1-acceptance/test_editable_plate_intents_an0/edge-100/replay.zip --measure

python -m pytest -q -ra \
  backend/tests/test_program_{beam_bridge,adapter_occt_native,compiled_beam_native,delivered_native,interface_native,revisions_native,equipment_bay_native}.py \
  backend/tests/test_engineering_{beam_golden,cantilever}.py
python -m pytest -q -ra \
  backend/tests/test_engineering_plate_{spec,reference,fea,benchmarks,adapter,api,native,qualification}.py
python -m pytest -q -ra \
  backend/tests/test_program_{schemas,kernel_registry,kernel_records,kernel_negative,registry,interface_parity}.py \
  backend/tests/test_language_{lexer,parser,lower_beam,semantic_corpus}.py \
  backend/tests/test_engineering_native.py
```

The M1 tests ran with empty or unset provider keys. The application uses a local fixture access token for Program API requests. The tested path performs deterministic local CAD work. STEP and report hashes can be compared across replays with the recorded toolchain. Revision IDs, receipts and archive digests identify the individual run.

The final run passed two native M1 tests, 54 MEL semantic tests, 87 beam and bay tests, and 354 plate-bending tests. The additional schema, registry, interface and engineering-native run passed 660 tests. The archived edited edge plate returned `native_reread=consistent`.
