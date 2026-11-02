// Fixed, deterministic regen-chamber builder. No LLM, no code generation: it only renders a typed spec.
// Usage: builder <spec.json> <out.stl>. Prints one JSON line on success.
using System;
using System.Diagnostics;
using System.Globalization;
using System.IO;
using System.Numerics;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using PicoGK;

record Spec(
  [property: JsonPropertyName("family")] string Family,
  [property: JsonPropertyName("units")] string Units,
  [property: JsonPropertyName("chamber_radius_mm")] float ChamberRadius,
  [property: JsonPropertyName("chamber_length_mm")] float ChamberLength,
  [property: JsonPropertyName("convergent_length_mm")] float ConvergentLength,
  [property: JsonPropertyName("throat_radius_mm")] float ThroatRadius,
  [property: JsonPropertyName("divergent_length_mm")] float DivergentLength,
  [property: JsonPropertyName("exit_radius_mm")] float ExitRadius,
  [property: JsonPropertyName("wall_thickness_mm")] float Wall,
  [property: JsonPropertyName("channel_count")] int ChannelCount,
  [property: JsonPropertyName("channel_radius_mm")] float ChannelRadius,
  [property: JsonPropertyName("channel_twist_rad_per_mm")] float Twist,
  [property: JsonPropertyName("manifold_radius_mm")] float ManifoldRadius,
  [property: JsonPropertyName("end_margin_mm")] float EndMargin,
  [property: JsonPropertyName("voxel_size_mm")] float Voxel,
  [property: JsonPropertyName("assumptions")] string Assumptions)
{
  public float Total => ChamberLength + ConvergentLength + DivergentLength;
  public float ThroatZ => ChamberLength + ConvergentLength;

  // Inner (hot-gas side) radius: chamber cylinder, then cosine blends (C1) to throat and to exit.
  public float Ri(float z)
  {
    if (z <= ChamberLength) return ChamberRadius;
    if (z <= ThroatZ)
    {
      float t = (z - ChamberLength) / ConvergentLength;
      return ThroatRadius + (ChamberRadius - ThroatRadius) * 0.5f * (1f + MathF.Cos(MathF.PI * t));
    }
    if (z <= Total)
    {
      float t = (z - ThroatZ) / DivergentLength;
      return ThroatRadius + (ExitRadius - ThroatRadius) * 0.5f * (1f - MathF.Cos(MathF.PI * t));
    }
    return ExitRadius;
  }

  public void Validate()
  {
    static bool Pos(float v) => float.IsFinite(v) && v > 0f;
    if (Family != "regen_chamber") throw new ArgumentException("family");
    if (Units != "mm") throw new ArgumentException("units");
    if (Assumptions != "geometry_only_no_gas_dynamics_thermal_or_structural_analysis") throw new ArgumentException("assumptions");
    if (!(Pos(ChamberRadius) && Pos(ChamberLength) && Pos(ConvergentLength) && Pos(ThroatRadius) && Pos(DivergentLength) &&
          Pos(ExitRadius) && Pos(Wall) && Pos(ChannelRadius) && Pos(ManifoldRadius) && Pos(EndMargin) && Pos(Voxel)))
      throw new ArgumentException("all dimensions must be finite and positive");
    if (!float.IsFinite(Twist)) throw new ArgumentException("twist must be finite");
    if (ChannelCount < 4 || ChannelCount > 256) throw new ArgumentException("channel_count");
    if (Voxel < 0.1f || Voxel > 1.0f) throw new ArgumentException("voxel_size_mm");
    if (2f * EndMargin >= Total) throw new ArgumentException("end_margin_mm too large");
  }
}

class Shell : IImplicit
{
  readonly Spec s;
  public Shell(Spec spec) { s = spec; }
  public float fSignedDistance(in Vector3 v)
  {
    float rho = MathF.Sqrt(v.X * v.X + v.Y * v.Y);
    float z = Math.Clamp(v.Z, 0.01f, s.Total - 0.01f);
    float ri = s.Ri(z);
    float d = MathF.Max(ri - rho, rho - (ri + s.Wall));
    return MathF.Max(d, MathF.Max(-v.Z, v.Z - s.Total));
  }
}

