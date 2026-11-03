"""Parent-side bounded report and artifact validation. Missing evidence is a failure."""
import hashlib
import json
import math
from pathlib import Path
import re
import zipfile

from cem.core import digest
from cem.core import canonical_json


def file_digest(path, maximum=64*1024*1024):
    if path.is_symlink() or not path.is_file() or path.stat().st_size > maximum:
        raise ValueError('Artifact outside evidence budget')
    h = hashlib.sha256()
    with path.open('rb') as f:
        for chunk in iter(lambda: f.read(65536), b''):
            h.update(chunk)
    return h.hexdigest()


def number(value, minimum=None, maximum=None):
    if type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Missing finite numerical evidence')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError('Evidence outside required bounds')
    return value


def physical_accounting(report, payload):
    """Require canonical inventory and explicit enclosure evidence in screening."""
    from cem.glider.geometry import expected_materials
    from cem.glider.model import configuration, SizedGlider
    from cem.glider.requirements import GliderMissionRequirements
    from gliders.model.components import WingParams, FuselageParams, TailParams
    elite = payload['elite']
    req = GliderMissionRequirements.model_validate(payload['requirements'])
    _, cfg = configuration(req, payload['calibration'], elite['process'])
    p = elite['parameters']
    gl = SizedGlider(WingParams(**p['wing']), FuselageParams(**p['fuselage']),
                    TailParams(**p['tail']), cfg, **elite['placement'])
    expected = expected_materials(gl)
    closure, manifest = report['closure'], report['bundle']['parts']
    accounting = closure['part_accounting']
    if accounting.get('state') != 'passed' or accounting.get('expected_parts') != sorted(expected):
        raise ValueError('Missing canonical part accounting')
    names = [part['name'] for part in manifest]
    measured_names = [part['name'] for part in closure['parts']]
    files = [f"{report['output_id']}_{name}.stl" for name in sorted(expected)]
    if (sorted(names) != sorted(expected) or sorted(measured_names) != sorted(expected)
            or accounting.get('files') != files):
        raise ValueError('Incomplete physical part accounting')
    for part in manifest:
        if part['material'] != expected[part['name']] or part['file'] != f"{report['output_id']}_{part['name']}.stl":
            raise ValueError('Noncanonical physical part')
    for part in closure['parts']:
        if part.get('connected_components') != 1 or part.get('material_law') != expected[part['name']]:
            raise ValueError('Missing connected-part material evidence')
    enclosure = closure['spar_enclosure']
    if (enclosure.get('state') != 'passed' or enclosure.get('method') != 'delivered-wing-face-halfspaces-v1'
            or enclosure.get('load_path_state') != 'unverified'):
        raise ValueError('Missing bounded spar enclosure evidence')
    tolerance = number(enclosure.get('tolerance_m'), 0., 2e-7)
    number(enclosure.get('max_outside_m'), 0., tolerance)


