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
      "Building and checking geometry. This may take a few minutes.",
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
        ["stl", "Download STL"],
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

// Small, dependency-free WebGL STL viewer. No CDN or browser Internet required.
function makeViewer(canvas) {
  const gl = canvas.getContext("webgl", { antialias: true, alpha: false });
  if (!gl)
    return {
      clear() {},
      load() {
        throw new Error("WebGL is not available in this browser");
      },
    };
  function shader(type, source) {
    const s = gl.createShader(type);
    gl.shaderSource(s, source);
    gl.compileShader(s);
    if (!gl.getShaderParameter(s, gl.COMPILE_STATUS))
      throw new Error(gl.getShaderInfoLog(s));
    return s;
  }
  const program = gl.createProgram();
  gl.attachShader(
    program,
    shader(
      gl.VERTEX_SHADER,
      "attribute vec3 p;attribute vec3 n;uniform mat4 m;varying vec3 normal;void main(){gl_Position=m*vec4(p,1.);normal=n;}",
    ),
  );
  gl.attachShader(
    program,
    shader(
      gl.FRAGMENT_SHADER,
      "precision mediump float;varying vec3 normal;void main(){float d=abs(dot(normalize(normal),normalize(vec3(.4,.8,.6))));vec3 c=mix(vec3(.12,.20,.40),vec3(.48,.65,1.),.25+.75*d);gl_FragColor=vec4(c,1.);}",
    ),
  );
  gl.linkProgram(program);
  if (!gl.getProgramParameter(program, gl.LINK_STATUS))
    throw new Error("Could not initialize 3D viewer");
  const buffer = gl.createBuffer();
  const matrix = gl.getUniformLocation(program, "m");
  let count = 0,
    yaw = 0.55,
    pitch = -0.2,
    zoom = 1,
    pointer;
  function draw() {
    const dpr = Math.min(devicePixelRatio || 1, 2),
      width = Math.max(1, Math.round(canvas.clientWidth * dpr)),
      height = Math.max(1, Math.round(canvas.clientHeight * dpr));
    if (canvas.width !== width || canvas.height !== height) {
      canvas.width = width;
      canvas.height = height;
    }
    gl.viewport(0, 0, width, height);
    gl.clearColor(0.047, 0.067, 0.106, 1);
    gl.clear(gl.COLOR_BUFFER_BIT | gl.DEPTH_BUFFER_BIT);
    if (!count) return;
    const cy = Math.cos(yaw),
      sy = Math.sin(yaw),
      cx = Math.cos(pitch),
      sx = Math.sin(pitch),
      scale = 0.78 * zoom,
      aspect = height / width;
    // Orthographic orbit, Z is the chamber's vertical axis.
    const m = new Float32Array([
      cy * scale * aspect,
      sx * sy * scale,
      -cx * sy * 0.35,
      0,
      -sy * scale * aspect,
      sx * cy * scale,
      -cx * cy * 0.35,
      0,
      0,
      cx * scale,
      sx * 0.35,
      0,
      0,
      0,
      0,
      1,
    ]);
    gl.useProgram(program);
    gl.uniformMatrix4fv(matrix, false, m);
    gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
    for (const [name, offset] of [
      ["p", 0],
      ["n", 12],
    ]) {
      const loc = gl.getAttribLocation(program, name);
      gl.enableVertexAttribArray(loc);
      gl.vertexAttribPointer(loc, 3, gl.FLOAT, false, 24, offset);
    }
    gl.enable(gl.DEPTH_TEST);
    gl.drawArrays(gl.TRIANGLES, 0, count);
  }
  canvas.addEventListener("pointerdown", (e) => {
    pointer = [e.clientX, e.clientY];
    canvas.setPointerCapture(e.pointerId);
  });
  canvas.addEventListener("pointermove", (e) => {
    if (!pointer) return;
    yaw += (e.clientX - pointer[0]) * 0.009;
    pitch = Math.max(
      -1.5,
      Math.min(1.5, pitch + (e.clientY - pointer[1]) * 0.009),
    );
    pointer = [e.clientX, e.clientY];
    draw();
  });
  canvas.addEventListener("pointerup", () => {
    pointer = null;
  });
  canvas.addEventListener("pointercancel", () => {
    pointer = null;
  });
  canvas.addEventListener(
    "wheel",
    (e) => {
      e.preventDefault();
      zoom = Math.max(0.3, Math.min(5, zoom * Math.exp(-e.deltaY * 0.001)));
      draw();
    },
    { passive: false },
  );
  canvas.addEventListener("keydown", (e) => {
    if (
      !["ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown", "+", "-"].includes(
        e.key,
      )
    )
      return;
    e.preventDefault();
    yaw += e.key === "ArrowLeft" ? -0.1 : e.key === "ArrowRight" ? 0.1 : 0;
    pitch += e.key === "ArrowUp" ? -0.1 : e.key === "ArrowDown" ? 0.1 : 0;
    if (e.key === "+") zoom *= 1.1;
    if (e.key === "-") zoom /= 1.1;
    draw();
  });
  $("reset-view").addEventListener("click", () => {
    yaw = 0.55;
    pitch = -0.2;
    zoom = 1;
    draw();
  });
  new ResizeObserver(draw).observe(canvas);
  return {
    clear() {
      count = 0;
      draw();
    },
    load(data) {
      const view = new DataView(data);
      if (data.byteLength < 84) throw new Error("Invalid STL");
      const triangles = view.getUint32(80, true);
      if (!triangles || 84 + triangles * 50 !== data.byteLength)
        throw new Error("Invalid binary STL");
      const step = Math.max(1, Math.ceil(triangles / 1500000));
      const vertices = new Float32Array(Math.ceil(triangles / step) * 18);
      let used = 0;
      const low = [Infinity, Infinity, Infinity],
        high = [-Infinity, -Infinity, -Infinity];
      for (let i = 0; i < triangles; i += step) {
        const off = 84 + i * 50,
          p = [];
        for (let v = 0; v < 3; v++)
          for (let k = 0; k < 3; k++) {
            const x = view.getFloat32(off + 12 + v * 12 + k * 4, true);
            if (!Number.isFinite(x)) throw new Error("Invalid mesh coordinate");
            p.push(x);
            low[k] = Math.min(low[k], x);
            high[k] = Math.max(high[k], x);
          }
        // STL normal records may be zero; derive lighting normals from the face.
        const ax = p[3] - p[0],
          ay = p[4] - p[1],
          az = p[5] - p[2],
          bx = p[6] - p[0],
          by = p[7] - p[1],
          bz = p[8] - p[2];
        const nx = ay * bz - az * by,
          ny = az * bx - ax * bz,
          nz = ax * by - ay * bx,
          length = Math.hypot(nx, ny, nz) || 1;
        for (let v = 0; v < 3; v++) {
          for (let k = 0; k < 3; k++) vertices[used++] = p[v * 3 + k];
          vertices[used++] = nx / length;
          vertices[used++] = ny / length;
          vertices[used++] = nz / length;
        }
      }
      const scale = Math.max(...high.map((x, k) => x - low[k])) / 2;
      if (!scale) throw new Error("Empty mesh");
      for (let i = 0; i < vertices.length; i += 6)
        for (let k = 0; k < 3; k++)
          vertices[i + k] = (vertices[i + k] - (low[k] + high[k]) / 2) / scale;
      gl.bindBuffer(gl.ARRAY_BUFFER, buffer);
      gl.bufferData(gl.ARRAY_BUFFER, vertices, gl.STATIC_DRAW);
      count = vertices.length / 6;
      yaw = 0.55;
      pitch = -0.2;
      zoom = 1;
      draw();
    },
  };
}
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
        ? "Generator ready."
        : "PicoGK runtime is not installed. Run the repository setup script on the server.",
    );
  })
  .catch((error) => status("unavailable", error.message));
