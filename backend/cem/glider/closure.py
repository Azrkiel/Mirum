"""Independent mesh import, mass/inertia re-prediction and immutable-placement trim."""
import copy
import math
from pathlib import Path

import numpy as np

from gliders.model.assembly import Glider
from .geometry import measure_part
from .model import SizedGlider, evaluate_candidate


class MeasuredGlider(SizedGlider):
    def balance(self, x_cg_target):
        # Geometry, payload and ballast are fixed for the closure solve. The
        # solver must not rebalance an unbuilt candidate and call it measured.
        pass

    def mass_summary(self, include_ballast=True, include_payload=True):
        parts = self.measured_parts
        masses = [p['mass_kg'] for p in parts]
        centers = [np.asarray(p['cg_m']) for p in parts]
        inertias = [np.asarray(p['inertia_kg_m2']) for p in parts]
        by_part = {p['name']: p['mass_kg'] for p in parts}
        base = Glider.mass_items(self, include_ballast, include_payload)
        for item in base:
            if item.name in ('payload', 'ballast', 'hardware'):
                masses.append(item.mass)
                centers.append(np.array([item.x, item.y, item.z]))
                inertias.append(np.zeros((3, 3)))
                by_part[item.name] = by_part.get(item.name, 0.) + item.mass
        mass = sum(masses)
        cg = sum(m * c for m, c in zip(masses, centers)) / mass
        inertia = np.zeros((3, 3))
        for m, c, moment in zip(masses, centers, inertias):
            displacement = c - cg
            inertia += moment + m * (np.dot(displacement, displacement) * np.eye(3) - np.outer(displacement, displacement))
        return dict(mass=mass, cg=cg, Ixx=inertia[0, 0], Iyy=inertia[1, 1], Izz=inertia[2, 2], by_part=by_part)


def verify(gl, sized_result, bundle, directory, request, mission, calibration, process):
    failures, parts = [], []
    try:
        for item in bundle['parts']:
            # All paths originate from the trusted builder, still enforce local
            # containment and exact revision binding before reading any bytes.
            path = (directory / item['file']).resolve()
            if not path.is_relative_to(directory.resolve()):
                raise ValueError('Artifact path outside bundle')
            measured = measure_part(path, item['material'])
            if measured['sha256'] != item['sha256']:
                raise ValueError('Mesh changed after construction')
            parts.append(dict(name=item['name'], **measured))
        lower = np.min([p['bounds_m'][0] for p in parts], axis=0)
        upper = np.max([p['bounds_m'][1] for p in parts], axis=0)
        if upper[0] - lower[0] > mission.constraints.max_length_m + 1e-6 or upper[1] - lower[1] > mission.constraints.max_span_m + 1e-6:
            failures.append('delivered mesh exceeds the approved dimensional envelope')
        measured = MeasuredGlider(gl.wing, gl.fuse, gl.tail, copy.deepcopy(gl.cfg),
            ballast_kg=gl.ballast_kg, tail_incidence_deg=gl.tail_incidence_deg,
            payload_x=gl.payload_x, wing_x=gl.wing_x)
        measured.measured_parts = parts
        nose, end = measured.fuse.pod_range()
        fraction = (measured.wing_position()[0] - nose) / (end - nose)
        measured.cfg['ranges']['fuselage']['wing_station_in_pod'] = (fraction, fraction)
        mass = measured.mass_summary()['mass']
        delta = abs(mass / sized_result['mass_kg'] - 1)
        if delta > .05:
            failures.append('measured skin-equivalent mass differs from sizing by more than 5 percent')
        result = evaluate_candidate(measured, request, mission, calibration, process)
        if not result['valid']:
            failures.append('measured-mass aerodynamic admission failed: ' + result['fail_reasons'])
        if abs(measured.tail_incidence_deg - gl.tail_incidence_deg) > .05:
            failures.append('measured-mass trim requires a geometry revision')
        spar_part = next(p for p in parts if p['name'] == 'spar')
        bounds = np.array(spar_part['bounds_m'])
        # Recover the uniform Y-axis rod's actual polygon section moment from
        # its measured solid inertia. Do not substitute an ideal circular formula
        # for the delivered tessellation's cross-section.
        moment = np.array(spar_part['inertia_kg_m2'])
        density = spar_part['material_law']['density_kg_m3']
        length = bounds[1, 1] - bounds[0, 1]
        section_ixx = (moment[1, 1] + moment[0, 0] - moment[2, 2]) / (2 * density * length)
        if not math.isfinite(section_ixx) or section_ixx <= 0:
            raise ValueError('Missing measured root section inertia')
        extreme_z = max(abs(bounds[:, 2] - spar_part['cg_m'][2]))
        stress = gl.spar()['root_moment_nm'] * extreme_z / section_ixx
        if stress > gl.spar()['allowable_with_sf_pa'] * (1 + 1e-5):
            failures.append('measured spar fails launch bending')
        return dict(state='failed' if failures else 'passed', failures=failures,
                    mass_relative_error=float(delta), measured_mass_kg=float(mass),
                    measured_spar_stress_pa=float(stress), parts=parts, reanalysis=result,
                    measured_spar_root_inertia_m4=float(section_ixx),
                    delivered_bounds_m=[lower.tolist(), upper.tolist()],
                    scope='Mesh-measured OML skin-equivalent mass and solid rod; not manufactured-part closure',
                    manufacturing_ready=False)
    except (ValueError, KeyError, OSError, StopIteration, TypeError):
        return dict(state='failed', failures=['Required bounded mesh or closure evidence is unavailable'],
                    manufacturing_ready=False)
