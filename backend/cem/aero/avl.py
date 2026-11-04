"""Native AVL full-surface qualification, geometric loads and stability gates."""
import math
import re

import numpy as np

from cem.evidence import number
from gliders.model.analysis import lateral_modes
from .native import run

DERIVATIVES=('CLa','Cma','CYb','Clb','Cnb','CYp','Clp','Cnp','CYr','Clr','Cnr','Cmq')


def geometry_text(geometry, spanwise=24, chordwise=8):
    ref=geometry.reference
    f=lambda x:format(x,'.12g')
    lines=['Mirum independent installed geometry','0','0 0 0',
        ' '.join(f(v) for v in (ref.area_m2,ref.chord_m,ref.span_m)),
        ' '.join(map(f,ref.cg_m)),'0']
    for surface in geometry.surfaces:
        lines+=['SURFACE',surface.name,f'{chordwise} 1.0 {spanwise} 1.0']
        if surface.mirror_y: lines+=['YDUPLICATE','0']
        for sec in surface.sections:
            lines+=['SECTION',' '.join(f(v) for v in (*sec.leading_edge_m,sec.chord_m,sec.incidence_deg)),
                    'NACA',surface.naca]
    return '\n'.join(lines)+'\n'


def command(alpha):
    return (f'LOAD aircraft.avl\nOPER\nA A {alpha:.12g}\nB B 0\nR R 0\nP P 0\nY Y 0\n'
            'X\nST stability.st\nFT forces.ft\nFE elements.fe\n\nQUIT\n')


def coefficients(raw, keys):
    # AVL appends a spiral diagnostic `Clb Cnr / Clr Cnb = ...`; this is
    # not a second Cnb derivative. Only standalone coefficient columns count.
    raw='\n'.join(line for line in raw.splitlines() if not re.match(r'\s*Clb Cnr / Clr Cnb\s*=',line))
    result={}
    for key in keys:
        matches=re.findall(r'(?<![A-Za-z0-9_])'+re.escape(key)+r'\s*=\s*(\S+)',raw)
        if len(matches)!=1:
            raise ValueError(f'AVL missing/duplicate coefficient {key}')
        try: result[key]=number(float(matches[0].replace('D','E')))
        except (TypeError,ValueError) as exc: raise ValueError(f'AVL nonfinite coefficient {key}') from exc
    return result


def strip_closure(raw, totals, geometry):
    """Reconstruct pitch and force from native element report, including mirrors.

    FE `wake dnwsh` is a dimensionless far-wake velocity; retained as such.
    It is not used as the effective local angle in the nonlinear lifting model.
    """
    strips=[]
    for chunk in re.split(r'\bStrip #\s*',raw)[1:]:
        def field(name):
            found=re.search(re.escape(name)+r'\s*=\s*(\S+)',chunk)
            if found is None: raise ValueError('Missing AVL strip field '+name)
            return number(float(found[1]))
        x,y,z=(field(k) for k in ('Xle','Yle','Zle'))
        area,chord,dihedral=(field(k) for k in ('Strip Area','Ave. Chord','Strip Dihed.'))
        if min(area,chord)<=0: raise ValueError('Invalid AVL strip area/chord')
        ca,cn,cmle=(field(k) for k in ('ca','cn','cmLE'))
        # AVL CMLE follows the *ordered leading-edge segment*, whose direction
        # reverses for YDUP. Convert it to the consistently positive-y span.
        if y < 0:
            cmle=-cmle
        angle=math.radians(dihedral)
        normal=np.array([0.,-math.sin(angle),math.cos(angle)])
        span=np.array([0.,math.cos(angle),math.sin(angle)])
        force=area*(np.array([ca,0.,0.])+cn*normal)
        moment=np.cross(np.array([x,y,z])-geometry.reference.cg_m,force)+area*chord*cmle*span
        downwash=re.search(r'wake dnwsh\s*=\s*(\S+)',chunk)
        if downwash is None: raise ValueError('Missing AVL far-wake downwash')
        strips.append(dict(center_le_m=[x,y,z],area_m2=area,chord_m=chord,
            force_per_q=force.tolist(),moment_per_q=moment.tolist(),
            far_wake_velocity_ratio=number(float(downwash[1]))))
    if not strips: raise ValueError('Missing AVL strip forces')
    ref=geometry.reference
    if 'Standard axis orientation' not in raw:
        raise ValueError('Unsupported AVL axis orientation')
    force=np.sum([s['force_per_q'] for s in strips],axis=0)/ref.area_m2
    target=np.array([-totals['CXtot'],totals['CYtot'],-totals['CZtot']])
    moment=np.sum([s['moment_per_q'] for s in strips],axis=0)/(
        ref.area_m2*np.array([ref.span_m,ref.chord_m,ref.span_m]))
    expected=np.array([-totals['Cltot'],totals['Cmtot'],-totals['Cntot']])
    # Native reports round strip geometry to 4-6 decimal places.
    force_error=float(max(abs(force-target)));moment_error=float(max(abs(moment-expected)))
    if force_error>.003 or moment_error>.003:
        raise ValueError(f'AVL strip force/moment conservation failed: force={force_error:.6g}, moment={moment_error:.6g}')
    return dict(strips=strips,force_coefficient_error=force_error,moment_coefficient_error=moment_error,
                tolerance=.003,axes='x aft, y right, z up; My nose-up')


