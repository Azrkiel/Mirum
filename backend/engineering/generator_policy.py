"""Fail-closed evidence policy for registry generators (dragster, regen, glider).

An adapter's ``evaluate()`` returns a dict with these keys:

- ``candidate``: the ``GeneratorCandidate`` evaluated.
- ``checks``: one ``GeneratorCheckResult`` per required check.
- ``outcome`` / ``passed``: the adapter's verdict and the generator's own pass flag.
- ``spec_sha256``: the specification digest the generator's report states.
- ``report`` / ``report_sha256``: the generator's report and its ``policy.report_digest``.
- ``report_bytes``: the generator's own report as captured. Its canonical JSON (sorted
  keys, exact types: ``true`` is not ``1``, ``1`` is not ``1.0``) must equal that of ``report``.
- ``artifacts``: captured ``generator-*`` bytes (accepted only; empty when rejected).
- ``diagnostics``: captured diagnostic bytes (rejected only).
- ``stated_digests``: the SHA-256 the generator states for every captured byte string.
- ``extra_digests``: ``(bytes, stated_sha256)`` pairs for bytes derived from a payload
  (for example each part read from inside a glider zip).

Trust model. No state is taken from the adapter:

- Closed limit table: each check's limits must equal ``descriptor.expected_limits(spec)``
  exactly (names, comparison, unit, value, tolerance; nothing extra, nothing missing). A
  check with no pinned table entry is unavailable. Each adapter PR completes its table.
- Pass or fail is recomputed from the measurements with the beam's comparison and must
  equal the check state.
- Measurement binding: the adapter (resolved through the registry; not installed means
  unavailable) parses ``report_bytes`` with ``measurements(report_bytes)``, and the typed
  checks' measurements must equal that result.

Anything missing, extra, duplicated or inconsistent is unavailable, never accepted.
Digests give consistency, not authenticity.
"""
from __future__ import annotations

import hashlib
import json

from cem.core import canonical_json
from .contracts import (ArtifactIdentity, CheckState, GeneratorCandidate, GeneratorCheckResult, Outcome)


class _Invalid(ValueError):
    """Carries a fixed safe diagnostic, never a generator exception or input dump."""


def _require(condition, reason):
    if not condition:
        raise _Invalid(reason)


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _validate(candidate: GeneratorCandidate, evaluation: dict):
    from .policy import PolicyDecision, report_digest
    candidate = GeneratorCandidate.model_validate(candidate.model_dump())
    descriptor = candidate.content.descriptor
    _require(GeneratorCandidate.model_validate(evaluation['candidate'].model_dump()) == candidate,
             'Candidate association mismatch')
    raw_checks = evaluation['checks']
    _require(type(raw_checks) is tuple and all(type(c) is GeneratorCheckResult for c in raw_checks),
             'Checks are not typed generator evidence')
    checks = tuple(GeneratorCheckResult.model_validate(c.model_dump()) for c in raw_checks)
    required = candidate.content.required_checks
    # Missing, extra or duplicate ids all differ from the reviewed set exactly.
    _require(sorted(c.check_id for c in checks) == sorted(required), 'Check set differs from the reviewed required set')
    _require(all(c.capability_id == descriptor.id and c.brief_sha256 == candidate.brief_sha256
                 and c.candidate_sha256 == candidate.candidate_sha256 for c in checks),
             'Check evidence belongs to another brief or candidate')
    _require(all(c.state in (CheckState.PASSED, CheckState.FAILED) for c in checks), 'Unexpected check state')
    from . import capabilities
    from .policy import _comparison
    table = descriptor.expected_limits(candidate.content.specification)
    for check in checks:
        # Closed table: the limits are exactly the pinned ones, nothing extra, nothing missing.
        _require(check.check_id in table and check.limits == table[check.check_id],
                 'Check limits differ from the pinned limit table')
        measured = {m.name: m.value for m in check.measurements}
        passes = all(_comparison(measured[l.name], l) for l in check.limits)
        _require(passes == (check.state == CheckState.PASSED), 'Check state contradicts its measurements')
    # Measurements are bound to the generator's own report, parsed by its adapter.
    report_bytes = evaluation['report_bytes']
    _require(type(report_bytes) is bytes and canonical_json(json.loads(report_bytes)) == canonical_json(evaluation['report']),
             'Report bytes differ from the captured report')
    adapter = capabilities.get_adapter(descriptor.id, descriptor.version,
                                       numerical_policy_version=descriptor.numerical_version,
                                       resource_policy_version=descriptor.resources.version)
    parsed = adapter.measurements(report_bytes)
    _require(parsed == {c.check_id: {m.name: m.value for m in c.measurements} for c in checks},
             'Check measurements differ from the generator report')
    failed = [c for c in checks if c.state == CheckState.FAILED]
    expected = Outcome.REJECTED if failed else Outcome.ACCEPTED
    _require(Outcome(evaluation['outcome']) == expected and evaluation['passed'] is (not failed),
             'Outcome or pass flag contradicts the checks')
    _require(evaluation['spec_sha256'] == candidate.candidate_sha256, 'Report specification digest mismatch')
    report = evaluation['report']
    _require(evaluation['report_sha256'] == report_digest(report), 'Report changed after capture')
    artifacts, diagnostics = evaluation['artifacts'], evaluation['diagnostics']
    captured = {**artifacts, **diagnostics}
    _require(len(captured) == len(artifacts) + len(diagnostics)
             and all(type(n) is str and type(v) is bytes for n, v in captured.items()), 'Captured bytes malformed')
    stated = evaluation['stated_digests']
    _require(set(stated) == set(captured), 'Every captured byte string needs a stated digest')
    _require(all(_sha(v) == stated[n] for n, v in captured.items()), 'Delivered bytes changed')
    _require(all(type(b) is bytes and _sha(b) == d for b, d in evaluation['extra_digests']), 'Derived bytes changed')
    if failed:
        _require(not artifacts, 'Rejected candidate cannot expose accepted geometry')
        _require(set(diagnostics) <= set(descriptor.diagnostic_payloads), 'Unexpected diagnostic payload')
    else:
        _require(not diagnostics and set(artifacts) == set(descriptor.payloads), 'Captured artifacts incomplete')
        for check in checks:
            _require(any(i.path == descriptor.primary_artifact for i in check.artifacts),
                     'Passed check lacks the measured primary artifact identity')
            for identity in check.artifacts:
                _require(identity == ArtifactIdentity(path=identity.path, sha256=_sha(artifacts[identity.path]),
                                                      bytes=len(artifacts[identity.path])),
                         'Check artifact identity differs from delivered bytes')
    return PolicyDecision(available=True, outcome=expected, checks=checks,
                          reasons=tuple(c.reason for c in failed))


def assess_generator(candidate: GeneratorCandidate, evaluation: dict):
    """An invalid or incomplete evidence association is unavailable, never acceptance."""
    from .policy import PolicyDecision
    try:
        return _validate(candidate, evaluation)
    except _Invalid as error:
        return PolicyDecision(available=False, outcome=Outcome.NOT_EVALUATED, checks=(), reasons=(str(error),))
    except Exception:
        return PolicyDecision(available=False, outcome=Outcome.NOT_EVALUATED, checks=(),
                              reasons=('Required evidence missing, inconsistent or outside the pinned policy',))
