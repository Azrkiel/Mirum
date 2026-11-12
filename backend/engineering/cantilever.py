"""V1 adapter over the existing builder and independent native beam verifier.

No solver/oracle lives here. Native report quantities are translated verbatim.
Only accepted captured bytes escape; rejected STEP identities remain diagnostic.
"""
from __future__ import annotations

import hashlib
import json

from .capabilities import CAPABILITY
from .contracts import (ArtifactIdentity, CandidateSpec, CheckResult, CheckState,
                        ComparisonLimit, EngineeringBrief, Measurement, Outcome)
from .service import EngineeringUnavailable


def report_checks(candidate: CandidateSpec, report: dict) -> tuple[CheckResult, ...]:
    """Validate and translate a bounded worker report, including rejected prefixes."""
    try:
        if (report['spec_sha256'] != candidate.candidate_sha256
                or report['scope'] != 'rectangular_cantilever_v1'
                or report['engineering_certified'] is not False
                or report['standards']['state'] != 'not_evaluated'
                or type(report['passed']) is not bool
                or 'required_evidence' in report['checks']):
            raise ValueError('Worker scope/identity/evidence mismatch')
        step = ArtifactIdentity(path='model.step', sha256=report['step_sha256'], bytes=report['step_bytes'])
        implementation = report['implementation_sha256']
        if set(implementation) != {'verify.py', 'beam_fea.py', 'spec.py', 'solid_set.py'}:
            raise ValueError('Missing verifier identity')
        if any(len(v) != 64 or any(c not in '0123456789abcdef' for c in v) for v in implementation.values()):
            raise ValueError('Malformed verifier identity')
        if not all(report['versions'].get(k) for k in ('cadquery', 'ocp', 'numpy', 'python')):
            raise ValueError('Missing runtime versions')
        method_version = hashlib.sha256(json.dumps(implementation, sort_keys=True).encode()).hexdigest()
        checks = []
        raw = report['checks']
        if set(raw) - set(CAPABILITY.required_checks):
            raise ValueError('Unknown worker check')
        for name in CAPABILITY.required_checks:
            entry = raw.get(name)
            state = CheckState(entry['state']) if entry else CheckState.NOT_EVALUATED
            checks.append(CheckResult(
                check_id=name, state=state, method='independent_step_beam_verifier_v1',
                method_version=method_version, brief_sha256=candidate.brief_sha256,
                candidate_sha256=candidate.candidate_sha256,
                measurements=tuple(Measurement.model_validate(m) for m in entry['measurements']) if entry else (),
                limits=tuple(ComparisonLimit.model_validate(m) for m in entry['limits']) if entry else (),
                artifacts=(step,), reason=entry.get('reason') if entry else 'Stopped after earlier measured rejection'))
        if report['passed']:
            if (any(c.state != CheckState.PASSED for c in checks) or report['failures']
                    or report['physics']['state'] != 'passed' or report['design_intent']['state'] != 'passed'):
                raise ValueError('False worker pass')
        elif not report['failures'] or not any(c.state == CheckState.FAILED for c in checks):
            raise ValueError('Rejection needs measured failure')
        return tuple(checks)
    except (KeyError, TypeError, ValueError, AttributeError):
        raise EngineeringUnavailable('Malformed, unavailable or mismatched native evidence') from None


class CantileverAdapter:
    descriptor = CAPABILITY

    def candidates(self, brief: EngineeringBrief) -> tuple[CandidateSpec, ...]:
        brief = EngineeringBrief.model_validate(brief.model_dump())
        return tuple(CandidateSpec.from_brief(brief, h) for h in brief.content.candidate_heights_mm)

    async def evaluate(self, candidate: CandidateSpec) -> dict:
        from .service import generate_verified
        from verification.acceptance import AcceptanceError
        candidate = CandidateSpec.model_validate(candidate.model_dump())
        try:
            result = await generate_verified(candidate.specification)
        except AcceptanceError:
            raise EngineeringUnavailable('Required native capture/acceptance unavailable') from None
        report = result['engineering_report']
        checks = report_checks(candidate, report)
        artifacts = {}
        if result['status'] == 'accepted':
            artifacts = {'model.py': result['script'].encode(), 'model.step': result['step'],
                         'preview.glb': result['glb']}
            hashes = report['artifacts']
            for path, key in (('model.py', 'source_sha256'), ('model.step', 'step_sha256'), ('preview.glb', 'glb_sha256')):
                if hashlib.sha256(artifacts[path]).hexdigest() != hashes[key]:
                    raise EngineeringUnavailable('Captured bytes differ from verified artifacts')
            if hashlib.sha256(artifacts['model.step']).hexdigest() != report['step_sha256']:
                raise EngineeringUnavailable('STEP changed after verification')
        return dict(candidate=candidate, outcome=Outcome(result['status']), checks=checks,
                    report=report, step_identity=checks[0].artifacts[0], artifacts=artifacts)
