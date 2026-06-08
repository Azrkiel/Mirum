"""
Text-to-CAD Streamlit Frontend.

Form-based UI with prompt input, CadQuery code viewer, and 3D model viewer.
"""

import base64
import json
import os

import requests
import streamlit as st
import streamlit.components.v1 as components
from streamlit_ace import st_ace

st.set_page_config(page_title="Text-to-CAD", page_icon="\U0001f529", layout="wide")

# Reduce top padding for a tighter layout
st.markdown(
    "<style>.block-container{padding-top:1.5rem;}</style>",
    unsafe_allow_html=True,
)

BACKEND_URL = os.environ.get("BACKEND_URL", "http://backend:8000")

STEP_LABELS = {
    "planner": "Planning blueprint",
    "assembler": "Compiling assembly",
    "export": "Exporting .glb",
    "script": "CadQuery code captured",
    "complete": "Model ready",
}

# ── Session State ───────────────────────────────────────────
for key, default in [
    ("script_code", ""),
    ("glb_bytes", None),
    ("status", "Ready"),
    ("script_version", 0),
    ("show_plan", False),
    ("pending_plan", None),
    ("pending_prompt", ""),
    ("show_clarification", False),
    ("pending_clarifications", []),
    ("clarification_prompt", ""),
    ("parameters", {}),
    ("last_manifest", None),   # T2-08: for refinement
    ("last_scripts", {}),      # T2-08: for refinement
    ("run_aero", False),       # Phase 3: opt-in XFOIL polar
    ("aero_report", None),     # Phase 3: PolarReport dict from /generate complete event
    ("aero_plots", None),      # T2-16: cached {cl_alpha, cd_alpha, cp_dist} base64 PNGs
    ("xfoil_pdf_bytes", None),      # PDF-04: cached PDF bytes for "Download XFOIL Results"
    ("geometry_certificate", None),  # G-05: GeometryCertificate dict from /generate
    ("struct_report", None),         # Phase 6: StructuralReport dict from /generate complete event
]:
    if key not in st.session_state:
        st.session_state[key] = default


# ── Helpers ─────────────────────────────────────────────────

def _step_label(step: str) -> str:
    if step in STEP_LABELS:
        return STEP_LABELS[step]
    if step.startswith("machinist:"):
        return f"Building '{step.split(':', 1)[1]}'"
    return step


def _icon(status: str) -> str:
    return {"running": "\u23f3", "done": "\u2705", "error": "\u274c"}.get(status, "\u2b1c")


