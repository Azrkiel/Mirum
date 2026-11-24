"""Run the pinned editable plate worker and map its evidence onto the product contract."""

from base64 import b64decode, b64encode
from hashlib import sha256
import json
import os
from pathlib import Path

from vendor.mirum_editable_plate import AXIS_TOL, LINEAR_TOL, PART_ID, VOLUME_TOL

from .capabilities import EDITABLE_PLATE
from .contracts import ArtifactIdentity, CheckState, GeneratorCheckResult, Measurement, Outcome
from .editable_plate import BORES, CHECKS, ENVELOPE, KERNEL_REVISION
from .policy import report_digest
from .program.hashing import canonical_json
from .program.kernel import (
    Artifact, ArtifactRef, BackendDescription, BuildResult, Built, ExecutionContext, ToleranceConvention, plan_for,
)
from .program.planning import generator_candidates
from .program.registry import ArtifactGraph, ArtifactNode, PLATE_OPERATION_VERSION
from .service import EngineeringUnavailable

STEP = 'generator-body.step'
REPORT = 'generator-report.json'
METHOD = 'editable-plate-occt-v1'
WORKER = Path(__file__).parent / 'program' / 'adapters' / '_editable_plate_worker.py'
MAX_STEP_BYTES = 512 * 1024


def describe():
    return BackendDescription(
        backend='occt', adapter_version=KERNEL_REVISION, mode='native',
        representations=('brep', 'report'), export_formats=('step',),
        tolerances=(
            ToleranceConvention(name='length', value=LINEAR_TOL, unit='mm', source=METHOD),
            ToleranceConvention(name='axis', value=AXIS_TOL, unit='relative', source=METHOD),
            ToleranceConvention(name='volume', value=VOLUME_TOL, unit='mm3', source=METHOD),
        ),
        capabilities=('editable_plate@1.0.0',),
        operations=tuple(f'capability.{op}@{PLATE_OPERATION_VERSION}' for op in ('build', 'inspect', 'export')))


