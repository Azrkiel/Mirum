import * as THREE from 'three';
import {OrbitControls} from 'three/addons/controls/OrbitControls.js';
import {Streamlit} from 'streamlit-component-lib';
import {meshes,shape} from './model.js';
import './style.css';
const $=s=>document.querySelector(s),clone=x=>JSON.parse(JSON.stringify(x));
document.querySelector('#app').innerHTML=`<div class="toolbar"><strong>Explore one design</strong><button id="reset">Reset design</button><button id="compare">Compare original</button><label><input id="wire" type="checkbox">Wireframe</label></div><div class="layout"><div id="controls"></div><main><div class="views"><button data-view="orbit">Reset camera</button><button data-view="top">Top</button><button data-view="front">Front</button><button data-view="side">Side</button></div><div id="viewport"><canvas></canvas><span id="guide"></span></div><div id="status" role="status">Loading geometry…</div><div class="analysis"><button id="evaluate">Recalculate performance</button><label><input id="auto" type="checkbox">Auto recalculate after pause</label></div><div id="metrics"></div><div id="warnings"></div><small>Metres · x aft, y right, z up. Drag to rotate; scroll to zoom.<br>Distance = launch height × trimmed L/D, in still air. Screening estimates, not flight validation.</small></main></div>`;
const renderer=new THREE.WebGLRenderer({canvas:$('canvas'),antialias:true});renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.setClearColor('#101e30');const scene=new THREE.Scene(),camera=new THREE.PerspectiveCamera(38,1,.001,50);camera.up.set(0,0,1);
const orbit=new OrbitControls(camera,renderer.domElement);orbit.enableDamping=true;
scene.add(new THREE.HemisphereLight(0xd9efff,0x46505c,3));const light=new THREE.DirectionalLight(0xffffff,3);light.position.set(-1,2,3);scene.add(light);
const axes=new THREE.AxesHelper(.2);scene.add(axes);
let model=new THREE.Group(),ghost=new THREE.Group();const angleGuide=new THREE.Group();scene.add(model,ghost,angleGuide);
let state,original,metadata,epoch,seq=0,timer,autoTimer,pollTimer,busy=false,lastReply=-1,lastGeometry,framePending=false,comparing=false,view='orbit',processedReply=null,evaluatedSeq=-1;
function clear(group){while(group.children.length){const o=group.children[0];o.geometry.dispose();o.material.dispose();group.remove(o);}}
function draw(geometry,group=model,opacity=1){clear(group);for(const p of geometry.parts){const g=new THREE.BufferGeometry();g.setAttribute('position',new THREE.Float32BufferAttribute(p.vertices.flat(),3));g.setIndex(p.triangles.flat());g.computeVertexNormals();const color=p.name==='wing'?0x74d7ca:p.name==='fuselage'?0xe8c493:0x7ea8ef;
 group.add(new THREE.Mesh(g,new THREE.MeshStandardMaterial({color,side:THREE.DoubleSide,roughness:.65,wireframe:$('#wire').checked,transparent:opacity<1,opacity,depthWrite:opacity===1})));}window.__gliderMeshVersion=(window.__gliderMeshVersion||0)+1;}
function aim(which){view=which;const L=state.parameters.fuselage.length;orbit.target.set(L/2,0,0);const target=orbit.target;
 const offset=which==='top'?[0,0,2]:which==='front'?[-2,0,0]:which==='side'?[0,2,0]:[-1.1,1.4,.9];camera.position.copy(target).add(new THREE.Vector3(...offset));camera.up.set(...(which==='top'?[1,0,0]:[0,0,1]));orbit.update();guide();}
function guide(){const w=state?.parameters.wing;if(!w)return;clear(angleGuide);
 if(view==='front'){
  const root=shape(state).surfaces[0].sections[0],r=.18,theta=w.dihedral_deg*Math.PI/180;
  const makeLine=points=>{const g=new THREE.BufferGeometry().setFromPoints(points.map(([y,z])=>new THREE.Vector3(root.x-.05,y,root.z+z)));angleGuide.add(new THREE.Line(g,new THREE.LineBasicMaterial({color:0xaaf2d7,depthTest:false})));};
  makeLine([[0,0],[r,0]]);makeLine([[0,0],[r*Math.cos(theta),r*Math.sin(theta)]]);
  makeLine(Array.from({length:21},(_,i)=>[r*.65*Math.cos(theta*i/20),r*.65*Math.sin(theta*i/20)]));
 }
 $('#guide').textContent=view==='front'?`∠ ${w.dihedral_deg.toFixed(1)}° upward${w.style==='polyhedral'?' (effective tip-height angle)':''}`:'';}
