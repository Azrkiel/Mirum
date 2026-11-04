"""Independent executable diagnostics; no analytic fallback or verified-gain claim."""
import math
import os
from pathlib import Path
import re
import shutil
import subprocess

import numpy as np

from cem.core import canonical_json
from gliders.model.analysis import lateral_modes
from gliders.model.components import naca
from gliders.search import finite_json
from .physics import atmosphere

TIMEOUT_S = 20
MAX_ARTIFACT = 1024*1024
DERIVATIVES = ('CLa', 'Cma', 'CYb', 'Clb', 'Cnb', 'CYp', 'Clp', 'Cnp', 'CYr', 'Clr', 'Cnr', 'Cmq')


def executable(name):
    from cem.evidence import file_digest
    value = os.environ.get(f'MIRUM_{name.upper()}_BIN', name)
    path = shutil.which(value)
    if path is None:
        return None
    resolved = Path(path).resolve()
    return dict(name=name, sha256=file_digest(resolved, maximum=64*1024*1024))


def avl_geometry(gl):
    """All installed lifting surfaces, including fin/V-tail and actual camber."""
    ref, cg = gl.wing_ref(), gl.mass_summary()['cg']
    f = lambda v: format(float(v), '.12g')
    lines = ['Mirum frozen shape comparison', '0', '0 0 0',
             ' '.join(f(ref[k]) for k in ('S', 'mac', 'b')), ' '.join(map(f, cg)), '0']
    for surf in [gl.wing_surface(), *gl.tail_surfaces()]:
        lines += ['SURFACE', surf.name, '8 1.0 24 1.0']
        if surf.symmetric:
            lines += ['YDUPLICATE', '0']
        for sec in surf.sections:
            lines += ['SECTION', ' '.join(f(x) for x in (sec.x, sec.y, sec.z, sec.chord, sec.twist)),
                      'NACA', surf.airfoil]
    return '\n'.join(lines)+'\n'


def avl_script(label, result):
    return (f"LOAD {label}_avl.avl\nOPER\nA A {result['alpha_trim_deg']:.12g}\n"
            f"X\nST {label}_avl.out\n\nQUIT\n")


def station_inputs(gl, result, req):
    air = atmosphere(req.environment)
    stations = []
    for surf in [gl.wing_surface(), *gl.tail_surfaces()]:
        for i, sec in enumerate(surf.sections):
            alpha = round(result['alpha_trim_deg']+sec.twist, 2)
            stations.append(dict(surface=surf.name, station=i, naca=surf.airfoil,
                chord_m=sec.chord, reynolds=air['density_kg_m3']*result['glide_speed_m_s']*sec.chord/air['dynamic_viscosity_pa_s'],
                alpha_deg=alpha))
    return stations


def xfoil_script(station, output):
    from analysis.airfoil import _build_xfoil_script
    a = station['alpha_deg']
    return _build_xfoil_script('NACA '+station['naca'], dict(alpha_start=a-1., alpha_end=a+1.,
        alpha_step=.5, reynolds=station['reynolds'], mach=0., n_crit=9.), output)


def assess_xfoil(path, station):
    from analysis.airfoil import _parse_polar_file
    from cem.evidence import number
    data = _parse_polar_file(str(path))
    expected = np.arange(station['alpha_deg']-1., station['alpha_deg']+1.01, .5)
    if any(len(data[k]) != len(expected) for k in ('alpha', 'Cl', 'Cd', 'Cm')):
        raise ValueError('XFOIL did not converge at every requested station angle')
    if not np.allclose(data['alpha'], expected, rtol=0, atol=.011):
        raise ValueError('XFOIL operating angles differ from the requested station')
    for key, values in data.items():
        for value in values:
            number(value, 1e-9 if key == 'Cd' else None)
    primary = naca(station['naca']).get_aero_from_neuralfoil(
        alpha=np.array(data['alpha']), Re=station['reynolds'], model_size='large')
    return dict(polar=data, max_abs_cl_difference=float(np.max(np.abs(np.array(data['Cl'])-primary['CL']))),
                max_abs_cd_difference=float(np.max(np.abs(np.array(data['Cd'])-primary['CD']))))


