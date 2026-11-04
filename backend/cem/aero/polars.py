"""Native station polars; bounded alpha/log-Re interpolation without extrapolation."""
import math
import re

import numpy as np

from cem.core import digest
from .contracts import NativeIdentity, Artifact, StationPolar, Transition
from .native import run, NativeFailure


class PolarCoverageError(ValueError):
    """The queried station is outside the supported polar evidence domain."""


def check_polar_controls(raw,naca,reynolds,transition):
    """Check reported foil/Re/Mach/transition, allowing native header rounding."""
    value=r'[-+]?(?:\d+(?:\.\d*)?|\.\d+)'
    foil=re.search(r'Calculated polar for:\s*NACA\s*(\d{4})\b',raw)
    reynolds_match=re.search(r'\bRe\s*=\s*('+value+r')\s*[eE]\s*([-+]?\d+)',raw)
    mach=re.search(r'\bMach\s*=\s*('+value+r')(?=\s|$)',raw)
    ncrit=re.search(r'\bNcrit\s*=\s*('+value+r')\s+('+value+r')(?=\s|$)',raw)
    trips=re.search(r'\bxtrf\s*=\s*('+value+r')\s*\(top\)\s*('+value+r')\s*\(bottom\)',raw)
    if any(v is None for v in (foil,reynolds_match,mach,ncrit,trips)):
        raise ValueError('Missing XFOIL native operating metadata')
    exponent=int(reynolds_match[2])
    if not -12<=exponent<=12:
        raise ValueError('Invalid XFOIL native Reynolds metadata')
    mantissa=reynolds_match[1]
    decimals=len(mantissa.split('.',1)[1]) if '.' in mantissa else 0
    quantum=.5*10.**(exponent-decimals)
    reported_re=float(mantissa)*10.**exponent
    observed=dict(reynolds=reported_re,mach=float(mach[1]),
        ncrit=[float(ncrit[1]),float(ncrit[2])],top_xtr=float(trips[1]),bottom_xtr=float(trips[2]))
    if (not math.isfinite(reported_re) or quantum>500.000001 or foil[1]!=naca
            or abs(reported_re-reynolds)>quantum+1e-6 or observed['mach']!=0.
            or any(abs(v-transition.n_crit)>.000501 for v in observed['ncrit'])
            or abs(observed['top_xtr']-transition.top_xtr)>.000501
            or abs(observed['bottom_xtr']-transition.bottom_xtr)>.000501):
        raise ValueError('XFOIL native operating controls differ from requested polar')
    return observed


def script(naca, reynolds, angles, transition, output, panels=160):
    if not (len(naca) == 4 and naca.isdigit()) or not 30000 <= reynolds <= 1000000:
        raise ValueError('Unsupported XFOIL airfoil/Reynolds envelope')
    if (len(angles) < 3 or len(angles) > 81 or not all(math.isfinite(a) for a in angles)
            or min(angles) < -10 or max(angles) > 12):
        raise ValueError('Unsupported XFOIL angle envelope')
    if any(b <= a for a,b in zip(angles,angles[1:])) or panels not in (160, 240):
        raise ValueError('Unordered angles or unsupported XFOIL panel count')
    if not output.replace('_','').replace('.','').isalnum():
        raise ValueError('Unsafe polar output name')
    # Every angle is explicit: a missing converged row cannot be interpolated over.
    lines = ['PLOP','G F','','NACA '+naca,'PPAR',f'N {panels}','','',
        'PANE','OPER',f'VISC {reynolds:.12g}','MACH 0','VPAR',
        f'N {transition.n_crit:.12g}',f'XTR {transition.top_xtr:.12g} {transition.bottom_xtr:.12g}',
        '','ITER 150','ALFA 0','PACC',output,'']
    lines += [f'ALFA {a:.8g}' for a in angles]
    lines += ['PACC','','QUIT']
    return '\n'.join(lines)+'\n'


