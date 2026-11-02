using System; using System.Diagnostics; using System.Numerics; using PicoGK;
// Regeneratively cooled nozzle: revolved contour with helical internal channels, 2 manifolds, 2 ports.
class Shell : IImplicit {
  public const float L=160, Wall=5f;
  public static float Ri(float z){ // chamber 35 -> throat 15 -> exit 45, cosine blends
    if(z<50) return 35;
    if(z<90){float t=(z-50)/40; return 15+20*0.5f*(1+MathF.Cos(MathF.PI*t));}
    {float t=(z-90)/70; return 15+30*MathF.Pow(t,0.7f);} }
  public float fSignedDistance(in Vector3 v){
    float rho=MathF.Sqrt(v.X*v.X+v.Y*v.Y), z=Math.Clamp(v.Z,0.01f,L-0.01f), ri=Ri(z);
    float d=MathF.Max(ri-rho, rho-(ri+Wall)); return MathF.Max(d,MathF.Max(-v.Z,v.Z-L)); } }
class P { static int Main(string[] a){
  float vox=float.Parse(a[0]); string stl=a[1]; var sw=Stopwatch.StartNew();
  using var lib=new Library(vox);
  // PicoGKRuntime #31 workaround: the 10 s memory monitor walks grids while they are mutated.
  if(Environment.GetEnvironmentVariable("PICOGK_STOP_MEM_TIMER")=="1"){
    var fld=typeof(Library).GetField("m_oTimerMemCheck",System.Reflection.BindingFlags.Instance|System.Reflection.BindingFlags.NonPublic)??throw new MissingFieldException("m_oTimerMemCheck");
    ((System.Threading.Timer)fld.GetValue(lib)).Change(System.Threading.Timeout.Infinite,System.Threading.Timeout.Infinite);
    Console.WriteLine("mem_timer=stopped"); }
  var body=new Voxels(lib,new Shell(),new BBox3(new Vector3(-60,-60,-1),new Vector3(60,60,161)));
  var lat=new Lattice(lib); const int N=48, S=220; float rc=1.1f;
  Vector3 Pt(int ch,float z,float off){ float th=ch*MathF.Tau/N+z*0.012f; float r=Shell.Ri(z)+Shell.Wall/2+off; return new Vector3(r*MathF.Cos(th),r*MathF.Sin(th),z);}
  for(int c=0;c<N;c++) for(int i=0;i<S;i++){ float z0=8+144f*i/S,z1=8+144f*(i+1)/S; lat.AddBeam(Pt(c,z0,0),Pt(c,z1,0),rc,rc,true);} 
  foreach(float mz in new[]{8f,152f}) for(int i=0;i<180;i++){ float r=Shell.Ri(mz)+Shell.Wall/2; float t0=i*MathF.Tau/180,t1=(i+1)*MathF.Tau/180;
    lat.AddBeam(new Vector3(r*MathF.Cos(t0),r*MathF.Sin(t0),mz),new Vector3(r*MathF.Cos(t1),r*MathF.Sin(t1),mz),1.6f,1.6f,true);
    if(i==0) lat.AddBeam(new Vector3(r,0,mz),new Vector3(r+8,0,mz),1.6f,1.6f,true);} 
  body.BoolSubtract(new Voxels(lat));
  body.CalculateProperties(out float vol,out BBox3 bb);
  var m=body.mshAsMesh(); m.SaveToStlFile(stl);
  Console.WriteLine($"vox={vox} cpus={Environment.ProcessorCount} vol_mm3={vol:F3} tris={m.nTriangleCount()} sec={sw.Elapsed.TotalSeconds:F1} peakMB={Process.GetCurrentProcess().PeakWorkingSet64/1048576}");
  return 0; } }
