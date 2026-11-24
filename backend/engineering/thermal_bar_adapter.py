"""Bind bounded thermal worker output to the reviewed candidate and delivered STEP."""

from base64 import b64decode, b64encode
from hashlib import sha256
import json
import os
from pathlib import Path

from .capabilities import THERMAL_BAR
from .contracts import ArtifactIdentity, CheckState, GeneratorCheckResult, Measurement, Outcome
from .policy import _comparison, report_digest
from .program.hashing import canonical_json
from .program.kernel import Artifact, ArtifactRef, BackendDescription, BuildResult, Built, ExecutionContext, plan_for
from .program.planning import generator_candidates
from .program.registry import ArtifactGraph, ArtifactNode, THERMAL_OPERATION_VERSION
from .service import EngineeringUnavailable
from .thermal_bar import KERNEL_REVISION


STEP = 'generator-body.step'
REPORT = 'generator-report.json'
METHOD = 'thermal_bar_fvm_v1'
PART = 'thermal.bar'
MAX_STEP_BYTES = 512 * 1024
WORKER = Path(__file__).parent / 'program' / 'adapters' / '_thermal_worker.py'


def describe():
    return BackendDescription(
        backend='occt', adapter_version=KERNEL_REVISION, mode='native', representations=('brep', 'report'),
        export_formats=('step',), tolerances=(), capabilities=('thermal_bar@1.0.0',),
        operations=tuple(f'capability.{op}@{THERMAL_OPERATION_VERSION}' for op in ('build', 'inspect', 'export')))


async def worker(python, operation, specification, *, step=None, seconds=30):
    from process_runner import ProcessRunner

    payload = dict(operation=operation, specification=specification)
    if step is not None:
        if not 0 < len(step) <= MAX_STEP_BYTES:
            raise EngineeringUnavailable('Thermal bar STEP exceeds the byte budget')
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
        raise EngineeringUnavailable('Thermal bar worker unavailable')
    return json.loads(result['stdout'])


def measured_values(report):
    values = {'bar_geometry': report['geometry']['geometry_measurements']}
    stages = report['analysis']['stages']
    conduction = stages.get('conduction', {}).get('result')
    expansion = stages.get('expansion', {}).get('result')
    clearance = stages.get('clearance', {}).get('result')
    values['conduction'] = dict(
        stage_complete=int(conduction is not None),
        rise_error=conduction['samples'][-1]['rise_error_relative'] if conduction else 0,
        refinement_change=conduction['finest_pair_relative_change'] if conduction else 0,
        heat_imbalance=max(s['heat_imbalance_relative'] for s in conduction['samples']) if conduction else 0)
    values['expansion'] = dict(
        stage_complete=int(expansion is not None),
        expansion_error=expansion['samples'][-1]['expansion_error_relative'] if expansion else 0,
        uniform_error=max(s['uniform_error_relative'] for s in expansion['samples']) if expansion else 0,
        refinement_change=expansion['finest_pair_relative_change'] if expansion else 0,
        transfer_error=max(s['max_transfer_error_k'] for s in expansion['samples']) if expansion else 0,
        coverage=min(s['coverage_fraction'] for s in expansion['samples']) if expansion else 0)
    values['clearance'] = dict(stage_complete=int(clearance is not None),
                               absolute_error=clearance['absolute_error_m'] * 1000 if clearance else 0,
                               remaining_gap=clearance['gap_m'] * 1000 if clearance else 0)
    return values


def bound_report(report, step, candidate):
    if (not 0 < len(step) <= MAX_STEP_BYTES or report['schema'] != 'mirum_thermal_bar_report_v1'
            or report['step_sha256'] != sha256(step).hexdigest()
            or report['specification'] != candidate.content.specification):
        raise ValueError('Thermal bar report is unbound')
    values = measured_values(report)
    limits = THERMAL_BAR.expected_limits(candidate.content.specification)
    passing = all(_comparison(values[check][limit.name], limit)
                  for check, rows in limits.items() for limit in rows)
    return dict(report, kernel_revision=KERNEL_REVISION, candidate_sha256=candidate.candidate_sha256,
                reviewed_brief_sha256=candidate.brief_sha256, outcome='pass' if passing else 'fail')


