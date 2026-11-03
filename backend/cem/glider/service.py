"""CEM stages and revision-bound reports; unqualified output is never accepted."""
from dataclasses import dataclass
import os
from pathlib import Path
import tempfile
import zipfile

from cem import VERSION
from cem.core import canonical_json, digest, output_id
from cem.provenance import provenance
from gliders.model.components import WingParams, FuselageParams, TailParams
from gliders.search import finite_json
from .requirements import GliderMissionRequirements
from .calibration import version
from .feasibility import feasibility
from .discovery import discover
from .model import configuration, SizedGlider
from .geometry import export_bundle
from .closure import verify
from .physics import launch_state
from .uncertainty import uncertainty

LIMITS = ['Screening prediction, not flight validation or engineering certification.',
          'Low-Re airfoil uncertainty; VLM stability with Ixz neglected.',
          'No gusts, landing loads, flexible aeroelasticity, spar joints or torsional qualification.',
          'Engineering priors are not calibrated test confidence intervals.',
          'Analysis OML/templates lack physical shell/rib/cavity/toolpath verification.']


def identity(req, calibration):
    revision = provenance()
    return dict(output_id=output_id(req, revision['cem_version'], version(calibration)), cem_version=revision['cem_version'],
                implementation_sha256=revision['implementation_sha256'],
                calibration_version=version(calibration), requirements_sha256=digest(req.model_dump(mode='json')))


def run_discovery(req, calibration, directory, progress=lambda value: None):
    if req.environment.kind in ('thermal', 'ridge'):
        raise ValueError('Thermal/ridge models are not qualified; select still air, wind or uniform uplift')
    result = discover(req, calibration, progress)
    result.update(**identity(req, calibration), requirements=req.model_dump(mode='json'),
                  feasibility=feasibility(req, calibration), model_limits=LIMITS,
                  calibration=calibration, provenance=provenance(), engineering_certified=False)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'result.json').write_text(canonical_json(result))
    return result


def build_design(req, calibration, elite, directory):
    process = elite['process']
    request, cfg = configuration(req, calibration, process)
    p = elite['parameters']
    gl = SizedGlider(WingParams(**p['wing']), FuselageParams(**p['fuselage']), TailParams(**p['tail']),
                    cfg, **elite['placement'])
    ids = identity(req, calibration)
    attempts = []
    for attempt in range(3):
        bundle = export_bundle(gl, directory, ids['output_id'])
        closure = verify(gl, elite['result'], bundle, directory, request, req, calibration, process)
        attempts.append(dict(attempt=attempt+1, tail_incidence_deg=gl.tail_incidence_deg,
                             state=closure['state'], failures=closure['failures']))
        if closure['failures'] != ['measured-mass trim requires a geometry revision']:
            break
        if attempt < 2:
            # Correct the geometry, then rebuild and independently re-measure;
            # never attach a new trim result to stale exported geometry.
            gl.tail_incidence_deg = closure['reanalysis']['tail_incidence_deg']
    return _package(req, calibration, elite, directory, gl, bundle, closure, attempts)


