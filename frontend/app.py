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

    try:
        with requests.post(
            f"{BACKEND_URL}/generate",
            json={"prompt": prompt},
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


# ── Page Layout ─────────────────────────────────────────────

# 1) Prompt
st.markdown("**Describe your 3D model**")
prompt = st.text_area(
    "prompt",
    placeholder="A smooth, ergonomic shell for a computer mouse, roughly 120mm long and 60mm wide.",
    label_visibility="collapsed",
    height=80,
)

# 2) Generate button
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

# 4) 3D viewer (when model exists)
if st.session_state.glb_bytes:
    render_glb_viewer(st.session_state.glb_bytes)
    st.download_button(
        "Download .glb",
        st.session_state.glb_bytes,
        "assembly.glb",
        "model/gltf-binary",
    )

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