function local(){if(framePending)return;framePending=true;requestAnimationFrame(()=>{framePending=false;try{draw(meshes(state,metadata.airfoils));guide();}catch(e){$('#warnings').textContent='Preview could not update: '+e.message;}});}
function send(action){if(action==='evaluate'){clearTimeout(timer);clearTimeout(autoTimer);}const invalid=$('#controls input:invalid');if(invalid){invalid.reportValidity();return;}Streamlit.setComponentValue({action,design:clone(state),sequence:seq,epoch,nonce:Date.now()});}
function dirty(){clearTimeout(pollTimer);seq++;busy=false;evaluatedSeq=-1;lastGeometry=null;$('#metrics').replaceChildren();$('#status').textContent='Local preview · Performance needs recalculation';$('#warnings').textContent='';local();clearTimeout(timer);clearTimeout(autoTimer);timer=setTimeout(()=>send('preview'),200);if($('#auto').checked)autoTimer=setTimeout(()=>send('evaluate'),900);}
const labels={span:'Span',root_chord:'Root chord',taper:'Tip / root chord',sweep_qc_deg:'Quarter-chord sweep',dihedral_deg:'Upward angle (dihedral)',washout_deg:'Tip twist (washout)',incidence_deg:'Wing incidence',length:'Body length',max_diameter:'Maximum diameter',nose_frac:'Nose fraction',pod_frac:'Pod fraction',boom_radius:'Boom radius',wing_station_in_pod:'Wing station in pod',h_span:'Horizontal tail span',h_root_chord:'Horizontal root chord',h_taper:'Horizontal taper',v_height:'Fin height',v_root_chord:'Fin root chord',v_taper:'Fin taper',v_sweep_le_deg:'Fin sweep',vt_panel_span:'V-tail panel span',vt_root_chord:'V-tail root chord',vt_taper:'V-tail taper',vt_dihedral_deg:'V-tail upward angle'};
function flightControls(root){
 const schema=metadata.flight_controls;if(!schema)return;
 const fc=state.flight_conditions;
 const section=document.createElement('section'),title=document.createElement('h3');title.textContent='Flight conditions';section.append(title);root.append(section);
 const label=document.createElement('label');label.textContent='Analysis mode';const select=document.createElement('select');select.dataset.field='flight.mode';
 select.add(new Option('Automatic trim (sea level)','automatic_trim'));select.add(new Option('Specified conditions','specified'));select.value=fc.mode;
 select.onchange=()=>{fc.mode=select.value;controls();dirty();};label.append(select);section.append(label);
 const names={airspeed_m_s:'True airspeed (m/s)',alpha_deg:'Angle of attack (deg)',beta_deg:'Sideslip (deg)',altitude_m:'ISA altitude above sea level (m)'};
 for(const [key,labelText] of Object.entries(names)){const meta=schema[key],row=document.createElement('label');row.textContent=labelText;row.title=meta.description;const input=document.createElement('input');input.type='number';input.required=true;input.min=meta.minimum;input.max=meta.maximum;input.step='any';input.value=fc[key];input.disabled=fc.mode!=='specified';input.dataset.field='flight.'+key;
 input.onchange=()=>{if(input.checkValidity())fc[key]=Number(input.value);dirty();};row.append(input);section.append(row);}
 const note=document.createElement('small');note.textContent=fc.mode==='specified'?'Forces at these conditions, not a trimmed glide or CFD. Existing placement is preserved. Altitude is separate from launch height.':'The solver selects trim speed and angle at sea level. Specified inputs are inactive.';section.append(note);
}
function controls(){const root=$('#controls');root.replaceChildren();flightControls(root);for(const family of ['wing','fuselage','tail']){const section=document.createElement('section');const title=document.createElement('h3');title.textContent=family==='fuselage'?'Body':family;section.append(title);root.append(section);
 for(const [name,meta] of Object.entries(metadata.controls[family])){if(name.startsWith('naca_'))continue;
 if(family==='tail'&&meta.numeric&&name.startsWith('vt_')!==(state.parameters.tail.kind==='V'))continue;
 const row=document.createElement('label'),caption=document.createElement('span');caption.textContent=(labels[name]||name)+(meta.unit?` (${meta.unit})`:'');row.append(caption);
 const control=document.createElement(meta.numeric?'input':'select');control.dataset.field=`${family}.${name}`;
 if(meta.numeric){control.type='range';control.min=meta.range[0];control.max=meta.range[1];control.step='any';control.value=state.parameters[family][name];control.disabled=meta.range[0]===meta.range[1];const value=document.createElement('output');value.textContent=Number(control.value).toPrecision(6);row.append(value);
 control.addEventListener('input',()=>{state.parameters[family][name]=Number(control.value);value.textContent=Number(control.value).toPrecision(6);state.placement={wing_x:null,payload_x:null,ballast_kg:0,tail_incidence_deg:0};dirty();});control.addEventListener('change',()=>{clearTimeout(timer);send('preview');});
 row.title=`Permitted interval: ${meta.range.join(' – ')} ${meta.unit}. Editing this value leaves search bounds unchanged.`;
 if(name==='washout_deg')row.title+=' Washout twists the tip nose down relative to the root.';
 if(name==='incidence_deg')row.title+=' Incidence tilts the whole wing relative to the body.';
 }else{for(const option of [...new Set(meta.range)])control.add(new Option(option,option));control.value=state.parameters[family][name];control.onchange=()=>{state.parameters[family][name]=control.value;
 if(family==='tail'&&name==='kind'){for(const [key,m]of Object.entries(metadata.controls.tail)){if(!m.numeric)continue;delete state.parameters.tail[key];if(key.startsWith('vt_')===(control.value==='V'))state.parameters.tail[key]=(m.range[0]+m.range[1])/2;}controls();}
 state.placement={wing_x:null,payload_x:null,ballast_kg:0,tail_incidence_deg:0};dirty();};}
 row.append(control);section.append(row);
 }
 if(family==='wing'){const row=document.createElement('label');row.textContent='Wing airfoil (NACA)';const select=document.createElement('select');const r=metadata.controls.wing;
 const codes=new Set();for(const m of r.naca_camber_pct.range)for(const p of r.naca_camber_pos.range)for(const t of r.naca_thickness_pct.range)codes.add(`${m}${m?p:0}${String(t).padStart(2,'0')}`);
 for(const code of codes)select.add(new Option(code,code));select.value=state.parameters.wing.naca;select.onchange=()=>{state.parameters.wing.naca=select.value;state.placement={wing_x:null,payload_x:null,ballast_kg:0,tail_incidence_deg:0};dirty();};row.append(select);section.append(row);}
 }}
