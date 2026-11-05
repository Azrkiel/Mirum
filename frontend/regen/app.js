"use strict";
const $ = (id) => document.getElementById(id);
const groups = {
  "Chamber & contour": [
    "chamber_radius_mm",
    "chamber_length_mm",
    "convergent_length_mm",
    "throat_radius_mm",
    "divergent_length_mm",
    "exit_radius_mm",
  ],
  "Cooling channels": [
    "wall_thickness_mm",
    "channel_count",
    "channel_radius_mm",
    "channel_twist_rad_per_mm",
    "manifold_radius_mm",
    "end_margin_mm",
  ],
  "Mesh resolution": ["voxel_size_mm"],
};
const labels = {
  chamber_radius_mm: "Chamber radius",
  chamber_length_mm: "Chamber length",
  convergent_length_mm: "Convergent length",
  throat_radius_mm: "Throat radius",
  divergent_length_mm: "Divergent length",
  exit_radius_mm: "Exit radius",
  wall_thickness_mm: "Wall thickness",
  channel_count: "Channel count",
  channel_radius_mm: "Channel radius",
  channel_twist_rad_per_mm: "Twist (rad/mm)",
  manifold_radius_mm: "Manifold radius",
  end_margin_mm: "End margin",
  voxel_size_mm: "Voxel size",
};
let config,
  active = false,
  lastSpec;
function status(state, message) {
  document.body.dataset.status = state;
  $("state").textContent = state.toUpperCase();
  $("message").textContent = message;
}
async function jsonRequest(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    const message = Array.isArray(data.detail)
      ? data.detail
          .map((e) => `${e.loc.join(".") || "Parameters"}: ${e.msg}`)
          .join("; ")
      : data.detail;
    throw new Error(message || `Request failed (${response.status})`);
  }
  return data;
}
function loadPreset() {
  const spec = config.examples[$("preset").value];
  for (const name of Object.keys(labels)) $(name).value = spec[name];
}
function specification() {
  const spec = {
    family: "regen_chamber",
    units: "mm",
    assumptions: "geometry_only_no_gas_dynamics_thermal_or_structural_analysis",
  };
  for (const name of Object.keys(labels)) spec[name] = Number($(name).value);
  return spec;
}
function buildForm() {
  for (const [group, names] of Object.entries(groups)) {
    const fieldset = document.createElement("fieldset");
    const legend = document.createElement("legend");
    legend.textContent = group;
    fieldset.append(legend);
    const grid = document.createElement("div");
    grid.className = "field-grid";
    fieldset.append(grid);
    for (const name of names) {
      const field = config.schema.properties[name],
        label = document.createElement("label");
      label.textContent = labels[name];
      const input = document.createElement("input");
      input.id = name;
      input.name = name;
      input.type = "number";
      input.required = true;
      input.step = name === "channel_count" ? "1" : "any";
      if (field.minimum !== undefined) input.min = field.minimum;
      else if (field.exclusiveMinimum !== undefined)
        input.min = Math.max(field.exclusiveMinimum + 0.000001, 0.000001);
      if (field.maximum !== undefined) input.max = field.maximum;
      label.append(input);
      grid.append(label);
    }
    $("fields").append(fieldset);
  }
  loadPreset();
}
function showReport(job) {
  if (!job.report) return;
  $("result").hidden = false;
  $("result-summary").textContent =
    job.status === "accepted"
      ? "Geometry matches the specification. Engineering certification has not been evaluated."
      : (job.report.failures || ["Geometry checks failed."]).join("; ");
  $("report").textContent = JSON.stringify(job.report, null, 2);
}
$("preset").addEventListener("change", () => {
  if (config) loadPreset();
});
$("copy-spec").addEventListener("click", async () => {
  try {
    await navigator.clipboard.writeText(
      JSON.stringify(lastSpec || specification(), null, 2),
    );
    $("copy-spec").textContent = "Copied";
  } catch {
    $("copy-spec").textContent = "Clipboard unavailable";
  }
});
$("spec-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (active) return;
  active = true;
  $("generate").disabled = true;
  $("preset").disabled = true;
  $("downloads").replaceChildren();
  $("downloads").hidden = true;
  $("result").hidden = true;
  viewer.clear();
  $("empty").hidden = false;
  $("preview-note").textContent = "";
  const start = Date.now();
  const timer = setInterval(() => {
    $("elapsed").textContent = `${Math.floor((Date.now() - start) / 1000)}s`;
  }, 1000);
  try {
    lastSpec = specification();
    status(
      "running",
      "Building and checking geometry. This usually takes a few minutes…",
    );
    let job = await jsonRequest("/api/jobs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastSpec),
    });
    while (job.status === "queued" || job.status === "running") {
      await new Promise((resolve) => setTimeout(resolve, 1200));
      job = await jsonRequest(`/api/jobs/${job.id}`);
    }
    status(job.status, job.message);
    showReport(job);
    if (job.status === "accepted") {
      for (const [key, title] of [
        ["stl", "Download STL ↗"],
        ["report", "Report JSON"],
        ["spec", "Parameters JSON"],
      ]) {
        const link = document.createElement("a");
        link.href = job.artifacts[key];
        link.download = "";
        link.textContent = title;
        $("downloads").append(link);
      }
      $("downloads").hidden = false;
      try {
        const response = await fetch(job.artifacts.stl);
        if (!response.ok) throw new Error("Mesh could not be loaded");
        viewer.load(await response.arrayBuffer());
        $("empty").hidden = true;
        $("preview-note").textContent =
          "Interactive preview may sample triangles from large meshes. The STL download contains the complete accepted mesh.";
      } catch (error) {
        $("preview-note").textContent =
          `Preview unavailable: ${error.message}. Your downloads are still available.`;
      }
    }
  } catch (error) {
    status("unavailable", error.message);
  } finally {
    clearInterval(timer);
    active = false;
    $("generate").disabled = !config?.ready;
    $("preset").disabled = false;
  }
});

let viewer;
try {
  viewer = makeViewer($("viewer"));
} catch {
  viewer = {
    clear() {},
    load() {
      throw new Error("3D graphics initialization failed");
    },
  };
}
jsonRequest("/api/config")
  .then((data) => {
    config = data;
    buildForm();
    $("notice").textContent = data.notice;
    $("generate").disabled = !data.ready;
    status(
      data.ready ? "ready" : "unavailable",
      data.ready
        ? "Generator ready. Choose dimensions to begin."
        : "PicoGK runtime is not installed. Run the repository setup script on the server.",
    );
  })
  .catch((error) => status("unavailable", error.message));
