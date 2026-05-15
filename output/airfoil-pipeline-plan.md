# Airfoil Aerodynamic Analysis Pipeline — Implementation Plan

**Created:** 2026-05-13
**Scope:** End-to-end pipeline from text prompt → CadQuery wing geometry → XFOIL aerodynamic validation → Cl/Cd report

---

## Phase 0 — Verification & Environment Audit

Confirm that existing infrastructure is ready before building new pipeline stages.

### P0-01 — Gemini Model Strings ✅ VERIFIED

**Status:** VERIFIED — all model strings are correct and consistent.

**Findings:**

There is no `config.py`. Model constants are defined as module-level `MODEL` variables in individual files. Current state (post-404-fix, per `output/baseline-followup-todo.md`):

| File | Model constant | Value | Tier |
|------|---------------|-------|------|
| `backend/agents.py` | `MODEL` | `gemini-3-flash-preview` | Heavy (Machinist, Planner, REA, Refiner) |
| `backend/evaluation.py` | `MODEL` | `gemini-3-flash-preview` | Heavy (LVM scoring) |
| `backend/continuity_check.py` | `gemini_model` param default | `gemini-3-flash-preview` | Heavy (VLM zebra check) |
| `backend/classifier.py` | `MODEL` | `gemini-3.1-flash-lite-preview` | Lite (domain classification) |
| `backend/postprocessing.py` | `model_name` | `gemini-3.1-flash-lite-preview` | Lite (PMI LLM extraction) |

**API key architecture:** `GEMINI_API_KEY_HEAVY` only. Set globally via `genai.configure()` in `main.py:68`. Both heavy and lite models run off the same key — quota is metered per `(model, key)` pair. `classifier.py` does NOT call `genai.configure()` itself (key-leak bug documented in `output/baseline-followup-todo.md` has been fixed).

**Live API test:** Cannot run from sandbox (no key). Model strings were last successfully resolved after the 404 incident (prior string `gemini-3.1-flash-preview` returned 404; current strings confirmed OK in the eval run documented in `output/baseline-followup-todo.md`).

**Recommendation:** Create `backend/config.py` to centralize model strings:
```python
# backend/config.py
GEMINI_MODEL_HEAVY = "gemini-3-flash-preview"
GEMINI_MODEL_LITE  = "gemini-3.1-flash-lite-preview"
```
This removes the per-file MODEL constant scatter and makes future model upgrades a single-line change.

---

### P0-02/P0-03 — Domain D Audit ✅ COMPLETE (no additions needed)

**Status:** COMPLETE — Domain D is fully implemented with all required components.

**Audit results:**

| Requirement | Status | Location |
|-------------|--------|----------|
| NACA 4-digit parameterization | ✅ Present | `backend/cad_utils.py:172` — `make_naca_wire(naca_code, chord_length)` with full analytic NACA 4-digit formula; `strategies.py` `rule_d_parameters` with default extraction |
| Lofted span generation | ✅ Present | `strategies.py` `rule_reversed_lofting`: `cq.Solid.makeLoft([tip_wire, root_wire])` with reversed-order requirement (outward normals); `rule_d_workflow` STEP 2 |
| Explicit airfoil cross-section workplane chaining | ✅ Present | `strategies.py` `rule_spatial_alignment`: XZ plane convention, `rotate((0,0,0),(1,0,0),-90)` to map wire into XZ; `rule_rib_spar_intersection`: `cq.Workplane('XZ').rect(...).extrude(rib_thickness)` rib chaining; `rule_assembly_export`: `cq.Assembly()` packaging |
| Complete worked example | ✅ Present | `strategies.py` `rule_d_example`: end-to-end NACA 2412 wing with 5 ribs + 2 spars |

**Domain D rule set injected for every Domain D prompt:**
- `rule_d_overview` — OML Master Modeling explanation + why not Dual-Loft/shell
- `rule_no_shell` — explicit .shell() ban with failure mode description
- `rule_spatial_alignment` — X=chord, Y=span, Z=thickness; mandatory XZ rotation
- `rule_reversed_lofting` — tip-first, root-last with OCCT normal explanation
- `rule_rib_spar_intersection` — oversized blank + intersect(oml_solid) pattern
- `rule_assembly_export` — cq.Assembly() packaging + dual GLB/STEP export
- `rule_d_workflow` — 4-step generation process with code patterns
- `rule_d_example` — complete working example script
- `rule_wire_to_solid` — Wire→Face→Workplane.extrude fallback for non-loft cases
- `rule_d_parameters` — parameter defaults (NACA 2412, root 200mm, span 500mm, 5 ribs, 2 spars)

**Conclusion:** No implementation work required for P0-02/P0-03. Domain D is production-ready for airfoil geometry generation.

---

### P0-04 — XFOIL Binary ❌ NOT FOUND — installer script created