def analyze(geometry, op, folder, spanwise=24, chordwise=8, *, replay=False):
    folder.mkdir(parents=True,exist_ok=True)
    path=folder/'aircraft.avl'
    from .native import artifact, resolve
    if replay:
        if path.read_text()!=geometry_text(geometry,spanwise,chordwise) or (folder/'avl.in').read_text()!=command(op.alpha_deg):
            raise ValueError('AVL geometry/operating commands differ from bound request')
        _,binary=resolve('avl')
        evidence=dict(solver='avl',executable_sha256=binary,state='completed',returncode=0,
            artifacts=[artifact(folder/n,folder) for n in ('avl.in','avl.log','forces.ft','stability.st','elements.fe')])
    else:
        path.write_text(geometry_text(geometry,spanwise,chordwise))
        evidence=run('avl',command(op.alpha_deg),folder,'avl',('forces.ft','stability.st','elements.fe'))
    evidence['artifacts'].append(artifact(path,folder))
    totals=coefficients((folder/'forces.ft').read_text(),
        ('CLtot','CDind','Cmtot','e','CXtot','CYtot','CZtot','Cltot','Cntot'))
    derivatives=coefficients((folder/'stability.st').read_text(),DERIVATIVES)
    if totals['CLtot']<=0 or totals['CDind']<=0 or not 0<totals['e']<=1.2:
        raise ValueError('AVL invalid lift/induced-drag evidence')
    closure=strip_closure((folder/'elements.fe').read_text(),totals,geometry)
    return dict(native=evidence,coefficients=totals,derivatives=derivatives,closure=closure)


def admission(coarse, fine, geometry, performance, limits):
    ref=geometry.reference;op=performance['loads']['operating_point']
    q=.5*op['density_kg_m3']*op['velocity_m_s']**2
    c=fine['coefficients'];d=dict(fine['derivatives']);old=coarse['coefficients']
    for key in ('CLtot','CDind','Cmtot'):
        if abs(c[key]-old[key])>max(.0005,.02*abs(c[key])):
            raise ValueError('AVL mesh convergence failed for '+key)
    for key in DERIVATIVES:
        if abs(d[key]-coarse['derivatives'][key])>max(.002,.05*abs(d[key])):
            raise ValueError('AVL derivative mesh convergence failed for '+key)
    loads=performance['loads'];alpha=math.radians(performance['alpha_deg'])
    force=np.array(loads['force_n']);cl=float(force@np.array([-math.sin(alpha),0.,math.cos(alpha)]))/(q*ref.area_m2)
    cm=loads['moment_cg_nm'][1]/(q*ref.area_m2*ref.chord_m)
    cd=loads['induced_drag_n']/(q*ref.area_m2)
    if abs(c['CLtot']-cl)>max(.02,.10*abs(cl)) or abs(c['Cmtot']-cm)>.02:
        raise ValueError('Independent lifting model/AVL force or moment disagreement')
    if abs(c['CDind']-cd)>max(.001,.15*abs(cd)):
        raise ValueError('Independent lifting model/AVL induced drag disagreement')
    # Body Munk couple destabilizes pitch/yaw; do not omit it from stability.
    from .body import increments
    from .contracts import OperatingPoint
    point=OperatingPoint.model_validate_json(__import__('json').dumps(op))
    step=.05
    plus=increments(geometry,point.model_copy(update={'alpha_deg':point.alpha_deg+step}))
    minus=increments(geometry,point.model_copy(update={'alpha_deg':point.alpha_deg-step}))
    d['Cma']+=(plus.moment_cg_nm[1]-minus.moment_cg_nm[1])/(math.radians(2*step)*q*ref.area_m2*ref.chord_m)
    def body_cl(value,angle):
        angle=math.radians(angle)
        return float(np.array(value.force_n)@np.array([-math.sin(angle),0.,math.cos(angle)]))/(q*ref.area_m2)
    d['CLa']+=(body_cl(plus,point.alpha_deg+step)-body_cl(minus,point.alpha_deg-step))/math.radians(2*step)
    a=geometry.body
    volume=sum(math.pi*(b.x_m-s.x_m)/12*(s.width_m**2+s.width_m*b.width_m+b.width_m**2) for s,b in zip(a,a[1:]))
    d['Cnb']-=2*volume/(ref.area_m2*ref.span_m)
    margin=-d['Cma']/d['CLa'] if d['CLa']>0 else -1.
    modes=lateral_modes(d,point.velocity_m_s,point.density_kg_m3,ref.area_m2,ref.span_m,ref.mass_kg,
                        ref.inertia_kg_m2[0],ref.inertia_kg_m2[2],math.radians(performance['glide_angle_deg']))
    failures=[]
    if any(not math.isfinite(float(v)) for k,v in modes.items() if k not in ('eigs','spiral_time_to_double_s')):
        failures.append('nonfinite lateral mode')
    if d['Cma']>=0 or not limits['static_margin_min']<=margin<=limits['static_margin_max']:
        failures.append('pitch stability/static margin')
    if d['Clb']>=0 or d['Cnb']<=0 or not math.isfinite(modes['roll_real']) or modes['roll_real']>=0:
        failures.append('lateral static/roll stability')
    if modes['dutch_roll_real']>=0 or modes['dutch_roll_zeta']<limits['dutch_roll_min_damping']:
        failures.append('Dutch roll damping')
    if modes['spiral_time_to_double_s']<limits['spiral_min_time_to_double_s']:
        failures.append('spiral divergence')
    if failures: raise ValueError('AVL admission rejected: '+', '.join(failures))
    return dict(state='passed',static_margin=margin,
        modes={k:(None if k=='spiral_time_to_double_s' and math.isinf(v) else float(v))
               for k,v in modes.items() if k!='eigs'},
        derivatives_with_body=d,
        limitation='Rigid linear lateral modes; Ixz and body rate damping omitted.')
