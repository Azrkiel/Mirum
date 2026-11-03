// Port of model/components.py. Geometry axes: x aft, y right, z up; metres.
const rad = x => x * Math.PI / 180;
const range = n => Array.from({length:n}, (_,i)=>i/(n-1));
const add=(a,b)=>a.map((v,i)=>v+b[i]);
const sub=(a,b)=>a.map((v,i)=>v-b[i]);
const mul=(a,s)=>a.map(v=>v*s);
const dot=(a,b)=>a.reduce((s,v,i)=>s+v*b[i],0);
const cross=(a,b)=>[a[1]*b[2]-a[2]*b[1],a[2]*b[0]-a[0]*b[2],a[0]*b[1]-a[1]*b[0]];
const unit=a=>mul(a,1/Math.hypot(...a));
const rotate=(a,axis,t)=>add(add(mul(a,Math.cos(t)),mul(cross(axis,a),Math.sin(t))),mul(axis,dot(axis,a)*(1-Math.cos(t))));
export function shape(design) {
 const {wing:w,fuselage:f,tail:t}=design.parameters;
 const p=design.placement;
 const L=f.length,R=f.max_diameter/2,rb=f.boom_radius;
 const ln=f.nose_frac*L,lp=Math.max(f.pod_frac*L,ln+.05*L);
 let stations=range(7).map(x=>[x*ln,R*Math.sqrt(Math.max(0,1-(1-x)**2))]);
 stations.push([lp,R]);
 if(f.style==='torpedo') stations.push(...range(9).slice(1).map(x=>[lp+x*(L-lp),rb+(R-rb)*.5*(1+Math.cos(Math.PI*x))]));
 else {const end=Math.min(lp+.15*L,.9*L);stations.push(...range(5).slice(1).map(x=>[lp+x*(end-lp),rb+(R-rb)*.5*(1+Math.cos(Math.PI*x))]),[L,.85*rb]);}
 stations=stations.map(([x,r])=>[x,2*Math.max(r,.0001),2*Math.max(r,.0001)*(f.style==='slab'?.85:1),f.style==='slab'?5:2]);
 const height=x=>{for(let i=1;i<stations.length;i++) if(x<=stations[i][0]){const a=stations[i-1],b=stations[i];return (a[2]+(b[2]-a[2])*(x-a[0])/(b[0]-a[0]))/2;}return stations.at(-1)[2]/2;};
 const wx=p.wing_x??(ln+f.wing_station_in_pod*(lp-ln)),wz=height(wx),tz=height(L),inc=p.tail_incidence_deg;
 const sec=(x,y,z,chord,twist)=>({x,y,z,chord,twist});
 const etas=w.style==='elliptical'?[0,.3,.55,.75,.88,.96,1]:w.style==='polyhedral'?[0,.55,1]:[0,1];
 const sections=etas.map(eta=>{const c=w.root_chord*(w.style==='elliptical'?w.taper+(1-w.taper)*Math.sqrt(Math.max(0,1-eta**2)):1-(1-w.taper)*eta),y=eta*w.span/2;
 const z=w.span/2*Math.tan(rad(w.dihedral_deg))*(w.style==='polyhedral'?Math.max(0,(eta-.55)/.45):eta);
 return sec(wx+.25*w.root_chord+y*Math.tan(rad(w.sweep_qc_deg))-.25*c,y,wz+z,c,w.incidence_deg-w.washout_deg*eta);});
 const surfaces=[{name:'wing',sections,symmetric:true,airfoil:w.naca,role:'wing',dihedral_deg:0}];
 if(t.kind==='V') {const c=t.vt_root_chord,b=t.vt_panel_span,g=rad(t.vt_dihedral_deg);surfaces.push({name:'vtail_V',sections:[sec(L-c,0,tz,c,inc),sec(L-c+.25*c*(1-t.vt_taper),b*Math.cos(g),tz+b*Math.sin(g),c*t.vt_taper,inc)],symmetric:true,airfoil:t.airfoil,role:'vee',dihedral_deg:t.vt_dihedral_deg});}
 else {const cv=t.v_root_chord,hv=t.v_height,tip=-cv+hv*Math.tan(rad(t.v_sweep_le_deg)); const ch=t.h_root_chord,cht=ch*t.h_taper,x0=t.kind==='T'?tip:-ch,z0=t.kind==='T'?hv:0;
 surfaces.push({name:'htail',sections:[sec(L+x0,0,tz+z0,ch,inc),sec(L+x0+.25*(ch-cht),t.h_span/2,tz+z0,cht,inc)],symmetric:true,airfoil:t.airfoil,role:'htail',dihedral_deg:0},
 {name:'vtail',sections:[sec(L-cv,0,tz,cv,0),sec(L+tip,0,tz+hv,cv*t.v_taper,0)],symmetric:false,airfoil:t.airfoil,role:'vtail',dihedral_deg:0});}
 return {surfaces,stations};
}
export function meshes(design,airfoils){
 const geometry=shape(design),parts=[];
 for(const s of geometry.surfaces){
  const vertices=[],triangles=[],secs=s.sections,af=airfoils[s.airfoil];
  const yz=i=>unit([0,secs[i+1].y-secs[i].y,secs[i+1].z-secs[i].z]);
  const frames=secs.map((sec,i)=>{let axis,scale=1;if(i===0)axis=yz(0);else if(i===secs.length-1)axis=yz(i-1);else{const a=yz(i-1),b=yz(i);axis=unit(add(a,b));scale=Math.sqrt(2/(dot(a,b)+1));}
   return [rotate([1,0,0],axis,rad(sec.twist)),rotate(mul(cross([1,0,0],axis),scale),axis,rad(sec.twist))];});
  // Same ordering as AeroSandbox Wing.mesh_body: chordwise strips, then sections.
  for(const [x,z] of af) secs.forEach((sec,i)=>vertices.push(add([sec.x,sec.y,sec.z],mul(add(mul(frames[i][0],x),mul(frames[i][1],z)),sec.chord))));
  const ix=(i,j)=>i+j*secs.length,quad=(a,b,c,d)=>triangles.push([a,b,d],[b,c,d]);
  const ni=secs.length-1,nj=af.length-1;
  for(let i=0;i<ni;i++)for(let j=0;j<nj;j++)quad(ix(i,j),ix(i+1,j),ix(i+1,j+1),ix(i,j+1));
  for(let j=0;j<Math.floor(nj/2);j++){
   quad(ix(0,nj-j),ix(0,j),ix(0,j+1),ix(0,nj-j-1));
   quad(ix(ni,j),ix(ni,j+1),ix(ni,nj-j-1),ix(ni,nj-j));
  }
  for(let i=0;i<ni;i++)quad(ix(i+1,0),ix(i+1,nj),ix(i,nj),ix(i,0));
  if(s.symmetric){const n=vertices.length;vertices.push(...vertices.map(([x,y,z])=>[x,-y,z]));triangles.push(...triangles.map(f=>f.map(i=>i+n)));}
  parts.push({name:s.name,vertices,triangles});
 }
 const vertices=[],triangles=[],n=24;
 for(const [x,w,h,s] of geometry.stations) for(let j=0;j<n;j++){const c=Math.cos(2*Math.PI*j/n),v=Math.sin(2*Math.PI*j/n);vertices.push([x,w/2*Math.abs(c)**(2/s)*Math.sign(c),h/2*Math.abs(v)**(2/s)*Math.sign(v)]);}
 const m=geometry.stations.length;for(let i=0;i<m-1;i++)for(let j=0;j<n;j++){const a=i*n+j,b=i*n+(j+1)%n;triangles.push([a,b,a+n],[b,i*n+(j+1)%n+n,a+n]);}
 parts.push({name:'fuselage',vertices,triangles});return {...geometry,parts};
}