_VIEWER_TEMPLATE = """<!doctype html>
<html><head>
<meta charset="utf-8">
<style>
  html, body { margin: 0; padding: 0; width: 100%; height: 100%; overflow: hidden; background: #1a1a2e; }
  #renderCanvas { width: 100%; height: 100%; display: block; outline: none; touch-action: none; }
</style>
<script src="https://cdn.babylonjs.com/babylon.js"></script>
<script src="https://cdn.babylonjs.com/loaders/babylonjs.loaders.min.js"></script>
<script src="https://cdn.babylonjs.com/materialsLibrary/babylonjs.materials.min.js"></script>
</head><body>
<canvas id="renderCanvas"></canvas>
<script>
(async () => {
  const canvas = document.getElementById("renderCanvas");
  let engine, scene, camera, ground, gridMat;
  let loadedMeshes = [];

  async function initEngine() {
    try {
      if (BABYLON.WebGPUEngine && (await BABYLON.WebGPUEngine.IsSupportedAsync)) {
        const e = new BABYLON.WebGPUEngine(canvas, { antialias: true });
        await e.initAsync();
        return e;
      }
    } catch (err) {
      console.warn("WebGPU init failed, falling back to WebGL2:", err);
    }
    return new BABYLON.Engine(canvas, true, { preserveDrawingBuffer: true, stencil: true });
  }

  function createGround(s) {
    const g = BABYLON.MeshBuilder.CreateGround("ground", { width: 50, height: 50 }, s);
    const m = new BABYLON.GridMaterial("grid", s);
    m.majorUnitFrequency = 10;
    m.minorUnitVisibility = 0.45;
    m.gridRatio = 1;
    m.mainColor = new BABYLON.Color3(0.10, 0.10, 0.18);
    m.lineColor = new BABYLON.Color3(0.40, 0.40, 0.55);
    m.opacity = 0.98;
    g.material = m;
    return [g, m];
  }

  function buildScene() {
    const s = new BABYLON.Scene(engine);
    s.clearColor = BABYLON.Color4.FromHexString("#1a1a2eff");
    camera = new BABYLON.ArcRotateCamera(
      "cam", -Math.PI / 3, Math.PI / 3, 10, BABYLON.Vector3.Zero(), s
    );
    camera.attachControl(canvas, true);
    camera.wheelDeltaPercentage = 0.01;
    camera.panningSensibility = 50;
    new BABYLON.HemisphericLight("hemi", new BABYLON.Vector3(0, 1, 0), s);
    [ground, gridMat] = createGround(s);
    return s;
  }

  async function updateViewer(blob) {
    for (const m of loadedMeshes) m.dispose(false, true);
    loadedMeshes = [];
    const url = URL.createObjectURL(blob);
    try {
      const before = new Set(scene.meshes);
      await BABYLON.SceneLoader.AppendAsync("", url, scene, null, ".glb");
      loadedMeshes = scene.meshes.filter(m => !before.has(m) && m !== ground);
    } finally {
      URL.revokeObjectURL(url);
    }
    for (const m of loadedMeshes) {
      const pbr = new BABYLON.PBRMaterial("pbr_" + m.name, scene);
      pbr.albedoColor = new BABYLON.Color3(0.78, 0.78, 0.82);
      pbr.metallic = 1.0;
      pbr.roughness = 0.3;
      m.material = pbr;
    }
    // Dispose ground before framing — getWorldExtends ignores setEnabled(false), so
    // a hidden 50x50 ground would still dominate the bounding box and push the camera out.
    ground.dispose();
    gridMat.dispose();
    scene.createDefaultCameraOrLight(true, true, true);
    [ground, gridMat] = createGround(scene);
  }

  engine = await initEngine();
  scene = buildScene();
  engine.runRenderLoop(() => scene.render());
  window.addEventListener("resize", () => engine.resize());

  const b64 = "__GLB_B64__";
  const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
  const blob = new Blob([bytes], { type: "model/gltf-binary" });
  await updateViewer(blob);
})();
</script>
</body></html>"""


def render_glb_viewer(glb_bytes: bytes, height: int = 500) -> None:
    assert isinstance(height, int) and 100 <= height <= 2000
    b64 = base64.b64encode(glb_bytes).decode("ascii")
    html = _VIEWER_TEMPLATE.replace("__GLB_B64__", b64)
    components.html(html, height=height + 20)


def _progress_md(steps: list[dict]) -> str:
    lines = []
    for s in steps:
        if s["step"] == "complete":
            continue
        icon = _icon(s["status"])
        label = _step_label(s["step"])
        detail = s.get("detail", "")
        line = f"{icon} **{label}**"
        if detail and s["status"] != "done":
            line += f"  \n&nbsp;&nbsp;&nbsp;&nbsp;_{detail}_"
        lines.append(line)
    return "\n\n".join(lines)