def query(binding, naca, reynolds, angles, folder, transition=None, panels=160, *, replay=False):
    from analysis.airfoil import _parse_polar_file
    transition = transition or Transition()
    tag = digest(dict(naca=naca,re=reynolds,angles=angles,transition=transition.model_dump(),panels=panels))[:16]
    stem = 'polar_'+tag
    commands=script(naca,reynolds,angles,transition,stem+'.out',panels)
    if replay:
        from .native import artifact, resolve
        if (folder/(stem+'.in')).read_text()!=commands:
            raise ValueError('XFOIL commands differ from bound polar query')
        _,binary=resolve('xfoil')
        evidence=dict(solver='xfoil',state='completed',returncode=0,executable_sha256=binary,
                      artifacts=[artifact(folder/(stem+ext),folder)
                      for ext in ('.in','.log','.out')])
    else:
        evidence = run('xfoil',commands,folder,stem,(stem+'.out',))
    data = _parse_polar_file(str(folder/(stem+'.out')))
    if any(not math.isfinite(value) for values in data.values() for value in values):
        evidence['state']='invalid_output'
        raise NativeFailure('XFOIL nonfinite native polar output',evidence)
    if len(data['alpha']) != len(angles) or not np.allclose(data['alpha'],angles,rtol=0,atol=.001):
        evidence['state'] = 'nonconvergence'
        evidence['coverage']=dict(naca=naca,reynolds=float(reynolds),
            transition=transition.model_dump(mode='json'),requested_alpha_deg=list(angles),
            converged_alpha_deg=data['alpha'],
            missing_alpha_deg=[float(a) for a in angles if not any(abs(a-b)<=.001 for b in data['alpha'])])
        raise NativeFailure('XFOIL nonconvergence: incomplete requested polar coverage', evidence)
    try:
        check_polar_controls((folder/(stem+'.out')).read_text(),naca,reynolds,transition)
    except ValueError as exc:
        evidence['state']='invalid_output'
        raise NativeFailure(str(exc),evidence) from exc
    identity = NativeIdentity(solver='xfoil',executable_sha256=evidence['executable_sha256'],
        artifacts=tuple(Artifact(**v) for v in evidence['artifacts']))
    return StationPolar(binding=binding,naca=naca,reynolds=float(reynolds),mach=0.,transition=transition,
        alpha_deg=tuple(data['alpha']),cl=tuple(data['Cl']),cd=tuple(data['Cd']),cm=tuple(data['Cm']),native=identity)


class PolarBank:
    def __init__(self, polars, binding):
        self.binding = binding
        self.polars = tuple(polars)
        self.groups = {}
        for p in self.polars:
            if p.binding != binding:
                raise ValueError('Stale station polar binding')
            self.groups.setdefault(p.naca, []).append(p)
        for group in self.groups.values():
            group.sort(key=lambda p:p.reynolds)
            if len(group)<2 or len({p.reynolds for p in group}) != len(group):
                raise ValueError('Missing or duplicate Reynolds coverage')
            if any((p.transition,p.mach,p.alpha_deg) != (group[0].transition,group[0].mach,group[0].alpha_deg) for p in group):
                raise ValueError('Inconsistent transition/Mach/alpha polar grid')

    def coefficients(self, naca, reynolds, alpha):
        if not all(math.isfinite(float(x)) for x in (reynolds,alpha)):
            raise ValueError('Nonfinite station query')
        group = self.groups.get(naca, ())
        if not group or not group[0].reynolds <= reynolds <= group[-1].reynolds:
            raise PolarCoverageError(f'Station Reynolds number {reynolds:.6g} outside polar coverage for NACA {naca}')
        i = max(0, min(len(group)-2, int(np.searchsorted([p.reynolds for p in group], reynolds))-1))
        a,b = group[i:i+2]
        if not a.alpha_deg[0] <= alpha <= a.alpha_deg[-1]:
            raise PolarCoverageError(f'Effective station angle {alpha:.6g} outside polar coverage for NACA {naca}')
        j = max(0, min(len(a.alpha_deg)-2, int(np.searchsorted(a.alpha_deg, alpha))-1))
        for p in (a,b):
            if p.cl[j+1] <= p.cl[j]:
                raise PolarCoverageError('Station polar outside attached positive-slope envelope')
        w = math.log(reynolds/a.reynolds)/math.log(b.reynolds/a.reynolds)
        values = [(1-w)*np.interp(alpha,a.alpha_deg,getattr(a,k))+w*np.interp(alpha,b.alpha_deg,getattr(b,k))
                  for k in ('cl','cd','cm')]
        return tuple(float(x) for x in values)