function metrics(result){const root=$('#metrics');root.replaceChildren();const m=result.metrics;const title=document.createElement('strong');const specified=result.analysis_mode==='specified';title.textContent=specified?'Specified-condition estimate — not trimmed':m.valid?'Feasible screening result':'Infeasible design';title.className=specified?'':m.valid?'valid':'invalid';root.append(title);
 const values=specified? [['Lift (N)',m.lift_N],['Drag (N)',m.drag_N],['Side force (N)',m.side_force_N],['Roll moment (N m)',m.roll_moment_Nm],['Pitch moment (N m)',m.pitch_moment_Nm],['Yaw moment (N m)',m.yaw_moment_Nm],['CL',m.CL],['CD',m.CD],['CY',m.CY],['Cl',m.Cl],['Cm',m.Cm],['Cn',m.Cn],['Mass (kg)',m.mass_kg],['Dynamic pressure (Pa)',m.dynamic_pressure_Pa],['Air density (kg/m³)',m.air_density_kg_m3],['Reynolds number',m.reynolds_mac],['Lift / weight',m.lift_to_weight]]: [['L/D',m.L_over_D],['Still-air distance (m)',m.glide_distance_m],['Speed (m/s)',m.glide_speed_m_s],['Mass (kg)',m.mass_kg],['Static margin (% MAC)',m.static_margin==null?null:m.static_margin*100],['Stall margin (%)',m.CLmax_est?100*(1-m.CL_trim/m.CLmax_est):null]];
 const grid=document.createElement('dl');for(const [label,value] of values){const dt=document.createElement('dt'),dd=document.createElement('dd');dt.textContent=label;dd.textContent=Number.isFinite(value)?value.toFixed(3):'Unavailable';grid.append(dt,dd);}root.append(grid);
 const reasons=document.createElement('p');reasons.textContent=specified?(m.warnings||[]).join('; '):m.fail_reasons||'';root.append(reasons);const info=document.createElement('p');info.textContent=specified?`True airspeed ${result.operating_conditions.airspeed_m_s} m/s · Angle of attack ${result.operating_conditions.alpha_deg}° · Sideslip ${result.operating_conditions.beta_deg}° · ISA altitude ${result.operating_conditions.altitude_m} m. No automatic rebalance/retrim. Glide distance and stability not established.`:`Launch height: ${state.request.launch_height_m??10} m · Rebalanced and retrimmed within permitted bounds.`;root.append(info);
 const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');summary.textContent='Placement adjustments and analysis evidence';pre.textContent=JSON.stringify({operating_conditions:result.operating_conditions,assumptions:result.assumptions,adjustments:result.adjustments,solver:result.solver,input_revision:result.input_revision,geometry_digest:result.geometry.geometry_digest},null,2);details.append(summary,pre);root.append(details);
 const download=document.createElement('button');download.textContent='Download evaluated revision';download.onclick=()=>send('export');root.append(download);
}
function receive(event){const args=event.detail.args;if(epoch!==args.epoch){epoch=args.epoch;state=clone(args.design);original=clone(state);metadata=args.metadata;seq=0;lastReply=-1;busy=false;controls();local();aim('orbit');send('preview');}
 const reply=args.reply;if(!reply||reply.epoch!==epoch||reply.sequence!==seq||reply.sequence<lastReply||reply.nonce===processedReply)return;lastReply=reply.sequence;processedReply=reply.nonce;
 if(reply.error){busy=false;$('#status').textContent=reply.error;return;}
 if(reply.preview&&evaluatedSeq!==seq){lastGeometry=reply.preview.geometry;draw(lastGeometry);$('#status').textContent='Canonical preview · Performance needs recalculation';$('#warnings').textContent=reply.preview.warnings.join(' ');}
 if(reply.evaluation_id){busy=true;$('#status').textContent='Recalculating performance… Geometry remains editable.';clearTimeout(pollTimer);pollTimer=setTimeout(()=>send('poll'),800);}
 if(reply.result){evaluatedSeq=seq;busy=false;clearTimeout(pollTimer);state.placement=clone(reply.result.effective_placement);lastGeometry=reply.result.geometry;draw(lastGeometry);guide();$('#status').textContent=reply.result.analysis_mode==='specified'?'Specified-condition analysis complete · Not trimmed':Number.isFinite(reply.result.metrics.L_over_D)?'Evaluated geometry · Performance is current':'Screening completed · Trimmed performance unavailable';metrics(reply.result);}
 if(reply.download){const a=document.createElement('a');a.href='data:application/zip;base64,'+reply.download;a.download='mirum-glider-evaluated.zip';a.click();}
}
$('#evaluate').onclick=()=>{clearTimeout(autoTimer);send('evaluate');};$('#auto').onchange=()=>{if($('#auto').checked){clearTimeout(autoTimer);autoTimer=setTimeout(()=>send('evaluate'),900);}else clearTimeout(autoTimer);};
$('#reset').onclick=()=>{state=clone(original);controls();dirty();};$('#compare').onclick=()=>{comparing=!comparing;if(comparing)draw(meshes(original,metadata.airfoils),ghost,.25);else clear(ghost);$('#compare').textContent=comparing?'Hide original':'Compare original';};
$('#wire').onchange=()=>{if(lastGeometry)draw(lastGeometry);else local();};document.querySelectorAll('[data-view]').forEach(b=>b.onclick=()=>aim(b.dataset.view));
new ResizeObserver(()=>{const width=$('#viewport').clientWidth,height=440;renderer.setSize(width,height);camera.aspect=width/height;camera.updateProjectionMatrix();Streamlit.setFrameHeight(980);}).observe($('#viewport'));
renderer.setAnimationLoop(()=>{orbit.update();renderer.render(scene,camera);});
window.addEventListener('pagehide',()=>{clearTimeout(timer);clearTimeout(autoTimer);clearTimeout(pollTimer);renderer.setAnimationLoop(null);clear(model);clear(ghost);clear(angleGuide);orbit.dispose();renderer.dispose();});
Streamlit.events.addEventListener(Streamlit.RENDER_EVENT,receive);Streamlit.setComponentReady();Streamlit.setFrameHeight(980);