def _package(req, calibration, elite, directory, gl, bundle, closure, attempts):
    ids, p, process = identity(req, calibration), elite['parameters'], elite['process']
    reanalysis = closure.get('reanalysis', {})
    bands = uncertainty(req, reanalysis, calibration, process, launch_state(req.launch, calibration)) if reanalysis.get('valid') else None
    p10 = bands['bands'][req.objective]['p10'] if bands and bands['bands'][req.objective] else None
    hard_pass = req.minimum_performance is None or (p10 is not None and p10 >= req.minimum_performance)
    reference = req.reference_target
    predicted = reanalysis.get('objective_value')
    report = finite_json(dict(**ids, elite_id=elite['elite_id'], requirements=req.model_dump(mode='json'),
        status='screened' if closure['state'] == 'passed' and hard_pass else 'rejected',
        accepted_for_manufacture=False, engineering_certified=False,
        process=process, feasibility=feasibility(req, calibration), uncertainty=bands,
        reference_gap=max(0., reference-predicted) if reference and predicted is not None else None,
        minimum_performance_passed=hard_pass, closure=closure, closure_attempts=attempts, bundle=bundle,
        assumptions=launch_state(req.launch, calibration)['assumptions'], model_limits=LIMITS,
        calibration=calibration, provenance=provenance(), capabilities=dict(picogk_builder=False, manufactured_geometry_closure=False),
        artifact_hashes={p.name: __import__('hashlib').sha256(p.read_bytes()).hexdigest()
                         for p in sorted(directory.iterdir()) if p.is_file()}))
    # Canonical JSON is deterministic. Archives use fixed timestamps too.
    from gliders.design_spec import placement
    final_placement = placement(gl)
    design_revision = digest(dict(identity=ids, parameters=p, placement=final_placement, process=process))
    report['design_revision_sha256'] = design_revision
    design = dict(**ids, elite=elite, requirements=req.model_dump(mode='json'), calibration=calibration,
                  final_placement=final_placement, design_revision_sha256=design_revision)
    (directory / 'design.json').write_text(canonical_json(finite_json(design)))
    (directory / 'performance_report.json').write_text(canonical_json(report))
    with zipfile.ZipFile(directory / 'bundle.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        for path in sorted(directory.iterdir()):
            if path.is_file() and path.name != 'bundle.zip':
                entry = zipfile.ZipInfo(path.name, date_time=(1980, 1, 1, 0, 0, 0))
                entry.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(entry, path.read_bytes())
    from cem.evidence import file_digest
    report['bundle_sha256'] = file_digest(directory / 'bundle.zip')
    (directory / 'result.json').write_text(canonical_json(report))
    return report


@dataclass(frozen=True)
class SizedDesign:
    requirements: GliderMissionRequirements
    elite: dict


@dataclass(frozen=True)
class GeometryBundle:
    directory: Path
    manifest: dict
    glider: SizedGlider
    request: object


class GliderCEM:
    name = 'glider'
    version = VERSION
    Requirements = GliderMissionRequirements

    def __init__(self, calibration):
        self.calibration = calibration
        self.version = provenance()['cem_version']

    def feasibility(self, req):
        return feasibility(req, self.calibration)

    def size(self, req, budget):
        value = req.model_dump(mode='json')
        value['discovery']['budget_evals'] = budget
        req = GliderMissionRequirements.model_validate(value)
        if req.environment.kind in ('thermal', 'ridge'):
            raise ValueError('Advanced lift-region models are not qualified')
        result = discover(req, self.calibration)
        if not result['candidates']:
            raise ValueError('No design meets the admitted model envelope')
        elite = max(result['candidates'], key=lambda e: e['result']['objective_value'])
        return SizedDesign(req, elite)

    def build(self, design):
        req, elite = design.requirements, design.elite
        request, cfg = configuration(req, self.calibration, elite['process'])
        p = elite['parameters']
        gl = SizedGlider(WingParams(**p['wing']), FuselageParams(**p['fuselage']), TailParams(**p['tail']),
                        cfg, **elite['placement'])
        root = Path(os.environ.get('MIRUM_WORK_DIR', '/tmp/mirum/work')) / 'cem/stages'
        root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix='glider-', dir=root))
        manifest = export_bundle(gl, directory, identity(req, self.calibration)['output_id'])
        return GeometryBundle(directory, manifest, gl, request)

    def verify(self, design, bundle):
        return verify(bundle.glider, design.elite['result'], bundle.manifest, bundle.directory,
                      bundle.request, design.requirements, self.calibration, design.elite['process'])

    def report(self, design, bundle, verification):
        return _package(design.requirements, self.calibration, design.elite, bundle.directory,
                        bundle.glider, bundle.manifest, verification, [])