def stream_pipeline(prompt: str, progress_ph) -> None:
    """Stream SSE events from /generate, updating session state."""
    steps, idx = [], {}
    script_code, glb_bytes = "", None
    error_message = ""
    params = {}
    aero_report = None
    struct_report = None  # Phase 6: structural & flight-envelope report
    geometry_cert = None  # G-05: geometry certificate
    # Phase 3: opt-in aerodynamic analysis (checkbox state lives in session)
    run_aero = bool(st.session_state.get("run_aero", False))

    try:
        with requests.post(
            f"{BACKEND_URL}/generate",
            json={"prompt": prompt, "run_aero": run_aero},
            stream=True,
            timeout=300,
        ) as resp:
            if resp.status_code != 200:
                try:
                    detail = resp.json().get("detail", resp.text)
                except Exception:
                    detail = resp.text
                st.session_state.status = f"Error (HTTP {resp.status_code})"
                st.error(f"Generation failed:\n\n```\n{detail}\n```")
                return

            st.session_state.status = "Generating..."

            for raw in resp.iter_lines(decode_unicode=True):
                if not raw or not raw.startswith("data: "):
                    continue
                ev = json.loads(raw[6:])
                step, status = ev["step"], ev["status"]

                if step == "complete":
                    if status == "done":
                        glb_bytes = base64.b64decode(ev.get("glb", ""))
                        params = ev.get("parameters", {})
                        aero_report = ev.get("aero_report")  # Phase 3
                        struct_report = ev.get("struct_report")  # Phase 6
                        geometry_cert = ev.get("geometry_certificate")  # G-05
                        # T2-08: store manifest for refinement
                        if ev.get("manifest"):
                            st.session_state.last_manifest = ev["manifest"]
                        if ev.get("scripts"):
                            st.session_state.last_scripts = ev["scripts"]
                    elif status == "error":
                        error_message = ev.get("message", "")
                    script_code = ev.get("script", script_code)
                    continue
                if step == "script" and status == "done":
                    script_code = ev.get("detail", "")

                if step in idx:
                    steps[idx[step]] = ev
                else:
                    idx[step] = len(steps)
                    steps.append(ev)
                progress_ph.markdown(_progress_md(steps))

    except requests.ConnectionError:
        st.session_state.status = "Connection error"
        st.error("Could not connect to backend. Is it running?")
        return
    except requests.Timeout:
        st.session_state.status = "Timeout"
        st.error("Request timed out.")
        return

    st.session_state.script_code = script_code
    st.session_state.glb_bytes = glb_bytes
    st.session_state.parameters = params
    st.session_state.aero_report = aero_report  # Phase 3
    st.session_state.struct_report = struct_report  # Phase 6
    st.session_state.aero_plots = None          # T2-16: reset so plots are re-fetched
    st.session_state.xfoil_pdf_bytes = None    # PDF-04: reset so PDF is re-fetched
    st.session_state.geometry_certificate = geometry_cert  # G-05
    if script_code:
        st.session_state.script_version += 1

    has_error = any(s["status"] == "error" for s in steps)
    if has_error:
        err = next(s for s in steps if s["status"] == "error")
        st.session_state.status = f"Error: {_step_label(err['step'])}"
        if error_message:
            st.error(
                f"CadQuery compilation failed:\n\n```\n{error_message}\n```"
            )
        progress_ph.markdown(_progress_md(steps))
    else:
        st.session_state.status = "Ready"
        progress_ph.empty()


def run_script(script: str) -> None:
    """Execute CadQuery code via the /run endpoint."""
    st.session_state.status = "Running..."
    try:
        data = requests.post(
            f"{BACKEND_URL}/run", json={"script": script}, timeout=120
        ).json()
        if data.get("status") == "error":
            st.session_state.status = "Run failed"
            st.error(f"Script execution failed:\n\n```\n{data.get('detail', 'Unknown error')}\n```")
            return
        glb_b64 = data.get("glb")
        if not glb_b64:
            st.session_state.status = "Run failed"
            st.error(
                f"Script returned no geometry. Backend response:\n\n```\n{data.get('detail', data)}\n```"
            )
            return
        st.session_state.glb_bytes = base64.b64decode(glb_b64)
        # Manual script re-run changes geometry — clear stale aero data so the
        # PerformanceCard doesn't show a polar that doesn't match the new model.
        st.session_state.aero_report = None
        st.session_state.aero_plots = None
        st.session_state.xfoil_pdf_bytes = None  # PDF-04
        st.session_state.status = "Ready"
    except requests.ConnectionError:
        st.session_state.status = "Connection error"
        st.error("Could not connect to backend.")
    except requests.Timeout:
        st.session_state.status = "Timeout"
        st.error("Script execution timed out.")


def fetch_clarifications(prompt: str) -> list[str]:
    """Call /clarify and return ambiguity questions (may be empty)."""
    try:
        resp = requests.post(
            f"{BACKEND_URL}/clarify",
            json={"prompt": prompt},
            timeout=30,
        )
        return resp.json().get("ambiguities", [])
    except Exception:
        return []


def fetch_plan(prompt: str) -> dict | None:
    """Call /plan endpoint and return the manifest dict, or None on error."""
    try:
        resp = requests.post(
            f"{BACKEND_URL}/plan",
            json={"prompt": prompt},
            timeout=180,
        )
        data = resp.json()
        if data.get("status") == "ok":
            return data
        st.error(f"Planning failed: {data.get('detail', 'Unknown error')}")
    except Exception as e:
        st.error(f"Could not reach backend: {e}")
    return None