async def worker(python, operation, spec, *, step=None, seconds=30):
    from process_runner import ProcessRunner
    payload = {'operation': operation, 'specification': spec}
    if step is not None:
        if not 0 < len(step) <= MAX_STEP_BYTES:
            raise EngineeringUnavailable('Editable plate STEP exceeds the byte budget')
        payload['step'] = b64encode(step).decode('ascii')
    env = {key: os.environ[key] for key in ('PATH', 'LANG', 'SYSTEMROOT', 'WINDIR', 'TEMP', 'TMP')
           if key in os.environ}
    env.update(OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', PYTHONDONTWRITEBYTECODE='1')
    runner = ProcessRunner(timeout=min(seconds, 30), output_limit=1024**2, memory_mb=1536)
    try:
        result = await runner.run([python, str(WORKER)], input=canonical_json(payload).encode(), env=env)
    finally:
        await runner.close()
    if result.get('returncode') != 0 or result.get('code'):
        raise EngineeringUnavailable('Editable plate worker unavailable')
    return json.loads(result['stdout'])


def bound_report(report, step, candidate):
    """Bind the measurements to the same candidate and exact bytes at build and on reread."""
    if step is not None:
        if (not 0 < len(step) <= MAX_STEP_BYTES
                or report['schema'] != 'editable_plate_measurement_v1'
                or report['step'] != {'bytes': len(step), 'sha256': sha256(step).hexdigest()}
                or report['specification'] != candidate.content.specification):
            raise ValueError('Editable plate measurement is unbound')
    if report['outcome'] != ('fail' if report['diagnostics'] else 'pass'):
        raise ValueError('Editable plate measurements contradict the outcome')
    return dict(report, kernel_revision=KERNEL_REVISION, candidate_sha256=candidate.candidate_sha256,
                reviewed_brief_sha256=candidate.brief_sha256)


def diagnostic_groups(report):
    result = {check: [] for check in CHECKS}
    for diagnostic in report['diagnostics']:
        code = diagnostic['code']
        key = 'plate_envelope' if code in ENVELOPE else 'plate_bores' if code in BORES else 'plate_binding'
        result[key].append(code)
    return result


class EditablePlateAdapter:
    descriptor = EDITABLE_PLATE

    def candidates(self, brief):
        return generator_candidates(brief.content)

    @staticmethod
    def measurements(data):
        return {check: {'diagnostics': len(codes)} for check, codes in diagnostic_groups(json.loads(data)).items()}

    async def evaluate(self, candidate):
        from .program.adapters.occt import OcctAdapter
        plan = plan_for(EDITABLE_PLATE.id, EDITABLE_PLATE.version, 'occt', candidate, ('step',))
        ctx = ExecutionContext(deadline_s=30.0, max_artifact_bytes=1024**2, workspace='editable-plate')
        built = await OcctAdapter(capability=EDITABLE_PLATE.id).build(plan, ctx)
        if built.evaluation is None:
            raise EngineeringUnavailable('Editable plate evaluation unavailable')
        return dict(built.evaluation)


def build_result(plan, output, context):
    candidate = plan.candidate
    step = b64decode(output['step'], validate=True) if 'step' in output else None
    report = bound_report(output['report'], step, candidate)
    report_bytes = canonical_json(report).encode()
    passed = report['outcome'] == 'pass'
    if (passed and step is None) or len(report_bytes) + len(step or b'') > context.max_artifact_bytes:
        raise ValueError('Invalid editable plate delivery')
    identities, artifacts, nodes = (), (), ()
    if passed:
        identity = ArtifactIdentity(path=STEP, sha256=sha256(step).hexdigest(), bytes=len(step))
        ref = ArtifactRef(name=STEP, format='step', representation='brep', sha256=identity.sha256,
                          size=identity.bytes, node_id=PART_ID)
        identities, artifacts = (identity,), (Artifact(ref, step),)
        nodes = (ArtifactNode(id=PART_ID, kind='solid', op_seq=0),)
    limits = EDITABLE_PLATE.expected_limits(candidate.content.specification)
    checks = tuple(
        GeneratorCheckResult(
            capability_id=EDITABLE_PLATE.id, check_id=check,
            state=CheckState.FAILED if codes else CheckState.PASSED,
            method=METHOD, method_version=KERNEL_REVISION, brief_sha256=candidate.brief_sha256,
            candidate_sha256=candidate.candidate_sha256,
            measurements=(Measurement(name='diagnostics', unit='count', value=len(codes)),),
            limits=limits[check], artifacts=identities, reason=', '.join(sorted(set(codes))) if codes else None)
        for check, codes in diagnostic_groups(report).items())
    captured, diagnostics = ({STEP: step, REPORT: report_bytes}, {}) if passed else ({}, {REPORT: report_bytes})
    evaluation = dict(candidate=candidate, checks=checks, passed=passed,
                      outcome=Outcome.ACCEPTED if passed else Outcome.REJECTED,
                      spec_sha256=candidate.candidate_sha256, report=report, report_sha256=report_digest(report),
                      report_bytes=report_bytes, artifacts=captured, diagnostics=diagnostics, extra_digests=(),
                      stated_digests={path: sha256(data).hexdigest()
                                      for path, data in (captured or diagnostics).items()})
    graph = ArtifactGraph(schema_version='mirum_artifact_graph_v0_1', op_stream=plan.op_stream, nodes=nodes)
    result = BuildResult(backend='occt', adapter_version=KERNEL_REVISION, mode='native',
                         capability_id=plan.capability_id, capability_version=plan.capability_version,
                         outcome='completed', domain_outcome=evaluation['outcome'].value,
                         claim_scope='geometry_checks_only', artifacts=tuple(a.ref for a in artifacts),
                         op_stream=plan.op_stream, artifact_graph=graph, report_sha256=evaluation['report_sha256'])
    return Built(result=result, artifacts=artifacts, evaluation=evaluation)
