"""E05 AI proposer for the ``glider_cem`` capability (Wave E, P3; sparse extraction, fix loop D5).

Asks the beam's provider (``GeminiProposalProvider``, the engineering key; no legacy client) for a
sparse set of explicitly stated glider requirements (``glider_extraction``), drops every value the
prompt does not state (``grounding``), fills the rest from the ``GliderMissionRequirements``
defaults labelled ``policy`` and returns a ``proposal_contract`` envelope. It never runs physics,
never finalizes a brief and never returns or logs raw provider text, exception text or the prompt.
``calibration_version`` is explicit reviewed policy (the shipped initial snapshot) and is never
supplied by the model. A calibration-update ask the model lists as an unsupported feature is dropped
and answered by the calibration clarification instead (fail closed). Importing this module imports no SDK and reads no key.
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

from . import glider_extraction, grounding, proposal_contract, router
from .briefs import InputIssue
from .capabilities import GENERATOR_SPEC_PREFIX, GLIDER
from .contracts import FieldOrigin, Provenance
from .proposer import GeminiProposalProvider

PROMPT_VERSION = glider_extraction.PROMPT_VERSION
UNAVAILABLE = ('AI preparation unavailable; typed form remains available',)
NOT_A_GLIDER = 'This request is not a glider; no glider draft was made'
FAMILIES_QUESTION = ('Which glider families should discovery search? '
                     'List one or more of: conventional, t_tail, v_tail.')
CALIBRATION_QUESTION = ('Only the shipped initial calibration is chosen automatically; '
                        'enter the calibration_version you want in the form.')
UNGROUNDED_QUESTION = 'The request does not state this value; enter it in the form or restate it in the request.'
MISSING_METHOD_QUESTION = ('The request does not state a launch method; '
                           'choose one in the form or restate it in the request.')
PAYLOAD_QUESTION = 'A payload needs mass, diameter and length all stated; enter the payload in the form.'
CALIBRATION_REFERENCE = 'cem.glider.calibration initial snapshot (shipped default)'
DEFAULT_REFERENCE = 'GliderMissionRequirements default'
_CALIBRATION_MENTION = re.compile(r'calibrat|flight[- ]?test', re.IGNORECASE)
_OTHER_UNSUPPORTED = re.compile(
    r'thermal|ridge|slope|soar|power|motor|engine|propell|electric|battery|radio|rc|servo|remote'
    r'|autopilot|gps|camera|script|code', re.IGNORECASE)
_FAMILIES_FIELD = 'specification.requirements.discovery.families'
_CALIBRATION_FIELD = 'specification.calibration_version'
_REQUIREMENTS_PREFIX = GENERATOR_SPEC_PREFIX + 'requirements.'
_PAYLOAD_KEYS = ('payload_mass_kg', 'payload_diameter_m', 'payload_length_m')


@dataclass(frozen=True)
class GliderProposal:
    state: str                       # proposed | clarification_required | unsupported | unavailable
    payload: dict
    issues: tuple
    provenance: Provenance
    reasons: tuple
    reason_id: str = ''


def _provenance(mode='form', origins=()) -> Provenance:
    return Provenance(record_id=uuid4().hex, created_at=datetime.now(timezone.utc),
                      input_mode=mode, field_origins=tuple(origins))


def _issue(state, reason, field='request') -> InputIssue:
    return InputIssue(field=field, state=state, reason=reason[:240])


def _check_prompt(prompt):
    if type(prompt) is not str or not prompt.strip() or len(prompt) > proposal_contract.MAX_PROMPT:
        raise ValueError('prompt must contain 1-2000 characters')


def _calibration_version():
    """The shipped snapshot ``/cem`` advertises as current, or ``None`` if it cannot be read."""
    try:
        value = calibration.version(calibration.initial())
        return value if isinstance(value, str) and value else None
    except Exception:
        return None


def _set(tree, path, value):
    parts = path.split('.')
    for part in parts[:-1]:
        tree = tree[part]
    tree[parts[-1]] = value


class AiProposer:
    """``base_spec`` is accepted for contract compatibility and ignored for now."""

    def __init__(self, budget, provider=None):
        self.budget = budget
        self.provider = provider or GeminiProposalProvider()

    def _payload(self) -> dict:
        return dict(capability=proposal_contract.reference(GLIDER))

    def _done(self, state, payload, issues=(), provenance=None, reasons=(), reason_id=''):
        proposal = GliderProposal(state, payload, tuple(issues), provenance or _provenance(), tuple(reasons),
                                  reason_id)
        return proposal_contract.conform(GLIDER.id, proposal)

    def _unavailable(self, reason_id):
        return self._done('unavailable', self._payload(), reasons=UNAVAILABLE, reason_id=reason_id)

    def _unsupported(self, issues, reason_id):
        issues = tuple(issues)
        return self._done('unsupported', self._payload(), issues, reasons=tuple(i.reason for i in issues),
                          reason_id=reason_id)

    async def propose(self, prompt: str, base_spec=None) -> GliderProposal:
        _check_prompt(prompt)
        if router.route(prompt).capability_id not in (None, GLIDER.id):
            return self._unsupported((_issue('unsupported', NOT_A_GLIDER),), 'unsupported.family')
        parsed, failure, settings = await glider_extraction.extract_sparse(
            self.provider, self.budget, prompt, GLIDER.resources.structured_response_bytes)
        if parsed is None:
            return self._unavailable(failure)
        return self._map(parsed, prompt, settings)

    def _map(self, reply, prompt, settings) -> GliderProposal:
        if reply.out_of_family:
            return self._unsupported((_issue('unsupported', NOT_A_GLIDER),), 'unsupported.family')
        features = list(reply.unsupported_features)
        if features and _CALIBRATION_MENTION.search(prompt):
            # A calibration ask is a clarification (calibration_version is never taken from the model).
            features = [f for f in features
                        if not (_CALIBRATION_MENTION.search(f) and not _OTHER_UNSUPPORTED.search(f))]
        dropped = len(features) < len(reply.unsupported_features)
        if features:
            return self._unsupported((_issue('unsupported', f, 'features') for f in features),
                                     'unsupported.feature')

        provided = {k: v for k, v in reply.fields.model_dump().items() if v is not None}
        if reply.questions and not provided:
            # The model only asked questions: no requirements to review, so no specification.
            issues = tuple(_issue('clarification_required', q) for q in reply.questions)
            return self._done('clarification_required', self._payload(), issues, None,
                              tuple(i.reason for i in issues), 'clarification.model_question')
        objective = provided.get('objective')
        if objective is not None and not grounding.is_grounded_choice(
                objective, prompt, glider_extraction.SYNONYMS['objective']):
            objective = None
        ungrounded = grounding.ungrounded_fields(provided, prompt,
                                                 glider_extraction.field_kinds(objective or 'distance_m'))
        grounded = {k: v for k, v in provided.items() if k not in ungrounded}
        payload_all = all(k in grounded for k in _PAYLOAD_KEYS)
        partial_payload = any(k in grounded for k in _PAYLOAD_KEYS) and not payload_all
        if partial_payload:
            for key in _PAYLOAD_KEYS:
                grounded.pop(key, None)

        requirements = GliderMissionRequirements(launch=dict(method='drop')).model_dump(mode='json')
        del requirements['launch']['method']
        if payload_all:
            requirements['constraints']['payload'] = {}
        for key, value in grounded.items():
            _set(requirements, glider_extraction.LEAVES[key], value)
        ai_paths = {glider_extraction.LEAVES[k] for k in grounded}
        if payload_all:
            ai_paths.add('constraints.payload')
        method_missing = 'launch_method' not in grounded

        version = _calibration_version()
        calibration_asked = version is None or bool(_CALIBRATION_MENTION.search(prompt))
        if dropped and not calibration_asked:
            return self._unsupported((_issue('unsupported', f, 'features') for f in reply.unsupported_features),
                                     'unsupported.feature')
        spec = dict(requirements=requirements)
        if not calibration_asked:
            spec['calibration_version'] = version
        families = requirements['discovery']['families']

        # Refusal first, except the families-'all' case, which is a question for the user.
        refused = [(f, r) for f, r in GLIDER.refuse(spec) if not (f == _FAMILIES_FIELD and families == 'all')]
        if refused:
            return self._unsupported((_issue('unsupported', reason, field) for field, reason in refused),
                                     'unsupported.value')

        issues, reason_ids = [], []

        def add(new, reason_id):
            if new:
                issues.extend(new)
                reason_ids.append(reason_id)

        field_prefix = 'specification.requirements.'
        add([_issue('clarification_required', q) for q in reply.questions], 'clarification.model_question')
        missing = []
        if method_missing:
            missing.append(_issue('clarification_required', MISSING_METHOD_QUESTION, field_prefix + 'launch.method'))
        if partial_payload:
            missing.append(_issue('clarification_required', PAYLOAD_QUESTION, field_prefix + 'constraints.payload'))
        add(missing, 'clarification.missing_field')
        add([_issue('clarification_required', UNGROUNDED_QUESTION,
                    field_prefix + glider_extraction.LEAVES[key]) for key in ungrounded],
            'clarification.ungrounded_value')
        if families == 'all':
            add([_issue('clarification_required', FAMILIES_QUESTION, _FAMILIES_FIELD)], 'clarification.families')
        if calibration_asked:
            add([_issue('clarification_required', CALIBRATION_QUESTION, _CALIBRATION_FIELD)],
                'clarification.calibration')
        invalid = []
        try:
            GLIDER.parse_spec(spec)
        except ValidationError as error:
            for item in error.errors():
                loc = tuple(item['loc'])
                if calibration_asked and loc == ('calibration_version',):
                    continue
                if method_missing and loc == ('requirements', 'launch', 'method'):
                    continue
                invalid.append(_issue('clarification_required', str(item['msg']),
                                      'specification' + ''.join(f'.{p}' for p in loc)))
        except (ValueError, TypeError):
            return self._unavailable('unavailable.nonconforming')
        add(invalid, 'clarification.invalid_value')

        provenance = self._provenance(spec, ai_paths, prompt, settings)
        payload = dict(self._payload(), specification=spec)
        if issues:
            return self._done('clarification_required', payload, issues, provenance,
                              tuple(i.reason for i in issues), reason_ids[0])
        return self._done('proposed', payload, (), provenance, ())

    def _provenance(self, spec, ai_paths, prompt, settings) -> Provenance:
        """Label by origin: only a grounded value the model stated is ``ai_proposed``; everything the
        proposer fills (``calibration_version`` or a ``GliderMissionRequirements`` default) is policy."""
        sha = hashlib.sha256(prompt.encode()).hexdigest()
        ai_reference = f'{sha}; {PROMPT_VERSION}; {settings.model}; unverified proposal'[:240]
        origins = []
        for field in GLIDER.review_fields(spec):
            if field == _CALIBRATION_FIELD:
                origins.append(FieldOrigin(field=field, origin='policy', reference=CALIBRATION_REFERENCE))
            elif field[len(_REQUIREMENTS_PREFIX):] in ai_paths:
                origins.append(FieldOrigin(field=field, origin='ai_proposed', reference=ai_reference))
            else:
                origins.append(FieldOrigin(field=field, origin='policy', reference=DEFAULT_REFERENCE))
        return _provenance('ai', origins)