def render_plan_review(plan: dict) -> bool:
    """Show the assembly plan for user review. Returns True if user approves."""
    st.subheader(f"📋 Assembly Plan — {plan['assembly_name']}")
    st.caption(f"{plan['part_count']} part(s) · {'Single part' if plan['is_single_part'] else 'Assembly'}")

    if not plan["is_single_part"]:
        st.markdown("**Parts to generate:**")
        for p in plan["parts"]:
            st.markdown(f"- **{p['part_id']}** — _{p['description'][:120]}_")

        st.markdown("**Mating rules:**")
        for r in plan["mating_rules"]:
            if r["source_part_id"] == r["target_part_id"]:
                continue  # skip self-referencing
            mate = r.get("mate_type") or "translation"
            src = f"{r['source_part_id']}@{r['source_anchor']}"
            tgt = f"{r['target_part_id']}@{r['target_anchor']}"
            st.markdown(f"- `{src}` ↔ `{tgt}` · **{mate}**")

    col_ok, col_cancel = st.columns([1, 1])
    with col_ok:
        if st.button("✅ Generate", type="primary", use_container_width=True, key="plan_approve"):
            return True
    with col_cancel:
        if st.button("✗ Cancel", use_container_width=True, key="plan_cancel"):
            st.session_state.show_plan = False
            st.rerun()
    return False


# ── T2-16: Aerodynamic PerformanceCard ─────────────────────

def _fetch_aero_plots(report: dict) -> dict | None:
    """POST to /analysis/plots and return {cl_alpha, cd_alpha, cp_dist} or None."""
    naca_code = report.get("naca_code", "0012")
    reynolds = float(report.get("reynolds") or 1_000_000.0)
    mach = float(report.get("mach") or 0.0)
    n_crit = float(report.get("n_crit") or 9.0)
    # Use 60% of stall alpha as the design point for the Cp plot
    stall_alpha = report.get("stall_alpha")
    design_alpha = round(stall_alpha * 0.60, 1) if stall_alpha else 4.0
    design_alpha = max(-5.0, min(design_alpha, 20.0))  # clamp
    try:
        resp = requests.post(
            f"{BACKEND_URL}/analysis/plots",
            json={
                "naca_code": naca_code,
                "polar": report,
                "design_alpha": design_alpha,
                "reynolds": reynolds,
                "mach": mach,
                "n_crit": n_crit,
            },
            timeout=30,
        )
        data = resp.json()
        if data.get("status") == "ok":
            return data
        st.warning(f"Polar plot generation failed: {data.get('detail', '')}")
    except requests.ConnectionError:
        st.warning("Could not reach backend for polar plots.")
    except Exception as _e:
        st.warning(f"Polar plot request failed: {_e}")
    return None


def _fetch_xfoil_pdf(report: dict) -> bytes | None:
    """POST to /analysis/pdf and return raw PDF bytes, or None on failure.

    Uses the same request body as _fetch_aero_plots so the server generates
    a fresh set of charts and assembles the PDF in one round-trip.
    """
    naca_code = report.get("naca_code", "0012")
    reynolds = float(report.get("reynolds") or 1_000_000.0)
    mach = float(report.get("mach") or 0.0)
    n_crit = float(report.get("n_crit") or 9.0)
    stall_alpha = report.get("stall_alpha")
    design_alpha = round(stall_alpha * 0.60, 1) if stall_alpha else 4.0
    design_alpha = max(-5.0, min(design_alpha, 20.0))
    try:
        resp = requests.post(
            f"{BACKEND_URL}/analysis/pdf",
            json={
                "naca_code": naca_code,
                "polar": report,
                "design_alpha": design_alpha,
                "reynolds": reynolds,
                "mach": mach,
                "n_crit": n_crit,
            },
            timeout=45,
        )
        if resp.status_code == 200 and resp.headers.get("content-type", "").startswith("application/pdf"):
            return resp.content
        st.warning(f"PDF generation failed (HTTP {resp.status_code})")
    except requests.ConnectionError:
        st.warning("Could not reach backend for PDF export.")
    except Exception as _e:
        st.warning(f"PDF request failed: {_e}")
    return None