def assess_avl(raw, gl, result):
    from analysis.avl import _KV_RE
    from cem.evidence import number
    kv = {m.group(1): float(m.group(2)) for m in _KV_RE.finditer(raw)}
    for key in ('CLtot', 'CDind', 'Cmtot', 'e', *DERIVATIVES):
        number(kv.get(key))
    if kv['CLtot'] <= 0 or kv['CDind'] <= 0 or not 0 < kv['e'] <= 1.2:
        raise ValueError('Invalid AVL lifting/drag evidence')
    lim, ref, ms = gl.cfg['limits'], gl.wing_ref(), gl.mass_summary()
    margin = -kv['Cma']/kv['CLa'] if kv['CLa'] > 0 else -1.
    modes = lateral_modes(kv, result['glide_speed_m_s'], gl.cfg['mission']['air_density_kg_m3'],
        ref['S'], ref['b'], ms['mass'], ms['Ixx'], ms['Izz'], math.radians(result['glide_angle_deg']))
    failures = []
    if abs(kv['Cmtot']) > .01:
        failures.append(f"pitch moment {kv['Cmtot']:.5g} exceeds |Cm|=0.01")
    if abs(kv['CLtot']/result['CL_trim']-1) > .1:
        failures.append('lift differs from primary equilibrium by more than 10%')
    if kv['Cma'] >= 0 or not lim['static_margin_min'] <= margin <= lim['static_margin_max']:
        failures.append('pitch stability/static margin outside mission envelope')
    if kv['Clb'] >= 0 or kv['Cnb'] <= 0 or modes['roll_real'] >= 0:
        failures.append('lateral static/roll stability failed')
    if modes['dutch_roll_real'] >= 0 or modes['dutch_roll_zeta'] < lim['dutch_roll_min_damping']:
        failures.append('Dutch roll damping outside mission envelope')
    if modes['spiral_time_to_double_s'] < lim['spiral_min_time_to_double_s']:
        failures.append('spiral divergence outside mission envelope')
    if failures:
        raise ValueError('AVL: '+'; '.join(failures))
    modes.pop('eigs')
    return finite_json(dict(coefficients=kv, static_margin=margin, modes=modes))


def run_solver(name, script, directory):
    value = os.environ.get(f'MIRUM_{name.upper()}_BIN', name)
    # Standard executables only; no shell, inherited PATH selects server-installed tools.
    subprocess.run([value], input=script, text=True, cwd=directory, timeout=TIMEOUT_S,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)


def _record(path, root):
    from cem.evidence import file_digest
    return dict(file=path.relative_to(root).as_posix(), sha256=file_digest(path, MAX_ARTIFACT))


def recheck_pair(gliders, arms, req, directory):
    root = directory.resolve()
    folder = root/'rechecks'
    folder.mkdir(parents=True, exist_ok=True)
    tools = {name: executable(name) for name in ('xfoil', 'avl')}
    values = []
    for label, gl, arm in zip(('baseline', 'shaped'), gliders, arms):
        value = dict(revision_sha256=arm['revision_sha256'])
        for name in ('xfoil', 'avl'):
            evidence = dict(state='not_evaluated', artifacts=[])
            value[name] = evidence
            if not arm['result'].get('valid'):
                evidence['reason'] = 'Paired arm failed primary admission'
                continue
            if tools[name] is None:
                evidence.update(state='unavailable', reason='Independent executable is not installed')
                continue
            try:
                if name == 'xfoil':
                    evidence['stations'] = []
                    for i, station in enumerate(station_inputs(gl, arm['result'], req)):
                        stem = f'{label}_xfoil_{i}'
                        out = folder/f'{stem}.out'
                        script = xfoil_script(station, out.name)
                        inp = folder/f'{stem}.in'
                        inp.write_text(script)
                        out.unlink(missing_ok=True)
                        run_solver(name, script, folder)
                        evidence['artifacts'].extend([_record(inp, root), _record(out, root)])
                        evidence['stations'].append(dict(inputs=station, **assess_xfoil(out, station)))
                    evidence['state'] = 'completed'
                else:
                    geometry = folder/f'{label}_avl.avl'
                    geometry.write_text(avl_geometry(gl))
                    out = folder/f'{label}_avl.out'
                    result = arm['result']
                    script = avl_script(label, result)
                    inp = folder/f'{label}_avl.in'
                    inp.write_text(script)
                    out.unlink(missing_ok=True)
                    run_solver(name, script, folder)
                    evidence['artifacts'].extend([_record(p, root) for p in (geometry, inp, out)])
                    evidence['assessment'] = assess_avl(out.read_text(), gl, result)
                    evidence['state'] = 'passed'
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                evidence.update(state='failed', reason=type(exc).__name__+': '+str(exc)[:240])
        values.append(value)
    completed = all(v['xfoil']['state'] == 'completed' and v['avl']['state'] == 'passed' for v in values)
    return dict(state='completed' if completed else 'incomplete', executables=tools, arms=values,
        scope='XFOIL geometric station-angle polars; AVL installed lifting surfaces, no fuselage increments.',
        missing=['Coupled independent whole-aircraft viscous performance', 'Physical construction closure'])