static class Program
{
  static readonly JsonSerializerOptions Opts = new()
  {
    UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
    RespectRequiredConstructorParameters = true,
    AllowTrailingCommas = false,
    NumberHandling = JsonNumberHandling.Strict,
  };

  static int Main(string[] a)
  {
    try { return Run(a); }
    catch (Exception e) { Console.Error.WriteLine("builder error: " + e.GetType().Name + ": " + e.Message); return 1; }
  }

  static int Run(string[] a)
  {
    if (a.Length != 2) { Console.Error.WriteLine("usage: builder <spec.json> <out.stl>"); return 64; }
    var sw = Stopwatch.StartNew();
    Spec s = JsonSerializer.Deserialize<Spec>(File.ReadAllText(a[0]), Opts) ?? throw new ArgumentException("empty spec");
    s.Validate();
    using var lib = new Library(s.Voxel);
    // PicoGKRuntime #31: the 10 s memory monitor walks grids while they are mutated (SIGSEGV seen in soak).
    // Always stop it; fail loudly if NuGet 2.3.0 private field is not there.
    var fld = typeof(Library).GetField("m_oTimerMemCheck", BindingFlags.Instance | BindingFlags.NonPublic)
              ?? throw new MissingFieldException("PicoGK.Library.m_oTimerMemCheck not found; refusing to run with the memory timer on");
    var timer = fld.GetValue(lib) as System.Threading.Timer
              ?? throw new InvalidOperationException("m_oTimerMemCheck is not a Timer");
    timer.Change(System.Threading.Timeout.Infinite, System.Threading.Timeout.Infinite);

    float L = s.Total;
    float rmax = MathF.Max(s.ChamberRadius, s.ExitRadius) + s.Wall + 5f;
    var body = new Voxels(lib, new Shell(s), new BBox3(new Vector3(-rmax, -rmax, -2f), new Vector3(rmax, rmax, L + 2f)));
    var lat = new Lattice(lib);
    float z0 = s.EndMargin, z1 = L - s.EndMargin;
    int steps = (int)MathF.Ceiling((z1 - z0) / (1.5f * s.Voxel));
    float rc = s.ChannelRadius, rm = s.ManifoldRadius;
    Vector3 Pt(int c, float z)
    {
      float th = c * (MathF.PI * 2f) / s.ChannelCount + z * s.Twist;
      float r = s.Ri(z) + s.Wall / 2f;
      return new Vector3(r * MathF.Cos(th), r * MathF.Sin(th), z);
    }
    for (int c = 0; c < s.ChannelCount; c++)
      for (int i = 0; i < steps; i++)
      {
        float za = z0 + (z1 - z0) * i / steps, zb = z0 + (z1 - z0) * (i + 1) / steps;
        lat.AddBeam(Pt(c, za), Pt(c, zb), rc, rc, true);
      }
    foreach (float mz in new[] { z0, z1 })
    {
      float r = s.Ri(mz) + s.Wall / 2f;
      int segs = (int)MathF.Ceiling(MathF.PI * 2f * r / (1.5f * s.Voxel));
      for (int i = 0; i < segs; i++)
      {
        float t0 = i * (MathF.PI * 2f) / segs, t1 = (i + 1) * (MathF.PI * 2f) / segs;
        lat.AddBeam(new Vector3(r * MathF.Cos(t0), r * MathF.Sin(t0), mz), new Vector3(r * MathF.Cos(t1), r * MathF.Sin(t1), mz), rm, rm, true);
      }
      // one radial port at theta = 0, mid-wall to 3 mm past the outer surface
      lat.AddBeam(new Vector3(r, 0f, mz), new Vector3(s.Ri(mz) + s.Wall + 3f, 0f, mz), rm, rm, true);
    }
    body.BoolSubtract(new Voxels(lat));
    body.CalculateProperties(out float vol, out BBox3 bb);
    var m = body.mshAsMesh();
    m.SaveToStlFile(a[1]);
    var inv = CultureInfo.InvariantCulture;
    Console.WriteLine("{\"volume_mm3\":" + vol.ToString("F3", inv) + ",\"tris\":" + m.nTriangleCount() +
      ",\"seconds\":" + sw.Elapsed.TotalSeconds.ToString("F1", inv) +
      ",\"peak_mb\":" + (Process.GetCurrentProcess().PeakWorkingSet64 / 1048576) + "}");
    return 0;
  }
}
