"""QA verification loop for DA / DB / DF / DG new domains.

Tests three layers without needing a running FastAPI server:
  1. Classifier routing  — does the Gemini Lite model route each prompt to the
                           expected domain?
  2. Strategy injection  — do the right rule blocks fire for each domain?
  3. Machinist synthesis — does the Gemini Heavy model generate syntactically
                           valid Python/CadQuery code for each prompt?

Run from inside the backend Docker container (conda env, GEMINI_API_KEY_HEAVY set):

    python backend/tests/test_new_domains_qa.py
    python backend/tests/test_new_domains_qa.py --suite DA      # DA prompts only
    python backend/tests/test_new_domains_qa.py --suite DF DG   # multiple suites
    python backend/tests/test_new_domains_qa.py --concurrency 4 # parallel API calls
    python backend/tests/test_new_domains_qa.py --skip-machinist # skip code gen (fast)

Exit code: 0 if all tests pass, 1 if any fail.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

_BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_BACKEND))


# ---------------------------------------------------------------------------
# Test corpus — one entry per eval prompt, with expected routing + rule checks
# ---------------------------------------------------------------------------

@dataclass
class QACase:
    case_id: str        # renamed from `id` to avoid shadowing the builtin
    suite: str          # DA / DB / DF / DG
    description: str
    expected_domain: str
    expected_rules: list[str]   # must appear in get_active_rules output
    forbidden_rules: list[str] = field(default_factory=list)


QA_CASES: list[QACase] = [
    # ── DA: Swept / Delta / Canard / Dihedral ─────────────────────────
    QACase(
        case_id="D_swept_wing_35deg",
        suite="DA",
        description="NACA 2412 wing, 800mm span, 35-degree leading-edge sweep, root chord 250mm, tip chord 120mm, 4 ribs, front and rear spar.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_planform_variants"],
    ),
    QACase(
        case_id="D_delta_wing_naca0006",
        suite="DA",
        description="Delta wing using NACA 0006 profile, root chord 600mm, span 400mm, tip chord 15mm (delta planform), 3 spanwise ribs.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_planform_variants"],
    ),
    QACase(
        case_id="D_canard_surface",
        suite="DA",
        description="Canard surface, NACA 2409 profile, span 300mm, chord 80mm, 2 ribs, no sweep.",
        expected_domain="D",
        expected_rules=["rule_d_overview"],
    ),
    QACase(
        case_id="D_dihedral_wing",
        suite="DA",
        description="NACA 4412 wing, 800mm span, 200mm root chord, 150mm tip chord, 6-degree dihedral angle, 5 ribs, front spar at 25% chord.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_planform_variants"],
    ),
    QACase(
        case_id="D_swept_stabilizer",
        suite="DA",
        description="Horizontal stabilizer, NACA 0009 profile, 30-degree leading-edge sweep, 500mm span, 150mm root chord, 80mm tip chord, 2 spars.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_planform_variants"],
    ),
    QACase(
        case_id="D_swept_dihedral_combo",
        suite="DA",
        description="NACA 2412 wing, 600mm span, 25-degree leading-edge sweep, 4-degree dihedral, root chord 200mm, tip chord 100mm, 5 ribs, front and rear spar.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_planform_variants"],
    ),

    # ── DB: Twisted Blades ─────────────────────────────────────────────
    QACase(
        case_id="D_propeller_2blade",
        suite="DB",
        description="2-blade propeller, NACA 4412, root chord 60mm, tip chord 25mm, span 300mm, root pitch angle 30 degrees, tip pitch angle 12 degrees.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_twisted_blade"],
        forbidden_rules=["rule_domain_b"],
    ),
    QACase(
        case_id="D_propeller_3blade",
        suite="DB",
        description="3-blade propeller, NACA 2412, 400mm diameter (200mm blade span), root chord 50mm, tip chord 20mm, root pitch 25 degrees, tip pitch 8 degrees, hub diameter 40mm.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_twisted_blade"],
        forbidden_rules=["rule_domain_b"],
    ),
    QACase(
        case_id="D_rotor_blade",
        suite="DB",
        description="Helicopter rotor blade, NACA 0012, 1200mm span, root chord 80mm, tip chord 50mm, root pitch 10 degrees, tip pitch 4 degrees.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_twisted_blade"],
        forbidden_rules=["rule_domain_b"],
    ),
    QACase(
        case_id="D_wind_turbine_blade",
        suite="DB",
        description="Wind turbine blade, NACA 4418, 2000mm span, root chord 150mm, tip chord 60mm, root pitch 15 degrees, tip pitch 3 degrees.",
        expected_domain="D",
        expected_rules=["rule_d_overview", "rule_twisted_blade"],
        forbidden_rules=["rule_domain_b"],
    ),

    # ── DF: Propulsion (Domain F) ─────────────────────────────────────
    QACase(
        case_id="F_nozzle_basic",
        suite="DF",
        description="Rocket nozzle (De Laval), throat diameter 30mm, exit diameter 90mm, chamber radius 40mm, chamber length 80mm, wall thickness 4mm.",
        expected_domain="F",
        expected_rules=["rule_domain_f"],
        forbidden_rules=["rule_domain_b", "rule_domain_a"],
    ),
    QACase(
        case_id="F_nozzle_steep_angle",
        suite="DF",
        description="De Laval rocket nozzle, 15-degree diverging half-angle, 30-degree converging half-angle, throat radius 25mm, exit radius 75mm, chamber length 60mm, wall thickness 5mm.",
        expected_domain="F",
        expected_rules=["rule_domain_f"],
        forbidden_rules=["rule_domain_b"],
    ),
    QACase(
        case_id="F_combustion_chamber",
        suite="DF",
        description="Cylindrical combustion chamber, 60mm inner radius, 5mm wall thickness, 120mm length, 6 fuel inlet ports at 36mm radial position, 8mm inlet diameter.",
        expected_domain="F",
        expected_rules=["rule_domain_f"],
        forbidden_rules=["rule_domain_b"],
    ),
    QACase(
        case_id="F_chamber_with_dome",
        suite="DF",
        description="Thrust chamber with spherical injector dome, 50mm inner radius, 5mm wall, 100mm chamber length, spherical dome radius 55mm, 8 fuel inlet ports.",
        expected_domain="F",
        expected_rules=["rule_domain_f"],
        forbidden_rules=["rule_domain_b"],
    ),
    QACase(
        case_id="F_nacelle_inlet",
        suite="DF",
        description="Subsonic nacelle inlet cowl, highlight radius 120mm, throat radius 95mm, fan face radius 100mm, intake length 200mm, cowl thickness 10mm.",
        expected_domain="F",
        expected_rules=["rule_domain_f"],
        forbidden_rules=["rule_domain_a", "rule_domain_b"],
    ),
    QACase(
        case_id="F_afterburner_duct",
        suite="DF",
        description="Afterburner duct, 80mm inner radius, 83mm outer radius, 400mm length, simple hollow cylinder.",
        expected_domain="F",
        expected_rules=["rule_domain_f"],
        forbidden_rules=["rule_domain_a"],
    ),

    # ── DG: Vehicle Bodies (Domain G) ────────────────────────────────
    QACase(
        case_id="G_nose_cone_ogive",
        suite="DG",
        description="Ogive nose cone, 80mm base diameter, 200mm length, 2mm wall thickness, 20mm cylindrical shoulder.",
        expected_domain="G",
        expected_rules=["rule_domain_g"],
        forbidden_rules=["rule_domain_c"],
    ),
    QACase(
        case_id="G_nose_cone_haack",
        suite="DG",
        description="Von Karman (Haack series) nose cone, 100mm base diameter, 250mm length, solid, 25mm shoulder.",
        expected_domain="G",
        expected_rules=["rule_domain_g"],
        forbidden_rules=["rule_domain_c"],
    ),
    QACase(
        case_id="G_fuselage_simple",
        suite="DG",
        description="Cylindrical fuselage section, 500mm diameter, 1500mm length, 4mm wall thickness, 6 structural frame rings evenly spaced.",
        expected_domain="G",
        expected_rules=["rule_domain_g"],
        forbidden_rules=["rule_domain_a", "rule_domain_c"],
    ),
    QACase(
        case_id="G_fuselage_tapered",
        suite="DG",
        description="Aircraft fuselage section with 3 stations: nose 200x180mm at x=0, cabin 600x500mm at x=2000mm, tail 200x180mm at x=4000mm, 3mm wall thickness, 8 frame rings.",
        expected_domain="G",
        expected_rules=["rule_domain_g"],
        forbidden_rules=["rule_domain_c"],
    ),
    QACase(
        case_id="G_pod_body",
        suite="DG",
        description="Streamlined pod body, ogive nose 120mm base diameter 300mm long, cylindrical mid-section 120mm diameter 400mm long, 2mm wall thickness.",
        expected_domain="G",
        expected_rules=["rule_domain_g"],
        forbidden_rules=["rule_domain_c"],
    ),
]


# ---------------------------------------------------------------------------
# Test result container
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    case_id: str
    suite: str
    layer: str      # "classifier" | "strategy" | "machinist"
    passed: bool
    message: str
    elapsed_s: float = 0.0


# ---------------------------------------------------------------------------
# Layer 1: Classifier routing
# ---------------------------------------------------------------------------

async def test_classifier(case: QACase, sem: asyncio.Semaphore) -> TestResult:
    from classifier import classify_part
    t0 = time.monotonic()
    async with sem:
        try:
            result = await classify_part(case.description)
            actual = result.get("domain", "?")
            passed = actual == case.expected_domain
            msg = f"got domain={actual!r}" if passed else f"WRONG: got {actual!r}, want {case.expected_domain!r} | reason: {result.get('reasoning','')[:120]}"
        except Exception as e:
            passed = False
            msg = f"EXCEPTION: {e}"
    return TestResult(case.case_id, case.suite, "classifier", passed, msg, time.monotonic() - t0)


# ---------------------------------------------------------------------------
# Layer 2: Strategy rule injection (pure Python, no API)
# ---------------------------------------------------------------------------

def test_strategy(case: QACase) -> TestResult:
    from strategies import get_active_rules
    t0 = time.monotonic()
    try:
        rules = get_active_rules(case.expected_domain, case.description)
        missing = [r for r in case.expected_rules if r not in rules]
        forbidden_found = [r for r in case.forbidden_rules if r in rules]
        if missing:
            return TestResult(case.case_id, case.suite, "strategy", False,
                              f"missing rules: {missing}", time.monotonic() - t0)
        if forbidden_found:
            return TestResult(case.case_id, case.suite, "strategy", False,
                              f"forbidden rules injected: {forbidden_found}", time.monotonic() - t0)
        return TestResult(case.case_id, case.suite, "strategy", True,
                          f"rules OK: {rules}", time.monotonic() - t0)
    except Exception as e:
        return TestResult(case.case_id, case.suite, "strategy", False,
                          f"EXCEPTION: {e}", time.monotonic() - t0)


# ---------------------------------------------------------------------------
# Layer 3: Machinist code synthesis + syntax validation
# ---------------------------------------------------------------------------

async def test_machinist(case: QACase, sem: asyncio.Semaphore) -> TestResult:
    from agents import run_machinist
    from schemas import PartDefinition
    t0 = time.monotonic()
    async with sem:
        try:
            part = PartDefinition(
                part_id=case.case_id,
                description=case.description,
                material="aluminum",
                dimensions={},
                anchor_tags=[],
            )
            code, _step, _cert = await run_machinist(part, domain_override=case.expected_domain)
            # Syntax check the generated code
            try:
                ast.parse(code)
                syntax_ok = True
                syntax_msg = ""
            except SyntaxError as se:
                syntax_ok = False
                syntax_msg = f" | SyntaxError: {se}"

            # Check that the code uses the expected library helpers
            helper_checks = {
                "DA": ["make_planform_wires", "make_naca_wire"],  # at least one
                "DB": ["make_twisted_blade"],
                "DF": ["make_nozzle_profile", "make_combustion_chamber", "make_nacelle_cowl"],
                "DG": ["make_nose_cone", "make_fuselage_loft"],
            }
            expected_helpers = helper_checks.get(case.suite, [])
            used_helper = any(h in code for h in expected_helpers) if expected_helpers else True

            passed = syntax_ok and used_helper
            hints = []
            if not syntax_ok:
                hints.append(f"syntax error{syntax_msg}")
            if not used_helper:
                hints.append(f"none of {expected_helpers} found in output")
            msg = "OK" if passed else " | ".join(hints)
        except Exception as e:
            passed = False
            msg = f"EXCEPTION: {e}"
    return TestResult(case.case_id, case.suite, "machinist", passed, msg, time.monotonic() - t0)


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def _print_result(r: TestResult, verbose: bool = False) -> None:
    status = "PASS" if r.passed else "FAIL"
    detail = f" — {r.message}" if (not r.passed or verbose) else ""
    print(f"  [{status}] {r.layer:<12} {r.case_id}{detail}  ({r.elapsed_s:.1f}s)")


async def main() -> int:
    parser = argparse.ArgumentParser(description="QA verification for new aerospace domains")
    parser.add_argument("--suite", nargs="+", choices=["DA", "DB", "DF", "DG"],
                        default=["DA", "DB", "DF", "DG"], help="Suites to run")
    parser.add_argument("--skip-classifier", action="store_true")
    parser.add_argument("--skip-machinist", action="store_true")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    api_key = os.environ.get("GEMINI_API_KEY_HEAVY") or os.environ.get("GEMINI_API_KEY")
    if not api_key and not (args.skip_classifier and args.skip_machinist):
        print("ERROR: GEMINI_API_KEY_HEAVY not set. Export it or use --skip-classifier --skip-machinist")
        return 1

    if api_key:
        import google.generativeai as genai
        genai.configure(api_key=api_key)

    cases = [c for c in QA_CASES if c.suite in args.suite]
    print(f"\n{'='*60}")
    print(f"Mirum new-domain QA — {len(cases)} cases, suites: {args.suite}")
    print(f"{'='*60}\n")

    all_results: list[TestResult] = []
    sem = asyncio.Semaphore(args.concurrency)

    for suite_name in args.suite:
        suite_cases = [c for c in cases if c.suite == suite_name]
        if not suite_cases:
            continue
        print(f"── Suite {suite_name} ({len(suite_cases)} cases) ────────────────")

        # Layer 1: Classifier (async, parallel)
        if not args.skip_classifier:
            clf_tasks = [test_classifier(c, sem) for c in suite_cases]
            clf_results = await asyncio.gather(*clf_tasks)
            for r in clf_results:
                _print_result(r, args.verbose)
                all_results.append(r)

        # Layer 2: Strategy (sync, instant)
        for c in suite_cases:
            r = test_strategy(c)
            _print_result(r, args.verbose)
            all_results.append(r)

        # Layer 3: Machinist (async, parallel, expensive)
        if not args.skip_machinist:
            mach_tasks = [test_machinist(c, sem) for c in suite_cases]
            mach_results = await asyncio.gather(*mach_tasks)
            for r in mach_results:
                _print_result(r, args.verbose)
                all_results.append(r)

        print()

    # Summary
    total = len(all_results)
    passed = sum(1 for r in all_results if r.passed)
    failed = total - passed
    print(f"{'='*60}")
    print(f"Results: {passed}/{total} passed" + (f"  ({failed} FAILED)" if failed else " ✓"))

    if failed:
        print("\nFailed tests:")
        for r in all_results:
            if not r.passed:
                print(f"  {r.suite}/{r.layer}/{r.case_id}: {r.message}")

    print(f"{'='*60}\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