def validate_rechecks(value, arms, requested, directory, gliders=None, req=None):
    from cem.evidence import file_digest
    if not requested:
        if value != dict(state='not_requested', missing=['XFOIL station polars', 'AVL trim and modes']):
            raise ValueError('Unexpected independent recheck claim')
        return
    if value.get('state') not in ('completed', 'incomplete') or len(value.get('arms', [])) != 2:
        raise ValueError('Missing paired independent evidence')
    if value.get('executables') != {name: executable(name) for name in ('xfoil', 'avl')}:
        raise ValueError('Independent executable identity changed')
    for label, arm, supplied, gl in zip(('baseline', 'shaped'), arms, value['arms'], gliders):
        if supplied.get('revision_sha256') != arm['revision_sha256']:
            raise ValueError('Recheck differs from comparison revision')
        for name in ('xfoil', 'avl'):
            evidence = supplied[name]
            if evidence['state'] not in ('not_evaluated', 'unavailable', 'failed', 'completed' if name == 'xfoil' else 'passed'):
                raise ValueError('Invalid independent solver state')
            tool = value['executables'][name]
            if evidence['state'] == 'unavailable' and tool is not None:
                raise ValueError('Unavailable solver has inconsistent identity')
            if evidence['state'] in ('completed', 'passed'):
                if not arm['result']['valid'] or not tool or not re.fullmatch('[a-f0-9]{64}', tool['sha256']):
                    raise ValueError('Missing independent executable identity/admission')
            artifacts = evidence['artifacts']
            names = []
            for item in artifacts:
                filename = item['file']
                if not re.fullmatch(rf'rechecks/{label}_(xfoil_\d+|avl)\.(in|out|avl)', filename):
                    raise ValueError('Unsafe independent artifact name')
                path = directory/filename
                if path.parent.is_symlink() or file_digest(path, MAX_ARTIFACT) != item['sha256']:
                    raise ValueError('Independent solver artifact changed')
                names.append(filename)
            if len(set(names)) != len(names):
                raise ValueError('Duplicate independent artifact')
            if evidence['state'] == 'completed' and name == 'xfoil':
                stations = station_inputs(gl, arm['result'], req)
                if len(evidence['stations']) != len(stations) or len(names) != 2*len(stations):
                    raise ValueError('Incomplete station polar coverage')
                for i, (station, measured) in enumerate(zip(stations, evidence['stations'])):
                    out = directory/f'rechecks/{label}_xfoil_{i}.out'
                    inp = directory/f'rechecks/{label}_xfoil_{i}.in'
                    if out.relative_to(directory).as_posix() not in names or inp.relative_to(directory).as_posix() not in names:
                        raise ValueError('Missing station artifacts')
                    if inp.read_text() != xfoil_script(station, out.name):
                        raise ValueError('XFOIL input differs from frozen station')
                    actual = dict(inputs=station, **assess_xfoil(out, station))
                    if canonical_json(actual) != canonical_json(measured):
                        raise ValueError('XFOIL assessment differs from raw evidence')
            if evidence['state'] == 'passed' and name == 'avl':
                if set(names) != {f'rechecks/{label}_avl.{ext}' for ext in ('avl', 'in', 'out')}:
                    raise ValueError('Missing AVL artifacts')
                if (directory/f'rechecks/{label}_avl.avl').read_text() != avl_geometry(gl):
                    raise ValueError('AVL geometry differs from frozen surfaces')
                if (directory/f'rechecks/{label}_avl.in').read_text() != avl_script(label, arm['result']):
                    raise ValueError('AVL operating point differs from frozen arm')
                actual = assess_avl((directory/f'rechecks/{label}_avl.out').read_text(), gl, arm['result'])
                if canonical_json(actual) != canonical_json(evidence['assessment']):
                    raise ValueError('AVL assessment differs from raw evidence')
    completed = all(v['xfoil']['state'] == 'completed' and v['avl']['state'] == 'passed' for v in value['arms'])
    if value['state'] != ('completed' if completed else 'incomplete'):
        raise ValueError('Independent recheck summary differs from evidence')
