"""Declare all exact-foil zero-circulation requests at the recorded 14 m/s, 0 deg target.

These are additional required windows, kept separate from 6 m/s trial windows.
No union or gap bridging is implied. Nonzero trial/converged requests need their
own passed bank coverage and are not inferred from initialization.
"""
import argparse
import json
import math
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT),str(ROOT/'backend')]
import aerosandbox as asb
import numpy as np
from cem.core import canonical_json, digest
from cem.glider.families.coupled_aero import panels


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--declaration',type=Path,required=True)
    p.add_argument('--output',type=Path,required=True)
    args=p.parse_args()
    original=json.loads(args.declaration.read_text())
    requests={}
    air=asb.Atmosphere(altitude=0)
    for state in original['states']:
        for panel in panels(state['installed'],4):
            v=np.array([1.,0.,0.])
            v-=float(v@panel['span'])*panel['span']
            re=14*float(air.density()/air.dynamic_viscosity())*np.linalg.norm(v)*panel['chord']
            alpha=math.degrees(math.atan2(float(v@panel['normal']),float(v@panel['chord_direction'])))
            requests.setdefault(panel['foil'],[]).append(dict(reynolds=float(re),alpha_deg=alpha,
                installed_sha256=state['installed']['installed_sha256'],surface=panel['surface']))
    banks=[]
    for key,rows in requests.items():
        lower=math.floor(min(r['reynolds'] for r in rows)/1000)*1000.
        upper=math.ceil(max(r['reynolds'] for r in rows)/1000)*1000.
        amin=math.floor(min(r['alpha_deg'] for r in rows)*2)/2
        amax=math.ceil(max(r['alpha_deg'] for r in rows)*2)/2
        banks.append(dict(foil=key,reynolds=[lower,upper],angles=[amin,(amin+amax)/2,amax]))
    declaration=dict(original_declaration_sha256=digest(original),scope='additional_required_14mps_0deg_initialization_only',
        states=[],foils=original['foils'],required_banks=banks,requests=requests,
        derivation='Retain the recorded 14 m/s, 0 deg reference request; bracket all six exact installed states outward to 1000 Re and half-degree bounds. No claim of full max-15 m/s mission coverage or nonzero-circulation trials.',
        production_admission=False,full_mission_qualification=False)
    with args.output.open('x') as stream:stream.write(canonical_json(declaration))


if __name__=='__main__':main()