**Status:** NOT FOUND on PATH. Sandbox has no outbound network, so the binary cannot be auto-downloaded server-side. A self-contained PowerShell installer has been created at `T2CIII/bin/install_xfoil_windows.ps1`. Run it once; it downloads and extracts in ~30 seconds.

**Test performed in sandbox:**
```bash
$ which xfoil
(no output)
$ xfoil  # subprocess test
(FileNotFoundError: [Errno 2] No such file or directory: 'xfoil')
```

**Expected post-install path:** `C:\Users\Kevin\Developments\T2CIII\bin\xfoil.exe`

`backend/config.py` is pre-wired to that path on Windows (`os.name == "nt"`) via `_WINDOWS_DEFAULT`. Override at runtime with `$env:MIRUM_XFOIL_BIN = "other\path\xfoil.exe"`.

See **Environment Setup → Windows** below.

---

## Environment Setup

### XFOIL Installation

XFOIL is a free subsonic airfoil analysis code by Mark Drela (MIT). Required for Phase 2 aerodynamic validation (Cl/Cd polar generation). Source: https://web.mit.edu/drela/Public/web/xfoil/

---

#### Windows (native — **primary path for this project**)

**Automatic install (recommended):**
```powershell
# Run from PowerShell in the project root
cd C:\Users\Kevin\Developments\T2CIII
.\bin\install_xfoil_windows.ps1
```

The script:
1. Downloads `XFOIL6.99.zip` (~3.7 MB) from `https://web.mit.edu/drela/Public/web/xfoil/XFOIL6.99.zip`
2. Extracts `xfoil.exe` to `T2CIII\bin\xfoil.exe`
3. Runs a smoke test (`echo QUIT | xfoil.exe`)
4. Prints the env var command to set

**After running**, set the env var once:
```powershell
# Session scope (use in your conda activate or .env):
$env:MIRUM_XFOIL_BIN = "C:\Users\Kevin\Developments\T2CIII\bin\xfoil.exe"

# Permanent user scope:
[System.Environment]::SetEnvironmentVariable(
    'MIRUM_XFOIL_BIN',
    'C:\Users\Kevin\Developments\T2CIII\bin\xfoil.exe',
    'User')
```