def _render_geometry_cert_card(cert: dict) -> None:
    """Render a collapsible geometry certificate card.

    Shows pass/fail status, volume, surface area, mass (if available),
    and per-check details in a Streamlit expander.
    """
    passed = cert.get("passed", False)
    icon = "✅" if passed else "❌"
    label = f"{icon} Geometry Certificate — {'PASSED' if passed else 'FAILED'}"

    with st.expander(label, expanded=not passed):
        # Key metrics row
        col1, col2, col3 = st.columns(3)
        with col1:
            vol = cert.get("volume_mm3")
            st.metric("Volume", f"{vol:,.1f} mm³" if vol is not None else "—")
        with col2:
            sa = cert.get("surface_area_mm2")
            st.metric("Surface Area", f"{sa:,.1f} mm²" if sa is not None else "—")
        with col3:
            mass_g = cert.get("mass_g")
            mat = cert.get("material", "")
            if mass_g is not None:
                st.metric("Mass", f"{mass_g:,.2f} g", delta=mat or None)
            else:
                st.metric("Mass", "—")

        # Bounding box
        bb = cert.get("bounding_box")
        if bb:
            st.caption(
                f"Bounding box: {bb.get('x_mm', 0):.1f} × "
                f"{bb.get('y_mm', 0):.1f} × "
                f"{bb.get('z_mm', 0):.1f} mm"
            )

        # Per-check table
        checks = cert.get("checks", {})
        if checks:
            check_labels = {
                "volume_positive": "Volume > 0",
                "surface_area_positive": "Surface area > 0",
                "no_degenerate_faces": "No degenerate faces",
                "bounding_box_finite": "Bounding box finite",
                "brep_valid": "BRep topology valid",
                "is_solid": "Shape is solid",
            }
            rows = []
            for key, label_text in check_labels.items():
                if key in checks:
                    rows.append(
                        f"{'✅' if checks[key] else '❌'} {label_text}"
                    )
            st.markdown("\n".join(rows))

        # Failures
        failures = cert.get("failures", [])
        if failures:
            st.error("**Failures:**\n" + "\n".join(f"- {f}" for f in failures))

        # Multi-part note
        if cert.get("part_count", 1) > 1:
            st.caption(f"Aggregate of {cert['part_count']} parts")


def _render_structural_card(report: dict) -> None:
    """Render the Flight-Envelope (V-n) + Structural-Margins cards.

    Sources every value from the StructuralReport dict shipped on the /generate
    `complete` event (Phase 6); renders nothing extra when fields are absent.
    """
    st.markdown("### 🛩️ Flight Envelope")
    try:
        from vn_diagram import vn_diagram_figure
        fig = vn_diagram_figure(report)
        st.pyplot(fig, use_container_width=True)
        import matplotlib.pyplot as _plt
        _plt.close(fig)
    except Exception:
        st.caption("_V-n diagram unavailable_")

    st.markdown("### 🧱 Structural Margins")
    passed = bool(report.get("passed"))
    st.markdown(("✅ **PASS**" if passed else "❌ **FAIL**")
                + f" — material **{report.get('material', '—')}**")

    bms = report.get("bending_margin_of_safety")
    sms = report.get("shear_margin_of_safety")
    c1, c2 = st.columns(2)
    c1.metric("Bending MS", f"{bms:+.3f}" if bms is not None else "—",
              delta="ok" if report.get("bending_passed") else "fail")
    c2.metric("Shear MS", f"{sms:+.3f}" if sms is not None else "—",
              delta="ok" if report.get("shear_passed") else "fail")

    rbm = report.get("root_bending_moment_nm")
    rsf = report.get("root_shear_force_n")
    c3, c4 = st.columns(2)
    c3.metric("Root bending", f"{rbm / 1e3:,.1f} kN·m" if rbm is not None else "—")
    c4.metric("Root shear", f"{rsf / 1e3:,.1f} kN" if rsf is not None else "—")

    nup = report.get("n_ultimate_pos")
    vne = report.get("v_ne_ms")
    c5, c6 = st.columns(2)
    c5.metric("n ultimate +", f"{nup:.2f}" if nup is not None else "—")
    c6.metric("V_NE", f"{vne:.0f} m/s" if vne is not None else "—")

    warnings = report.get("warnings") or []
    if warnings:
        st.caption("⚠ " + "; ".join(str(w) for w in warnings))


