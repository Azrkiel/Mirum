"""Build or independently reread the thermal bar in a bounded native worker."""

from base64 import b64decode, b64encode
from hashlib import sha256
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import cadquery as cq

from _occt_measure import measure_step

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from engineering.program.units import THERMAL_UNIT_CONTRACT
from engineering.thermal_bar import GEOMETRY_LIMIT_MM, LENGTH_FIELDS, ThermalBarSpec, si_case
from vendor.mirum_kernel import thermal


def _geometry_witness(table, dimensions):
    lo = [0.0] * 3
    maximum_deviation = max(abs(a - b) for a, b in zip(table['bounds'], lo + dimensions, strict=True))
    volume = dimensions[0] * dimensions[1] * dimensions[2]
    matched = []
    for axis in range(3):
        for sign in (-1, 1):
            normal = [0, 0, 0]
            normal[axis] = sign
            center = [dimension / 2 for dimension in dimensions]
            center[axis] = 0 if sign == -1 else dimensions[axis]
            area = volume / dimensions[axis]
            faces = [face for face in table['faces'] if face['surface'] == 'plane'
                     and max(abs(a - b) for a, b in zip(face['normal'], normal)) <= 1e-10
                     and max(abs(a - b) for a, b in zip(face['center'], center)) <= GEOMETRY_LIMIT_MM
                     and abs(face['area'] - area) <= area * 1e-8]
            matched.append(dict(axis='XYZ'[axis], direction=sign, faces=faces))
    mismatches = (sum(len(row['faces']) != 1 for row in matched) + abs(len(table['faces']) - 6)
                  + abs(table['solid_count'] - 1) + abs(table['closed_solid_count'] - 1))
    return dict(bounds_mm=table['bounds'], volume_mm3=table['volume'], matched_faces=matched,
                geometry_measurements=dict(bounds_error=maximum_deviation,
                                           volume_error=abs(table['volume'] - volume) / volume,
                                           face_mismatches=mismatches),
                native_versions=table['native_versions'])


def main():
    request = json.load(sys.stdin)
    spec = ThermalBarSpec.model_validate_json(json.dumps(request['specification'])).model_dump(mode='json')
    case = si_case(spec)
    dims = [spec[key] for key in ('length_mm', 'width_mm', 'depth_mm')]
    with TemporaryDirectory(prefix='mirum-thermal-') as directory:
        path = Path(directory) / 'bar.step'
        if request['operation'] == 'build':
            cq.exporters.export(cq.Solid.makeBox(*dims), str(path), exportType='STEP')
            step = path.read_bytes()
        elif request['operation'] == 'inspect':
            step = b64decode(request['step'], validate=True)
            path.write_bytes(step)
        else:
            raise ValueError('Unsupported thermal worker operation')
        table = measure_step(str(path))['table']
    geometry = _geometry_witness(table, dims)
    analysis = thermal.run(case)
    solver_values = {key: getattr(case, key.removesuffix('_mm') + '_m') for key in LENGTH_FIELDS}
    conversions = dict(contract=THERMAL_UNIT_CONTRACT, lengths={
        key: dict(reviewed_mm=spec[key], solver_m=value) for key, value in solver_values.items()},
        passthrough_units=dict(sink_k='K', reference_k='K', uniform_delta_k='delta_K', power_w='W',
                               conductivity_w_m_k='W/(m*K)', alpha_per_k='1/K'))
    output = {'report': dict(schema='mirum_thermal_bar_report_v1', specification=spec, units=conversions,
                             step_sha256=sha256(step).hexdigest(), geometry=geometry, analysis=analysis)}
    if request['operation'] == 'build':
        output['step'] = b64encode(step).decode('ascii')
    print(json.dumps(output, allow_nan=False))


if __name__ == '__main__':
    main()