def validate(report, payload, expected, directory):
    def finite_tree(value):
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError('Nonfinite report evidence')
        if isinstance(value, dict):
            for child in value.values(): finite_tree(child)
        elif isinstance(value, list):
            for child in value: finite_tree(child)
    finite_tree(report)
    if not isinstance(report, dict) or any(report.get(k) != v for k, v in expected.items()):
        raise ValueError('Stale CEM evidence')
    if report.get('requirements') != payload['requirements'] or report.get('calibration') != payload['calibration']:
        raise ValueError('Report input binding missing')
    if report.get('engineering_certified') is not False:
        raise ValueError('Unsupported certification claim')
    if payload['kind'] == 'discovery':
        if report.get('status') != 'completed' or not isinstance(report.get('candidates'), list) or not isinstance(report.get('per_process'), dict):
            raise ValueError('Missing archive evidence')
        archive = [e for p in report['per_process'].values() for e in p['archive']]
        by_id = {e['elite_id']: e for e in archive}
        identifiers = set()
        for elite in archive:
            if not re.fullmatch(r'[a-f0-9]{16}', elite['elite_id']) or elite['elite_id'] in identifiers:
                raise ValueError('Invalid elite identity')
            identifiers.add(elite['elite_id'])
            revision = digest(dict(process=elite['process'], parameters=elite['parameters'], placement=elite['placement']))[:16]
            if revision != elite['elite_id']:
                raise ValueError('Elite parameters do not match its revision')
            result = elite['result']
            if result.get('valid') is not True or result.get('fail_reasons'):
                raise ValueError('Archive contains failed evidence')
            for key in ('mass_kg', 'CL_trim', 'CD_trim', 'CLmax_est', 'glide_speed_m_s', 'objective_value'):
                number(result.get(key), 0.)
            number(result['validity_envelope']['min_confidence'], .8, 1.)
            number(result['validity_envelope']['min_station_reynolds'], 30000.)
            if result['manufacturability']['state'] != 'passed':
                raise ValueError('Missing process admission')
            if elite.get('novelty_state') != 'not_claimed' or elite.get('verification_state') != 'screened_existing_layout':
                raise ValueError('Unqualified novelty')
        for elite in report['candidates']:
            if elite['elite_id'] not in identifiers or canonical_json(elite) != canonical_json(by_id[elite['elite_id']]):
                raise ValueError('Candidate differs from archive evidence')
        for process, data in report['per_process'].items():
            if any(e['process'] != process for e in data['archive']):
                raise ValueError('Process archive mismatch')
            best = data.get('best')
            if data['archive']:
                if best is None or best['elite_id'] not in identifiers or canonical_json(best) != canonical_json(by_id[best['elite_id']]):
                    raise ValueError('Best candidate differs from archive evidence')
                if best['result']['objective_value'] < max(e['result']['objective_value'] for e in data['archive']):
                    raise ValueError('Best candidate does not match archive ranking')
            elif best is not None:
                raise ValueError('Best candidate lacks archive evidence')
        return
    if (report.get('accepted_for_manufacture') is not False or report.get('status') not in ('screened', 'rejected')
            or report.get('elite_id') != payload['elite']['elite_id'] or report.get('process') != payload['elite']['process']):
        raise ValueError('Invalid design claims')
    closure = report.get('closure')
    if not isinstance(closure, dict) or closure.get('state') not in ('passed', 'failed') or not isinstance(closure.get('failures'), list):
        raise ValueError('Missing closure evidence')
    if report['status'] == 'rejected':
        if closure['state'] != 'failed' and report.get('minimum_performance_passed') is not False:
            raise ValueError('Inconsistent rejected report')
        return
    if closure['state'] != 'passed' or closure['failures'] or report.get('minimum_performance_passed') is not True:
        raise ValueError('Screened design failed closure')
    physical_accounting(report, payload)
    number(closure.get('mass_relative_error'), 0., .05)
    number(closure.get('measured_mass_kg'), 0.000001, payload['requirements']['constraints']['max_mass_kg'])
    aero = closure['reanalysis']
    if aero.get('valid') is not True or aero.get('fail_reasons'):
        raise ValueError('Measured-mass analysis did not pass')
    number(closure.get('measured_spar_stress_pa'), 0., aero['spar']['allowable_with_sf_pa'] * (1+1e-5))
    number(closure.get('measured_spar_root_inertia_m4'), 1e-16)
    for key in ('CL_trim', 'CD_trim', 'CLmax_est', 'glide_speed_m_s', 'objective_value', 'static_margin'):
        number(aero.get(key), 0.)
    number(aero['validity_envelope']['min_confidence'], .8, 1.)
    number(aero['validity_envelope']['min_station_reynolds'], 30000.)
    bands = report['uncertainty']
    if bands.get('samples') != 200 or bands.get('sustained_samples') != 0:
        raise ValueError('Missing unconditional uncertainty evidence')
    for key in ('duration_s', 'distance_m'):
        band = bands['bands'][key]
        if not (0 <= number(band['p10']) <= number(band['p50']) <= number(band['p90'])):
            raise ValueError('Invalid performance band')
    hard = payload['requirements'].get('minimum_performance')
    if hard is not None and bands['bands'][payload['requirements']['objective']]['p10'] < hard:
        raise ValueError('Hard minimum not met at P10')
    manifest = report['bundle']['parts']
    if not isinstance(manifest, list) or not 3 <= len(manifest) <= 16 or len(closure['parts']) != len(manifest):
        raise ValueError('Incomplete mesh accounting')
    seen = set()
    for part in manifest:
        name = part['file']
        if (not re.fullmatch(r'[a-f0-9]{12}_[A-Za-z0-9_-]+\.stl', name)
                or not name.startswith(expected['output_id'] + '_') or name in seen):
            raise ValueError('Unsafe or duplicate artifact name')
        seen.add(name)
        if file_digest(directory / name) != part['sha256']:
            raise ValueError('Artifact revision mismatch')
        measurement = next((m for m in closure['parts'] if m['name'] == part['name']), None)
        if not measurement or measurement['sha256'] != part['sha256']:
            raise ValueError('Missing artifact measurement binding')
        number(measurement['mass_kg'], .000000001)
        number(measurement['volume_m3'], .000000000001)
    bundle = directory / 'bundle.zip'
    if file_digest(bundle) != report.get('bundle_sha256'):
        raise ValueError('Bundle revision mismatch')
    with zipfile.ZipFile(bundle) as z:
        names = z.namelist()
        if len(names) != len(set(names)) or len(names) > 64 or sum(i.file_size for i in z.infolist()) > 64*1024*1024:
            raise ValueError('Invalid archive budget')
        if any(Path(name).name != name or '\\' in name or ':' in name for name in names) or not (seen | {'design.json', 'performance_report.json'}).issubset(names):
            raise ValueError('Invalid archive accounting')
        inner = json.loads(z.read('performance_report.json'))
        if canonical_json(inner) != canonical_json({k:v for k,v in report.items() if k != 'bundle_sha256'}):
            raise ValueError('Archive report differs from validated evidence')
        design = json.loads(z.read('design.json'))
        if design.get('requirements') != payload['requirements'] or design.get('calibration') != payload['calibration']:
            raise ValueError('Bundle input mismatch')
        revision = digest(dict(identity=expected, parameters=payload['elite']['parameters'],
                               placement=design['final_placement'], process=payload['elite']['process']))
        if report.get('design_revision_sha256') != revision or design.get('design_revision_sha256') != revision:
            raise ValueError('Final design revision mismatch')
        for part in manifest:
            if hashlib.sha256(z.read(part['file'])).hexdigest() != part['sha256']:
                raise ValueError('Archive geometry differs from measured artifact')