class ThermalBarAdapter:
    descriptor = THERMAL_BAR

    def candidates(self, brief):
        return generator_candidates(brief.content)

    @staticmethod
    def measurements(data):
        return measured_values(json.loads(data))

    async def evaluate(self, candidate):
        from .program.adapters.occt import OcctAdapter

        plan = plan_for(THERMAL_BAR.id, THERMAL_BAR.version, 'occt', candidate, ('step',))
        context = ExecutionContext(deadline_s=30.0, max_artifact_bytes=1024**2, workspace='thermal-bar')
        built = await OcctAdapter(capability=THERMAL_BAR.id).build(plan, context)
        if built.evaluation is None:
            raise EngineeringUnavailable('Thermal bar evaluation unavailable')
        return dict(built.evaluation)


def build_result(plan, output, context):
    candidate = plan.candidate
    step = b64decode(output['step'], validate=True)
    report = bound_report(output['report'], step, candidate)
    report_bytes = canonical_json(report).encode()
    if len(report_bytes) + len(step) > context.max_artifact_bytes:
        raise ValueError('Thermal bar bundle exceeds the byte budget')
    passing = report['outcome'] == 'pass'
    identities, artifacts, nodes = (), (), ()
    if passing:
        identities = (ArtifactIdentity(path=STEP, sha256=report['step_sha256'], bytes=len(step)),)
        ref = ArtifactRef(name=STEP, format='step', representation='brep', sha256=report['step_sha256'],
                          size=len(step), node_id=PART)
        artifacts = (Artifact(ref, step),)
        nodes = (ArtifactNode(id=PART, kind='solid', op_seq=0),)
    checks = []
    values = measured_values(report)
    for check, limits in THERMAL_BAR.expected_limits(candidate.content.specification).items():
        passed = all(_comparison(values[check][limit.name], limit) for limit in limits)
        checks.append(GeneratorCheckResult(
            capability_id=THERMAL_BAR.id, check_id=check, state=CheckState.PASSED if passed else CheckState.FAILED,
            method=METHOD, method_version=KERNEL_REVISION, brief_sha256=candidate.brief_sha256,
            candidate_sha256=candidate.candidate_sha256, limits=limits, artifacts=identities,
            measurements=tuple(Measurement(name=limit.name, unit=limit.unit, value=values[check][limit.name])
                               for limit in limits),
            reason=None if passed else f'{check} exceeds the reviewed numerical limits'))
    outcome = Outcome.ACCEPTED if passing else Outcome.REJECTED
    captured, diagnostics = ({STEP: step, REPORT: report_bytes}, {}) if passing else ({}, {REPORT: report_bytes})
    evaluation = dict(candidate=candidate, checks=tuple(checks), passed=passing, outcome=outcome,
                      spec_sha256=candidate.candidate_sha256, report=report, report_sha256=report_digest(report),
                      report_bytes=report_bytes, artifacts=captured, diagnostics=diagnostics, extra_digests=(),
                      stated_digests={path: sha256(data).hexdigest()
                                      for path, data in (captured or diagnostics).items()})
    graph = ArtifactGraph(schema_version='mirum_artifact_graph_v0_1', op_stream=plan.op_stream, nodes=nodes)
    result = BuildResult(backend='occt', adapter_version=KERNEL_REVISION, mode='native',
                         capability_id=plan.capability_id, capability_version=plan.capability_version,
                         outcome='completed', domain_outcome=outcome.value, claim_scope='thermal_benchmark',
                         artifacts=tuple(a.ref for a in artifacts), op_stream=plan.op_stream, artifact_graph=graph,
                         report_sha256=evaluation['report_sha256'])
    return Built(result=result, artifacts=artifacts, evaluation=evaluation)