def _render_performance_card(report: dict) -> None:
    """Render the aerodynamic PerformanceCard into the current Streamlit column.

    Shows four key metrics, then three matplotlib polar charts (Cl, Cd, Cp)
    fetched from /analysis/plots, and a CSV download button.
    """
    naca = report.get("naca_code", "?")
    Re = float(report.get("reynolds") or 0)
    Cl_max = report.get("Cl_max")
    stall_alpha = report.get("stall_alpha")

    st.markdown("### ✈️ Aero Performance")

    m1, m2 = st.columns(2)
    m1.metric("Airfoil", f"NACA {naca}")
    m2.metric("Re", f"{Re / 1e6:.2f}M" if Re else "n/a")
    m3, m4 = st.columns(2)
    m3.metric("Cl_max",
              f"{Cl_max:.3f}" if Cl_max is not None else "n/a")
    m4.metric("Stall α",
              f"{stall_alpha:.1f}°" if stall_alpha is not None else "n/a")

    st.markdown("---")

    # Fetch (or retrieve cached) plot images.
    # aero_plots is None → not yet fetched; False → fetch failed (sentinel to
    # prevent re-fetching on every Streamlit interaction); dict → success.
    plots = st.session_state.get("aero_plots")
    if plots is None:
        with st.spinner("Generating charts…"):
            plots = _fetch_aero_plots(report)
            # Store False as a failure sentinel so we don't retry on every
            # subsequent interaction (which would hang for 30 s each time).
            st.session_state.aero_plots = plots if plots is not None else False

    if plots:
        cl_b64 = plots.get("cl_alpha")
        cd_b64 = plots.get("cd_alpha")
        cp_b64 = plots.get("cp_dist")

        if cl_b64:
            st.image(base64.b64decode(cl_b64), use_container_width=True)
        if cd_b64:
            st.image(base64.b64decode(cd_b64), use_container_width=True)
        if cp_b64:
            st.image(base64.b64decode(cp_b64), use_container_width=True)
        else:
            st.caption("_Cp distribution unavailable (XFOIL not found)_")
    elif plots is False:
        st.caption("_Charts unavailable — backend returned an error_")
    else:
        st.caption("_Charts unavailable_")

    # CSV download
    import pandas as _pd
    alpha_vals = report.get("alpha", [])
    if alpha_vals:
        polar_df = _pd.DataFrame({
            "α (deg)": alpha_vals,
            "Cl": report.get("Cl", []),
            "Cd": report.get("Cd", []),
            "Cm": report.get("Cm", []),
            "L/D": report.get("L_D", []),
        }).set_index("α (deg)")
        st.download_button(
            "⬇ Download polar CSV",
            polar_df.to_csv().encode("utf-8"),
            f"polar_naca{naca}.csv",
            "text/csv",
            key="polar_csv_download",
        )

    # PDF download — click-triggered fetch, sentinel False on failure (not None)
    re_val = float(report.get("reynolds") or 0)
    pdf_cached = st.session_state.get("xfoil_pdf_bytes")  # None=unfetched, False=failed, bytes=ready

    if pdf_cached is False:
        st.caption("_PDF unavailable — backend returned an error_")
        if st.button("Retry PDF", key="xfoil_pdf_retry"):
            st.session_state.xfoil_pdf_bytes = None
            st.rerun()
    elif pdf_cached is None:
        if st.button("📄 Generate PDF Report", key="xfoil_pdf_generate"):
            with st.spinner("Building PDF report… (~10s)"):
                pdf_bytes = _fetch_xfoil_pdf(report)
                # Store False as failure sentinel so we don't retry on every rerun
                st.session_state.xfoil_pdf_bytes = pdf_bytes if pdf_bytes else False
            st.rerun()
    else:
        st.download_button(
            "⬇ Download XFOIL Simulation Results",
            pdf_cached,
            f"mirum_naca{naca}_{re_val:.0e}.pdf",
            "application/pdf",
            key="xfoil_pdf_download",
        )


# ── Page Layout ─────────────────────────────────────────────

