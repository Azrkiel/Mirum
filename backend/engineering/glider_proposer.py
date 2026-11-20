"""E05 AI proposer for the ``glider_cem`` capability (Wave E, P3).

Wraps ``cem.extraction.extract`` (unchanged) and maps its reply onto a ``proposal_contract``
envelope. It never runs physics, never finalizes a brief and never returns or logs raw model
text, exception text or the prompt. ``calibration_version`` is explicit reviewed policy (the
shipped initial snapshot) and is never supplied by the model. Importing this module imports no
SDK and reads no key; ``cem.extraction`` is imported lazily, only inside ``propose`` and only
when no ``extract`` callable was injected.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import re
from uuid import uuid4

from pydantic import ValidationError

from cem.glider import calibration
from cem.glider.requirements import GliderMissionRequirements

from . import proposal_contract, router
from .briefs import InputIssue
from .capabilities import GENERATOR_SPEC_PREFIX, GLIDER
from .contracts import FieldOrigin, Provenance

PROMPT_VERSION = 'glider_cem_extract_v1'
UNAVAILABLE = ('AI preparation unavailable; typed form remains available',)
NOT_A_GLIDER = 'This request is not a glider; no glider draft was made'
FAMILIES_QUESTION = ('Which glider families should discovery search? '
                     'List one or more of: conventional, t_tail, v_tail.')
CALIBRATION_QUESTION = ('Only the shipped initial calibration is chosen automatically; '
                        'enter the calibration_version you want in the form.')
GENERIC_QUESTION = 'More information is needed to complete the glider draft'
CALIBRATION_REFERENCE = 'cem.glider.calibration initial snapshot (shipped default)'
DEFAULT_REFERENCE = 'GliderMissionRequirements default'
_CALIBRATION_MENTION = re.compile(r'calibrat|flight[- ]?test', re.IGNORECASE)
_FAMILIES_FIELD = 'specification.requirements.discovery.families'
_CALIBRATION_FIELD = 'specification.calibration_version'
_REQUIREMENTS_PREFIX = GENERATOR_SPEC_PREFIX + 'requirements.'
_MISSING = object()


@dataclass(frozen=True)
class GliderProposal:
    state: str                       # proposed | clarification_required | unsupported | unavailable
    payload: dict
    issues: tuple
    provenance: Provenance
    reasons: tuple


def _provenance(mode='form', origins=()) -> Provenance:
    return Provenance(record_id=uuid4().hex, created_at=datetime.now(timezone.utc),
                      input_mode=mode, field_origins=tuple(origins))


def _issue(state, reason, field='request') -> InputIssue:
    return InputIssue(field=field, state=state, reason=reason[:240])


def _check_prompt(prompt):
    if type(prompt) is not str or not prompt.strip() or len(prompt) > proposal_contract.MAX_PROMPT:
        raise ValueError('prompt must contain 1-2000 characters')


def _walk(tree, path):
    for part in path.split('.'):
        if not isinstance(tree, dict) or part not in tree:
            return _MISSING
        tree = tree[part]
    return tree


def _defaults(requirements):
    """Documented defaults at the extracted launch method, or ``None`` if they cannot be built."""
    try:
        method = requirements['launch']['method']
        return GliderMissionRequirements(launch=dict(method=method)).model_dump(mode='json')
    except Exception:
        return None


def _calibration_version():
    """The shipped snapshot ``/cem`` advertises as current, or ``None`` if it cannot be read."""
    try:
        value = calibration.version(calibration.initial())
        return value if isinstance(value, str) and value else None
    except Exception:
        return None


class AiProposer:
    """``base_spec`` is accepted for contract compatibility and ignored for now."""

    def __init__(self, budget, extract=None):
        self.budget = budget
        self.extract = extract

    def _payload(self) -> dict:
        return dict(capability=proposal_contract.reference(GLIDER))

    def _done(self, state, payload, issues=(), provenance=None, reasons=()):
        proposal = GliderProposal(state, payload, tuple(issues), provenance or _provenance(), tuple(reasons))
        return proposal_contract.conform(GLIDER.id, proposal)

    def _unavailable(self):
        return self._done('unavailable', self._payload(), reasons=UNAVAILABLE)

    def _unsupported(self, issues):
        issues = tuple(issues)
        return self._done('unsupported', self._payload(), issues, reasons=tuple(i.reason for i in issues))

    def _clarify(self, issues, payload=None, provenance=None):
        issues = tuple(issues)
        return self._done('clarification_required', payload or self._payload(), issues, provenance,
                          tuple(i.reason for i in issues))

    async def propose(self, prompt: str, base_spec=None) -> GliderProposal:
        _check_prompt(prompt)
        if router.route(prompt).capability_id not in (None, GLIDER.id):
            return self._unsupported((_issue('unsupported', NOT_A_GLIDER),))
        if not self.budget.reserve():
            return self._unavailable()
        try:
            extract = self.extract
            if extract is None:
                from cem import extraction
                extract = extraction.extract
            result = await extract(prompt)
        except Exception:
            return self._unavailable()
        finally:
            self.budget.release()
        return self._map(result, prompt)

    def _map(self, result, prompt) -> GliderProposal:
        if not isinstance(result, dict):
            return self._unavailable()
        status = result.get('status')
        if status == 'needs_clarification':
            questions = result.get('questions')
            questions = [q for q in questions if isinstance(q, str) and q.strip()][:8] \
                if isinstance(questions, list) else []
            return self._clarify([_issue('clarification_required', q) for q in questions]
                                 or [_issue('clarification_required', GENERIC_QUESTION)])
        requirements = result.get('requirements')
        if status != 'review_requirements' or not isinstance(requirements, dict):
            return self._unavailable()

        version = _calibration_version()
        calibration_asked = version is None or bool(_CALIBRATION_MENTION.search(prompt))
        spec = dict(requirements=requirements)
        if not calibration_asked:
            spec['calibration_version'] = version
        discovery = requirements.get('discovery')
        families = discovery.get('families', 'all') if isinstance(discovery, dict) else 'all'

        # Refusal first, except the families-'all' case, which is a question for the user.
        refused = [(f, r) for f, r in GLIDER.refuse(spec) if not (f == _FAMILIES_FIELD and families == 'all')]
        if refused:
            return self._unsupported(_issue('unsupported', reason, field) for field, reason in refused)

        issues = []
        if families == 'all':
            issues.append(_issue('clarification_required', FAMILIES_QUESTION, _FAMILIES_FIELD))
        if calibration_asked:
            issues.append(_issue('clarification_required', CALIBRATION_QUESTION, _CALIBRATION_FIELD))
        try:
            GLIDER.parse_spec(spec)
        except ValidationError as invalid:
            for error in invalid.errors():
                loc = tuple(error['loc'])
                if calibration_asked and loc == ('calibration_version',):
                    continue
                issues.append(_issue('clarification_required', str(error['msg']),
                                     'specification' + ''.join(f'.{p}' for p in loc)))
        except (ValueError, TypeError):
            return self._unavailable()

        provenance = self._provenance(spec, requirements, prompt)
        payload = dict(self._payload(), specification=spec)
        if issues:
            return self._clarify(issues, payload, provenance)
        return self._done('proposed', payload, (), provenance, ())

    def _provenance(self, spec, requirements, prompt) -> Provenance:
        defaults = _defaults(requirements)
        sha = hashlib.sha256(prompt.encode()).hexdigest()
        ai_reference = f'{sha}; {PROMPT_VERSION}; extraction; unverified proposal'[:240]
        origins = []
        for field in GLIDER.review_fields(spec):
            if field == _CALIBRATION_FIELD:
                origins.append(FieldOrigin(field=field, origin='policy', reference=CALIBRATION_REFERENCE))
                continue
            path = field[len(_REQUIREMENTS_PREFIX):]
            if (defaults is not None and path != 'launch.method'
                    and _walk(defaults, path) is not _MISSING
                    and _walk(defaults, path) == _walk(requirements, path)):
                origins.append(FieldOrigin(field=field, origin='policy', reference=DEFAULT_REFERENCE))
            else:
                origins.append(FieldOrigin(field=field, origin='ai_proposed', reference=ai_reference))
        return _provenance('ai', origins)
