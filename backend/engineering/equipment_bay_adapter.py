"""Equipment-bay KernelAdapter seam and typed generator evidence."""

from base64 import b64decode, b64encode
from hashlib import sha256
import json
from pathlib import Path

from .capabilities import EQUIPMENT_BAY
from .contracts import ArtifactIdentity, CheckState, GeneratorCheckResult, Measurement, Outcome
from .equipment_bay import KERNEL_REVISION
from .policy import _comparison, report_digest
from .program.hashing import canonical_json
from .program.kernel import (
    Artifact, ArtifactRef, BackendDescription, BuildResult, Built, ExecutionContext,
    ToleranceConvention, plan_for,
)
from .program.planning import generator_candidates
from .program.registry import ArtifactGraph, ArtifactNode
from .service import EngineeringUnavailable

STEP = 'generator-body.step'
BOM = 'generator-bom.json'
REPORT = 'generator-report.json'
METHOD = 'equipment-bay-occt-v1'
WORKER = Path(__file__).parent / 'program' / 'adapters' / '_bay_worker.py'
MAX_STEP_BYTES = 512 * 1024
DESCRIPTORS = tuple(f'capability.{operation}@0.3.0' for operation in ('build', 'inspect', 'export'))


def describe():
    return BackendDescription(
        backend='occt', adapter_version=KERNEL_REVISION, mode='native',
        representations=('brep', 'report'), export_formats=('step',),
        tolerances=(ToleranceConvention(name='alignment', value=0.05, unit='mm',
                                        source='equipment-bay digital mounting criterion'),),
        capabilities=('equipment_bay@1.0.0',), operations=DESCRIPTORS)


async def worker(python, operation, spec, *, step=None, seconds=30):
    from process_runner import ProcessRunner
    payload = {'operation': operation, 'specification': spec}
    if step is not None:
        if len(step) > MAX_STEP_BYTES:
            raise EngineeringUnavailable('Equipment-bay STEP exceeds its byte budget')
        payload['step'] = b64encode(step).decode('ascii')
    runner = ProcessRunner(timeout=min(seconds, 30), output_limit=1024**2, memory_mb=1536)
    try:
        result = await runner.run([python, str(WORKER)], input=canonical_json(payload).encode())
    finally:
        await runner.close()
    if result.get('returncode') != 0 or result.get('code'):
        raise EngineeringUnavailable('Equipment-bay native worker unavailable')
    return json.loads(result['stdout'])


class EquipmentBayAdapter:
    descriptor = EQUIPMENT_BAY

    def candidates(self, brief):
        return generator_candidates(brief.content)

    @staticmethod
    def measurements(data):
        report = json.loads(data)
        length, width = report['envelope']['measured_mm']
        rows = report['mounting']['correspondences']
        measured = [row['offset_mm'] for row in rows if row['offset_mm'] is not None]
        return {
            'bay_envelope': {'length': length, 'width': width},
            'mounting_alignment': {'max_offset': max(measured, default=0.0), 'correspondences': len(measured)},
            'occurrence_binding': {'diagnostics': len(report['diagnostics'])},
        }

    async def evaluate(self, candidate):
        from .program.adapters.occt import OcctAdapter
        plan = plan_for('equipment_bay', '1.0.0', 'occt', candidate, ('step',))
        context = ExecutionContext(deadline_s=30.0, max_artifact_bytes=1024**2, workspace='equipment-bay')
        built = await OcctAdapter(capability=EQUIPMENT_BAY.id).build(plan, context)
        if built.result.outcome != 'completed' or built.evaluation is None:
            raise EngineeringUnavailable('Equipment-bay evaluation unavailable')
        return dict(built.evaluation)


def _identity(path, data):
    return ArtifactIdentity(path=path, sha256=sha256(data).hexdigest(), bytes=len(data))


def build_result(plan, output, context):
    candidate = plan.candidate
    step = b64decode(output['step'], validate=True)
    if len(step) > MAX_STEP_BYTES:
        raise ValueError('Equipment-bay STEP exceeds its byte budget')
    report = output['report']
    if report['step'] != {'bytes': len(step), 'sha256': sha256(step).hexdigest()}:
        raise ValueError('Equipment-bay report is unbound')
    report.update(search=output['search'], kernel_revision=KERNEL_REVISION)
    report_bytes = canonical_json(report).encode()
    bom = canonical_json(output['bom']).encode()
    captured = {STEP: step, BOM: bom, REPORT: report_bytes}
    if sum(map(len, captured.values())) > context.max_artifact_bytes:
        raise ValueError('Equipment-bay artifacts exceed their byte budget')
    limits = EQUIPMENT_BAY.expected_limits(candidate.content.specification)
    measured = EquipmentBayAdapter.measurements(report_bytes)
    passed = report['outcome'] == 'pass'
    checks = []
    for check in candidate.content.required_checks:
        rows = tuple(Measurement(name=limit.name, unit=limit.unit, value=measured[check][limit.name])
                     for limit in limits[check])
        accepted = all(_comparison(row.value, limit) for row, limit in zip(rows, limits[check]))
        checks.append(GeneratorCheckResult(
            capability_id=EQUIPMENT_BAY.id, check_id=check,
            state=CheckState.PASSED if accepted else CheckState.FAILED,
            method=METHOD, method_version=KERNEL_REVISION, brief_sha256=candidate.brief_sha256,
            candidate_sha256=candidate.candidate_sha256, measurements=rows, limits=limits[check],
            artifacts=(_identity(STEP, step),) if passed else (),
            reason=None if accepted else f'Equipment-bay {check} criterion failed'))

    artifacts, diagnostics = (captured, {}) if passed else ({}, {REPORT: report_bytes})
    evaluation = dict(candidate=candidate, checks=tuple(checks), passed=passed,
                      outcome=Outcome.ACCEPTED if passed else Outcome.REJECTED,
                      spec_sha256=candidate.candidate_sha256, report=report,
                      report_sha256=report_digest(report), report_bytes=report_bytes, artifacts=artifacts,
                      diagnostics=diagnostics, extra_digests=(),
                      stated_digests={path: sha256(data).hexdigest() for path, data in (artifacts or diagnostics).items()})
    ref = ArtifactRef(name=STEP, format='step', representation='brep', sha256=sha256(step).hexdigest(),
                      size=len(step), node_id='bay.assembly')
    graph = ArtifactGraph(schema_version='mirum_artifact_graph_v0_1', op_stream=plan.op_stream,
                          nodes=(ArtifactNode(id='bay.assembly', kind='solid', op_seq=0),))
    result = BuildResult(backend='occt', adapter_version=KERNEL_REVISION, mode='native',
                         capability_id=plan.capability_id, capability_version=plan.capability_version,
                         outcome='completed', domain_outcome=evaluation['outcome'].value,
                         claim_scope='geometry_checks_only', artifacts=(ref,) if passed else (),
                         op_stream=plan.op_stream, artifact_graph=graph, report_sha256=evaluation['report_sha256'])
    return Built(result=result, artifacts=(Artifact(ref, step),) if passed else (), evaluation=evaluation)
