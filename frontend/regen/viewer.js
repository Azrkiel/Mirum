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
      "precision mediump float;varying vec3 normal;void main(){float d=abs(dot(normalize(normal),normalize(vec3(.4,.8,.6))));vec3 c=mix(vec3(.14,.28,.18),vec3(.69,.88,.50),.25+.75*d);gl_FragColor=vec4(c,1.);}",
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
    gl.clearColor(0.065, 0.1, 0.075, 1);
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
  document.getElementById("reset-view").addEventListener("click", () => {
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