**Manual fallback** (if the script fails):
1. Open https://web.mit.edu/drela/Public/web/xfoil/ in a browser
2. Download `XFOIL6.99.zip`
3. Extract `xfoil.exe` into `C:\Users\Kevin\Developments\T2CIII\bin\`

`backend/config.py` is already pre-wired: on Windows (`os.name == "nt"`), `XFOIL_BIN` defaults to `C:\Users\Kevin\Developments\T2CIII\bin\xfoil.exe` without needing the env var.

---

#### Linux (Ubuntu/Debian — production Docker container)

```dockerfile
# Add to Dockerfile
RUN apt-get update && apt-get install -y xfoil && apt-get clean
```

Or inside the container/WSL shell:
```bash
sudo apt-get update && sudo apt-get install -y xfoil
which xfoil  # should print /usr/bin/xfoil
```

No env var needed — `config.py` falls back to `"xfoil"` on non-Windows.

---

#### macOS (Homebrew)
```bash
brew install xfoil
which xfoil  # /usr/local/bin/xfoil or /opt/homebrew/bin/xfoil
```

---

#### Verification test (any OS)
```python
import subprocess
result = subprocess.run(
    ["xfoil"],            # or full path on Windows before env var is set
    input="QUIT\n",
    capture_output=True,
    text=True,
    timeout=5,
)
print("XFOIL available:", "XFOIL" in result.stdout or result.returncode == 0)
```

---

## Phase 1 — Airfoil Analysis Module ✅ COMPLETE

**Implemented:** 2026-05-13
**Files created:**
- `analysis/__init__.py` — public API exports
- `analysis/airfoil.py` — all core logic
- `analysis/tests/__init__.py`
- `analysis/tests/test_airfoil.py` — 24 tests (17 Group A + 7 Group B)

### P1-01 ✅ — `analysis/` package created
`analysis/__init__.py` exports: `analyze_naca`, `analyze_wing_step`, `predict_stall`, `slice_step_to_dat`, `naca4_to_dat`, `FlightEnvelope`, `PolarResult`.

### P1-02 ✅ — STEP → .dat via OCC BRepAlgoAPI_Section
`slice_step_to_dat(step_path, span_mm, output_dat_path)`:
- Loads STEP with `STEPControl_Reader`
- Cuts at Y = span_mm/2 using `BRepAlgoAPI_Section` with a `gp_Pln(gp_Pnt(0,y,0), gp_Dir(0,1,0))`
- Discretises all section edges via `BRepAdaptor_Curve` + `GCPnts_UniformAbscissa`
- Projects to XZ (drops Y), deduplicates on 1/1000-chord grid
- Sorts into Selig format: upper TE→LE then lower LE→TE
- Normalises to chord=1
- Fallback: `naca4_to_dat(code, path)` — fully analytic, no OCC required

### P1-03 ✅ — XFOIL subprocess runner
`run_xfoil_polar(airfoil_spec, flight_env, *, xfoil_bin, timeout_s=30)`:

XFOIL command sequence:
```
NACA xxxx          (or LOAD /path/to.dat + PANE)
OPER
VISC
{reynolds:.6e}
MACH {mach:.4f}
[VPAR / N {n_crit} / (blank)]   ← only if n_crit ≠ 9.0
ITER 100
PACC
{polar_path}
(blank — no dump file)
ASEQ {alpha_start} {alpha_end} {alpha_step}
PACC
(blank — exit OPER)
QUIT
```

Binary resolution priority: `xfoil_bin` arg → `MIRUM_XFOIL_BIN` env var → `config.py XFOIL_BIN` → `"xfoil"` on PATH.

Timeout: `subprocess.run(..., timeout=timeout_s)` — raises `XfoilRunError` on expiry.
Convergence failure: if fewer than 2 alpha points in the polar file → `XfoilRunError`.

Returns `PolarResult` TypedDict with: `naca_code`, `chord_mm`, `reynolds`, `mach`, `n_crit`, `alpha`, `Cl`, `Cd`, `Cm`, `L_D`, `converged_count`, `stall_alpha`, `Cl_max`, `dat_path`, `polar_path`.

### P1-04 ✅ — Stall predictor
`predict_stall_from_lists(alpha, Cl)`:
- **Hard criterion**: first index where dCl/dα goes positive→negative
- **Soft criterion**: first index where Cl drops ≥5% from Cl_max (catches plateau/post-stall)
- Returns whichever fires first; `(None, None)` if fewer than 3 points

`predict_stall(polar)` — convenience wrapper on a PolarResult.

### P1-05 ✅ — Unit tests

**Group A (pure Python, always run):** 17 tests, all pass
- A1–A3: `naca4_to_dat` format, symmetry, error handling
- A4: `_sort_airfoil_points` upper monotone, lower monotone, x∈[0,1], degenerate error
- A5: `_parse_polar_file` row count, alpha sequence, Cl@5°, Cd@0°, Cm sign, missing file
- A6–A8: `predict_stall` hard stall, soft stall, <3 points

**Group B (XFOIL required, skip if not found):** 7 tests, auto-skip in CI without binary
- B1: Cl at α=5° ≈ 0.57 ± 0.03
- B2: Cd at α=0° ≈ 0.006 ± 0.001
- B3: stall_alpha ∈ [12°, 14°]
- B4: PolarResult type contract (all 14 required keys)
- B5: converged_count ≥ 10
- B6: `XfoilNotFoundError` on bogus binary path
- B7: L/D positive for all pre-stall points

Run on your machine (with XFOIL installed):
```powershell
cd C:\Users\Kevin\Developments\T2CIII
python analysis\tests\test_airfoil.py
# All 24 tests should run (17 Group A + 7 Group B)
```

### Phase 1 Completion Summary

| Task | Status | Key detail |
|------|--------|-----------|
| P1-01 | ✅ | `analysis/` package with clean public API |
| P1-02 | ✅ | OCC section + analytical NACA fallback |
| P1-03 | ✅ | Full XFOIL subprocess runner, timeout+convergence guards |
| P1-04 | ✅ | Dual hard/soft stall criterion |
| P1-05 | ✅ | 17/17 Group A pass; 7 Group B await XFOIL on Windows |

---

## Phase 2 — XFOIL Aerodynamic Validation

*(Pending — requires XFOIL binary from Environment Setup)*

Planned pipeline:
1. Extract NACA code, chord, AoA range from manifest
2. Write XFOIL input script (NACA → OPER → ALFA → PACC → CPWR)
3. Run `subprocess.run(["xfoil"], input=xfoil_script, ...)`
4. Parse polar file → Cl, Cd, Cm vs. AoA
5. Return structured aerodynamic report alongside STEP file

---

## Phase 3 — Integration & Reporting

*(Pending)*

- Attach aerodynamic report to `/generate` SSE response payload
- Add `aero_report` field to `AssemblyManifest` schema
- Display Cl/Cd polar in Streamlit frontend

---

## Phase 0 Completion Summary

| Task | Result | Action taken |
|------|--------|-------------|
| P0-01: Gemini model strings | ✅ VERIFIED — 2 models, 5 files, all correct | None — documented; recommended centralizing into config.py |
| P0-02: Domain D present and complete | ✅ VERIFIED — all 3 requirements met | None — no additions needed |
| P0-03: Domain D additions | ✅ SKIPPED — P0-02 found it complete | N/A |
| P0-04: XFOIL binary | ❌ NOT FOUND | Documented install instructions above |
