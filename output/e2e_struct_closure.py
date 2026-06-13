"""4.3 addendum: drive the LIVE struct path — evaluated ms_min criterion,
and if the margin fails, the analytic resize + re-analysis inside closure."""
import json
import sys

import requests

PROMPT = (
    "Design a 2-spar wing box for a 1200 kg utility-category light aircraft "
    "cruising at 60 m/s at 3000 m altitude. Reference wing area 16 m^2, span "
    "10.7 m, NACA 2412. Wing box: 1.8 m root chord, 12 percent "
    "thickness-to-chord, spar cap area 0.00025 m^2 per cap, shear web "
    "thickness 1.5 mm, AL2024-T3. The structural margin of safety must be "
    "at least 0.5."
)

complete = None
events = []
with requests.post(
    "http://127.0.0.1:8010/generate",
    json={"prompt": PROMPT, "run_struct": True},
    stream=True, timeout=(10, 1200),
) as r:
    r.raise_for_status()
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data: "):
            continue
        evt = json.loads(raw[6:])
        if evt.get("step") == "complete":
            complete = evt
        elif evt.get("step") in ("struct", "verdict"):
            events.append((evt.get("step"), evt.get("status"),
                           (evt.get("message") or "")[:110]))

dv = (complete or {}).get("design_verdict")
sr = (complete or {}).get("struct_report")
print(json.dumps({
    "events": events,
    "glb_delivered": bool((complete or {}).get("glb")),
    "design_verdict": dv,
    "struct_margins": ({"bending_ms": sr.get("bending_margin_of_safety"),
                        "shear_ms": sr.get("shear_margin_of_safety")}
                       if sr else None),
}, indent=2))
ok = dv is not None and any(c["name"] == "ms_min" and c["actual"] is not None
                            for c in dv.get("criteria", []))
print("\n=== STRUCT E2E:", "EVALUATED" if ok else "STILL UNEVALUATED")
sys.exit(0 if ok else 1)