# 1) Prompt
st.markdown("**Describe your 3D model**")
prompt = st.text_area(
    "prompt",
    placeholder="A smooth, ergonomic shell for a computer mouse, roughly 120mm long and 60mm wide.",
    label_visibility="collapsed",
    height=80,
)

# 2) Options + Generate button
st.checkbox(
    "Run aerodynamic analysis (Domain D wings only — adds ~30s)",
    key="run_aero",
    help=(
        "Opt-in. When the assembly is classified as Domain D (aerospace) and "
        "has a NACA airfoil + chord + span, the backend slices the wing STEP "
        "at mid-span and runs XFOIL for Cl/Cd/Cm vs angle of attack. Failure "
        "is non-blocking — the GLB is delivered either way."
    ),
)
generate = st.button(
    "Generate (Ctrl+Enter)", type="primary", use_container_width=True
)

# 3) Progress area (populated during streaming)
progress_ph = st.empty()

if generate and prompt.strip():
    p = prompt.strip()
    # Step 1: Clarification (optional, fast)
    ambs = fetch_clarifications(p)
    if ambs:
        st.session_state.pending_clarifications = ambs
        st.session_state.clarification_prompt = p
        st.session_state.show_clarification = True
        st.rerun()
    else:
        # No ambiguities — go straight to plan/generate
        plan = fetch_plan(p)
        if plan:
            if plan["is_single_part"]:
                # Single part — skip review, generate immediately
                stream_pipeline(p, progress_ph)
            else:
                st.session_state.show_plan = True
                st.session_state.pending_plan = plan
                st.session_state.pending_prompt = p
                st.rerun()

# Show clarification form if pending
if st.session_state.get("show_clarification") and st.session_state.get("pending_clarifications"):
    st.info("**Optional: Answer these to improve accuracy** _(or click Skip)_")
    answers = {}
    for q in st.session_state.pending_clarifications:
        answers[q] = st.text_input(q, key=f"clarify_{hash(q)}", placeholder="Optional")

    col_skip, col_cont = st.columns([1, 2])
    with col_skip:
        if st.button("Skip", key="clarify_skip"):
            st.session_state.show_clarification = False
            plan = fetch_plan(st.session_state.clarification_prompt)
            if plan:
                if plan["is_single_part"]:
                    stream_pipeline(st.session_state.clarification_prompt, progress_ph)
                else:
                    st.session_state.show_plan = True
                    st.session_state.pending_plan = plan
                    st.session_state.pending_prompt = st.session_state.clarification_prompt
                    st.rerun()
    with col_cont:
        if st.button("Continue with answers", type="primary", key="clarify_cont"):
            # Append answers to prompt
            enriched = st.session_state.clarification_prompt
            filled = [f"{q}: {a}" for q, a in answers.items() if a.strip()]
            if filled:
                enriched += "\n\nAdditional context:\n" + "\n".join(filled)
            st.session_state.show_clarification = False
            plan = fetch_plan(enriched)
            if plan:
                if plan["is_single_part"]:
                    stream_pipeline(enriched, progress_ph)
                else:
                    st.session_state.show_plan = True
                    st.session_state.pending_plan = plan
                    st.session_state.pending_prompt = enriched
                    st.rerun()

if st.session_state.show_plan and st.session_state.pending_plan:
    approved = render_plan_review(st.session_state.pending_plan)
    if approved:
        st.session_state.show_plan = False
        stream_pipeline(st.session_state.pending_prompt, progress_ph)

# 4) 3D viewer + PerformanceCard (T2-16: side-by-side when aero_report present)
if st.session_state.glb_bytes:
    aero_report = st.session_state.get("aero_report")
    if aero_report:
        # Side-by-side: viewer ~60 %, performance card ~40 %
        viewer_col, card_col = st.columns([3, 2], gap="medium")
        with viewer_col:
            render_glb_viewer(st.session_state.glb_bytes, height=460)
            st.download_button(
                "⬇ Download .glb",
                st.session_state.glb_bytes,
                "assembly.glb",
                "model/gltf-binary",
                key="glb_download_1",
            )
        with card_col:
            _render_performance_card(aero_report)
    else:
        # No aero report — full-width viewer
        render_glb_viewer(st.session_state.glb_bytes)
        st.download_button(
            "⬇ Download .glb",
            st.session_state.glb_bytes,
            "assembly.glb",
            "model/gltf-binary",
            key="glb_download_2",
        )

    # G-06: geometry certificate collapsible card (shown below viewer always)
    geo_cert = st.session_state.get("geometry_certificate")
    if geo_cert:
        _render_geometry_cert_card(geo_cert)

    # Phase 6: structural & flight-envelope cards (Domain D, opt-in run_struct)
    struct_report = st.session_state.get("struct_report")
    if struct_report:
        with st.expander("🛩️ Flight Envelope & Structural Margins", expanded=True):
            _render_structural_card(struct_report)

