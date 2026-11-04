# Regen verifier validation results: November 3, 2026

**Status: partial validation. Six of 48 primary designs passed; 41 have not been tested, and the largest design remains unresolved after a deadline timeout.**

Five focused retry runs passed all twelve geometry checks after more host RAM became available. They cover one previously memory-failing large mesh, three representative channel/wall geometries, and a fresh repeat of that large mesh. All five preserve baseline raw and cleaned STL hashes. The repeat matches both hashes from the first build.

These results demonstrate successful verification of several large models. They do not establish reliability across every allowed design or suitability for physical production. In this test, model size refers to mesh complexity rather than the physical size of a product.

## Overall coverage

| Measurement | Result |
|---|---:|
| Unique primary designs accepted | 6/48 |
| Primary designs not yet tested | 41 |
| Primary design with unresolved resource failure | 1 |
| Fresh benchmark repeats matched | 1/8 |
| Reference runs accepted | 1/2 |
| Accepted runs, including retries | 9 |
| Unique accepted case results | 8 |
| Total geometry attempts | 14 |
| Retained resource interruptions | 5: four host-memory stops, one timeout |

The accepted-run count includes an extra successful retry of a primary design already accepted under an earlier runtime policy. Resource interruptions are retained separately and are not counted as geometry defects.

## Five focused retry runs

| Run | End-to-end time | Container peak | Result |
|---|---:|---:|---|
| Large regression mesh | 227.922 s | 1.765 GiB | 12/12 checks pass; baseline hashes match |
| Representative geometry 1 | 160.234 s | 1.163 GiB | 12/12 checks pass; baseline hashes match |
| Representative geometry 2 | 147.641 s | 1.411 GiB | 12/12 checks pass; baseline hashes match |
| Representative geometry 3 | 130.609 s | 0.932 GiB | 12/12 checks pass; baseline hashes match |
| Fresh repeat of large regression mesh | 223.265 s | 1.767 GiB | 12/12 checks pass; baseline hashes match |

Maximum container peak: **1.767 GiB**. The saved cleaned STL files were hashed independently. Report safety fields were preserved; physics and standards remain `not_evaluated`, and `engineering_certified` remains `false`.

## Unresolved largest case and retained interruptions

The largest case exceeded its configured 360-second end-to-end deadline. The limiter observed 361 seconds at its polling boundary; the controller recorded 361.922 seconds including inspection and cleanup. The builder completed in 134 seconds, but verification had not finished. Peak container memory was 1.785 GiB, with no OOM or host-memory-floor stop. Its retained raw STL matches baseline. There is no completed Docker geometry verdict or cleaned-hash verdict for this case. The exact slow verifier phase has not been isolated.

An earlier attempt on the other large mesh stopped when host free RAM fell to 0.6 GiB, below the unchanged 1.5 GiB stop floor. The limiter observed 110 seconds; total controller time was 112.078 seconds. Container peak was 1.023 GiB, with no OOM or geometry verdict. That mesh subsequently passed in the retry set above.

The remaining historical resource interruptions are retained in the totals. An admission refusal before starting geometry is not counted as a geometry attempt.

## Runtime and limits

- Immutable local Docker image ID: `sha256:a4b8b7fa6128357a29320ea4d1e4010b5ea02554b20ac781ac347e8be2b244cf`. The image was not published.
- Focused retry policy: 2 GiB container memory and equal memory-swap caps, two CPUs and numeric threads, no network, and a 360-second end-to-end deadline.
- Host RAM policy: 3.6 GiB launch floor and 1.5 GiB stop floor.
- Python allocator mmap/trim thresholds: 8192 bytes; the C# builder retains its default allocator environment.
- Temporary WSL cap: `memory=3GB`; retry guest `MemTotal` measured 2,975,388 KiB.
- The earlier four accepted runs used default allocation and 3 GiB container caps. They do not establish acceptance under the newer policy.
- Geometry thresholds, cumulative work limits and the internal memory guard were unchanged.

The original WSL configuration was restored after each session. Task-owned Docker/WSL activity was stopped, with no running WSL distribution on the final check.

## Retained native evidence

Existing evidence was reused because tested source hashes were unchanged: 179 small tests plus 43 adversarial tests, **222 total**. The saved native matrix detected **16/16 injected defects**, passed **2/2 valid controls**, and had zero matrix resource failures. Its maximum process-tree peak was 1.00 GiB under a 2 GiB cap. These tests and the matrix were not rerun merely to export this report.

A separate largest-mesh native attempt timed out at 300 seconds. A longer 600-second diagnostic passed all twelve checks in 341.297 seconds, peaking at 1.55 GiB. That saved-STL Windows diagnostic is not a fresh Linux end-to-end pass or proof of meeting the Docker deadline.

## Tested source provenance

**The verifier and test changes were uncommitted when measured.** Their base commit is `c6e9416a311eddd3e5f241027c159403ee8abc92`. This results-only export records those local file hashes; it does not publish the implementation changes or assert that the base commit alone produced these results.

| Tested file | SHA256 |
|---|---|
| `backend/engineering/regen_mesh.py` | `9d59b01501343cfe17b4d820029258325e6e42fb7ce4236386eb36191d317f1b` |
| `backend/tests/test_regen_budget.py` | `b8456aad5e0055a76d96a8b5f611cf2dcb5bdb01c54cd192bdd7dcca4930c9e5` |
| `backend/tests/test_regen_mesh.py` | `5e2e11227f34f4b71fd20e06afaa98517204054e6193e9cd81d3aa88a1c6f761` |

## Remaining work

Investigate the largest case under the unchanged 360-second acceptance deadline. The full 48-design sweep, remaining seven benchmark repeats, second reference run and stress/soak testing remain incomplete. Representative cases were selected for geometric variety; these are regression measurements, not exhaustive or blind generalization evidence.

Private fixture parameters, defect coordinates, complete per-case reports, meshes, handoffs and local operator paths are excluded from this public export. See the [machine-readable summary](regen-verifier-results-2026-11-03.json) for the published metrics.
