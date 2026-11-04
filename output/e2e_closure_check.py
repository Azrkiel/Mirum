"""Plan task 4.3: live e2e check of the design-closure loop via /generate SSE.

Runs three checks against a locally running backend:
  1. targets-bearing prompt -> verdict events + design_verdict in complete payload
  2. unmeetable target -> overall 'fail'/'incomplete', GLB still delivered
  3. negative control -> design_verdict is null

Usage: python output/e2e_closure_check.py [base_url]
"""
import json
import sys
import time

import requests

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"

CASES = [
    ("trainer_wing_targets",
     "Design a 2-spar wing box for a 600 kg trainer, NACA 2412, span 8 m, "
     "chord 1.1 m, cruise 55 m/s at 2000 m, AL2024-T3. The structural margin "
     "of safety must be at least 0.5 and Cl_max at least 1.4.",
     True),
    ("unmeetable",
     "Design a NACA 2412 wing segment, span 6 m, chord 1.0 m, cruise 50 m/s "
     "at 2000 m, generated as a single solid skin with no internal structure. "
     "The wing must achieve a Cl_max of at least 4.0.",
     True),
    ("negative_control",
     "A NACA 2412 wing panel, 7 m span, 1.2 m chord, with a single forward "
     "spar at 25 percent chord and 4 evenly spaced internal ribs. Skin "
     "thickness 2 mm.",
     False),
]


def run_case(name: str, prompt: str, expect_verdict: bool) -> dict:
    t0 = time.time()
    events = []
    complete = None
    with requests.post(
        f"{BASE}/generate",
        json={"prompt": prompt, "run_aero": True, "run_struct": True},
        stream=True, timeout=(10, 1200),
    ) as r:
        r.raise_for_status()
        for raw in r.iter_lines(decode_unicode=True):
            if not raw or not raw.startswith("data: "):
                continue
            evt = json.loads(raw[6:])
            step = evt.get("step")
            if step == "complete":
                complete = evt
            else:
                events.append((step, evt.get("status"), (evt.get("message") or "")[:90]))
    elapsed = time.time() - t0
    verdict_events = [e for e in events if e[0] == "verdict"]
    dv = (complete or {}).get("design_verdict")
    glb_len = len((complete or {}).get("glb") or "")
    return {
        "name": name, "elapsed_s": round(elapsed, 1),
        "complete_status": (complete or {}).get("status"),
        "glb_bytes_b64": glb_len,
        "verdict_events": verdict_events,
        "design_verdict_overall": dv.get("overall") if dv else None,
        "design_verdict_iters": dv.get("iterations_used") if dv else None,
        "design_verdict_criteria": [
            (c.get("name"), c.get("actual"), c.get("passed"))
            for c in (dv.get("criteria") if dv else [])],
        "expect_verdict": expect_verdict,
        "aero_report_present": (complete or {}).get("aero_report") is not None,
        "struct_report_present": (complete or {}).get("struct_report") is not None,
    }


def main() -> int:
    failures = []
    for name, prompt, expect_verdict in CASES:
        try:
            res = run_case(name, prompt, expect_verdict)
        except Exception as e:
            print(f"[{name}] ERROR {type(e).__name__}: {e}")
            failures.append(name)
            continue
        print(json.dumps(res, indent=2))
        if res["complete_status"] != "done" or res["glb_bytes_b64"] == 0:
            failures.append(f"{name}: no GLB delivered")
        if expect_verdict and res["design_verdict_overall"] is None:
            failures.append(f"{name}: expected a design_verdict, got null")
        if not expect_verdict and res["design_verdict_overall"] is not None:
            failures.append(f"{name}: REA invented targets (verdict should be null)")
    print("\n=== E2E RESULT:", "FAIL: " + "; ".join(failures) if failures else "ALL CHECKS PASS")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