# Parameter editor sidebar (T1-06)
if st.session_state.parameters:
    st.sidebar.subheader("⚙️ Parameters")
    st.sidebar.caption("Edit and click Run to update model")
    updated_params = {}
    for name, value in st.session_state.parameters.items():
        updated_params[name] = st.sidebar.number_input(
            name,
            value=float(value),
            step=0.5,
            format="%.1f",
            key=f"param_{name}",
        )
    if st.sidebar.button("Apply Parameters", key="apply_params"):
        # Substitute values back into the script
        script = st.session_state.script_code
        for name, value in updated_params.items():
            # Replace "NAME = old_value" at top of script
            import re as _re
            script = _re.sub(
                rf"^{name}\s*=\s*[\d.]+",
                f"{name} = {value:.1f}",
                script,
                flags=_re.MULTILINE,
            )
        st.session_state.script_code = script
        st.session_state.script_version += 1
        run_script(script)
        st.rerun()

# 5) CadQuery Code header row
col_label, col_run = st.columns([5, 1])
with col_label:
    st.markdown("**CadQuery Code**")
with col_run:
    run_clicked = st.button("Run (Ctrl+Enter)")

# 6) Editable code editor (always visible)
edited_code = st_ace(
    value=st.session_state.script_code or "",
    language="python",
    theme="monokai",
    height=400,
    key=f"ace_editor_{st.session_state.script_version}",
    auto_update=True,
)
st.session_state.script_code = edited_code

if run_clicked and st.session_state.script_code:
    run_script(st.session_state.script_code)
    st.rerun()

# 7) Status bar
st.caption(st.session_state.status)

# ---------------------------------------------------------------------------
# T2-08: Multi-turn refinement UI (shown when a model exists)
# ---------------------------------------------------------------------------
if st.session_state.glb_bytes and st.session_state.get("last_manifest"):
    st.markdown("---")
    st.subheader("✏️ Refine Design")
    refinement_text = st.text_area(
        "What would you like to change?",
        placeholder=(
            "e.g. 'make the shaft 20% longer', "
            "'add a chamfer to the top flange', "
            "'replace the revolute joint with a ball joint'"
        ),
        height=80,
        key="refinement_input",
    )
    if st.button("Apply Refinement", key="apply_refinement") and refinement_text.strip():
        with st.spinner("Refining design…"):
            try:
                import requests as _req
                resp = _req.post(
                    "http://backend:8000/refine",
                    json={
                        "original_manifest": st.session_state.last_manifest,
                        "original_scripts": st.session_state.get("last_scripts", {}),
                        "refinement_prompt": refinement_text.strip(),
                        "session_id": "",
                    },
                    timeout=180,
                )
                data = resp.json()
                if data.get("status") == "ok":
                    glb_b64 = data.get("glb")
                    if glb_b64:
                        import base64 as _b64
                        st.session_state.glb_bytes = _b64.b64decode(glb_b64)
                    else:
                        st.warning("Refinement succeeded but no geometry was returned — viewer not updated.")
                    st.session_state.last_manifest = data.get("updated_manifest", st.session_state.last_manifest)
                    st.session_state.last_scripts = data.get("updated_scripts", {})
                    regen = data.get("regenerated_parts", [])
                    reused = data.get("reused_parts", [])
                    st.success(
                        f"Refined: regenerated {regen}, reused {reused}"
                        if regen else "Refinement applied — no parts changed."
                    )
                    st.session_state.script_version += 1
                    st.rerun()
                else:
                    st.error(f"Refinement failed: {data.get('detail', 'Unknown error')}")
            except Exception as _ref_err:
                st.error(f"Refinement request failed: {_ref_err}")
