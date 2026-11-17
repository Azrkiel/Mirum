"""Glider CEM adapter for ``/engineering/v1``.

Runs the existing CEM stages unchanged (discovery, elite selection by the ``GliderCEM.size``
rule, design) through ``cem.api.jobs``, then splits the validated design report into the
capability's named checks. Values are copied verbatim from the report; limits come only from
``GliderDescriptor.expected_limits`` (the recorded ``cem.evidence.validate`` floors).

Nothing under ``backend/cem`` or ``backend/gliders`` is touched, so no CEM output id changes.
``cem.api`` (FastAPI) is imported inside ``evaluate`` only.

The evaluation's ``report`` is a composite, bound by digest:

- ``specification``: the reviewed specification;
- ``discovery``: digest and counts of the discovery ``result.json`` (summary only);
- ``binding`` / ``artifact``: tallies computed from the bytes by this adapter;
- ``design``: the design ``result.json``, verbatim, or ``None`` when discovery found no candidate.

Evidence the report lacks (a rejected design that stopped early, or no design) is measured as a
sentinel outside its floor, so that check is FAILED with a reason that says the evidence is absent.
"""
from __future__ import annotations

import asyncio
import hashlib
import importlib
import io
import json
import math
import shutil
import zipfile

from cem.core import canonical_json, digest
from cem.glider.requirements import GliderMissionRequirements
from pydantic import ValidationError

from .capabilities import CAPABILITY, GLIDER, UnsupportedCapability, _leaf
from .contracts import (ArtifactIdentity, CheckState, ComparisonLimit, GeneratorBriefContent, GeneratorCandidate,
                        GeneratorCheckResult, Measurement, Outcome)
from .service import EngineeringUnavailable

RUN_BYTES = CAPABILITY.resources.run_bytes
RESULT_BYTES = 8 * 1024 * 1024     # the report budget ``cem.api.Jobs.run`` applies
ZIP_EXPANDED_BYTES = 64 * 1024 * 1024
BUNDLE = 'generator-bundle.zip'
RESULT = 'generator-result.json'
METHOD, METHOD_VERSION = 'cem_evidence_floors', 'glider_cem_checks_v1'
SCHEMA = 'glider_cem_evaluation_v1'
SEGMENTED = 'segmented_foam_wing_v1'
EXTRA_UNITS = {
    'measured_spar_stress_pa': 'Pa', 'allowable_with_sf_pa': 'Pa',
    'duration_p10_s': 's', 'duration_p50_s': 's', 'duration_p90_s': 's',
    'distance_p10_m': 'm', 'distance_p50_m': 'm', 'distance_p90_m': 'm',
    'objective_p10_s': 's', 'objective_p10_m': 'm', 'minimum_performance_s': 's', 'minimum_performance_m': 'm',
}


class GliderInconsistent(EngineeringUnavailable):
    """The CEM report contradicts itself or the verdict it states; never accepted."""


def _sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _num(value):
    return float(value) if type(value) in (int, float) and math.isfinite(value) else None


def _dig(value, *path):
    for key in path:
        if not isinstance(value, dict) or key not in value:
            return None
        value = value[key]
    return value


def _flag(section):
    """0 when a section reports ``state == 'passed'``, 1 when it does not, None when absent."""
    return None if not isinstance(section, dict) else (0.0 if section.get('state') == 'passed' else 1.0)


def _strict_loads(data: bytes):
    def refuse(_):
        raise ValueError('nonfinite')
    return json.loads(data, parse_constant=refuse)


def _sentinel(limit: ComparisonLimit) -> float:
    """A finite value that fails the limit; used only where the report has no such evidence."""
    return limit.value - 1.0 if limit.comparison == 'ge' else limit.value + 1.0


def _raw(report: dict) -> dict:
    """``{check_id: {measurement name: value or None}}`` straight from the composite."""
    spec = report['specification']
    discovery, design = report['discovery'], report['design']
    raw = {'discovery_archive': {'archive_elites': _num(discovery['archive_elites'])},
           'candidate_selection': {'candidates': _num(discovery['candidates'])}}
    if design is None:
        return raw
    closure = _dig(design, 'closure')
    closure = closure if isinstance(closure, dict) else {}
    reanalysis = closure.get('reanalysis')
    reanalysis = reanalysis if isinstance(reanalysis, dict) else None
    pinned = {r.name: r.value for r in GLIDER.numerical}
    construction = _leaf(spec, 'requirements.construction')
    parts = _dig(design, 'bundle', 'parts')
    count = float(len(parts)) if isinstance(parts, list) else None
    ceiling = pinned['parts_max_segmented'] if construction == SEGMENTED else pinned['parts_max']
    failures = closure.get('failures')
    enclosure = closure.get('spar_enclosure')
    tolerance = _num(_dig(enclosure, 'tolerance_m'))
    outside = _num(_dig(enclosure, 'max_outside_m'))
    stress = _num(closure.get('measured_spar_stress_pa'))
    allowable = _num(_dig(reanalysis, 'spar', 'allowable_with_sf_pa'))
    uncertainty = design.get('uncertainty')
    bands = _dig(uncertainty, 'bands')
    violations, band_values = None, {}
    if isinstance(bands, dict):
        violations = 0.0
        for key, label, unit in (('duration_s', 'duration', 's'), ('distance_m', 'distance', 'm')):
            band = bands.get(key)
            p = [_num(_dig(band, k)) for k in ('p10', 'p50', 'p90')]
            if None in p or not 0 <= p[0] <= p[1] <= p[2]:
                violations += 1
                continue
            band_values.update({f'{label}_{k}_{unit}': v for k, v in zip(('p10', 'p50', 'p90'), p)})
    requirement = _leaf(spec, 'requirements.objective')
    unit = 'm' if requirement == 'distance_m' else 's'
    extra_performance = {}
    if f'{"distance" if unit == "m" else "duration"}_p10_{unit}' in band_values:
        extra_performance[f'objective_p10_{unit}'] = band_values[f'{"distance" if unit == "m" else "duration"}_p10_{unit}']
    hard = _num(_leaf(spec, 'requirements.minimum_performance'))
    if hard is not None:
        extra_performance[f'minimum_performance_{unit}'] = hard
    passed = design.get('minimum_performance_passed')
    artifact = report.get('artifact') or {}
    raw.update({
        'design_binding': {'binding_mismatches': _num(_dig(report, 'binding', 'mismatches'))},
        'closure': {'closure_failures': float(len(failures)) if isinstance(failures, list) else None},
        'part_accounting': {'bundle_parts': count, 'part_count_headroom': None if count is None else ceiling - count,
                            'part_accounting_failures': _flag(closure.get('part_accounting'))},
        'spar_enclosure': {'spar_enclosure_tolerance_m': tolerance,
                           'spar_enclosure_excess_m': None if None in (tolerance, outside) else outside - tolerance,
                           'spar_enclosure_failures': _flag(enclosure)},
        'mass_closure': {'mass_relative_error': _num(closure.get('mass_relative_error'))},
        'measured_mass_aero': {
            'measured_mass_kg': _num(closure.get('measured_mass_kg')),
            'measured_mass_aero_invalid': None if reanalysis is None else (
                0.0 if reanalysis.get('valid') is True and not reanalysis.get('fail_reasons') else 1.0)},
        'validity_envelope': {
            'min_station_reynolds': _num(_dig(reanalysis, 'validity_envelope', 'min_station_reynolds')),
            'min_confidence': _num(_dig(reanalysis, 'validity_envelope', 'min_confidence'))},
        'spar_structure': {
            'spar_stress_to_allowable': None if stress is None or allowable is None or allowable <= 0 else stress / allowable,
            'measured_spar_root_inertia_m4': _num(closure.get('measured_spar_root_inertia_m4')),
            'spar_structure_failures': _flag(closure.get('spar_structure')),
            'measured_spar_stress_pa': stress, 'allowable_with_sf_pa': allowable},
        'uncertainty_bands': {
            'uncertainty_samples': _num(_dig(uncertainty, 'samples')),
            'sustained_samples': _num(_dig(uncertainty, 'sustained_samples')),
            'band_violations': violations, **band_values},
        'minimum_performance': {'minimum_performance_failures': None if not isinstance(passed, bool) else float(not passed),
                                **extra_performance},
        'artifact_binding': {'artifact_hash_mismatches': _num(artifact.get('hash_mismatches')),
                             'bundle_bytes': _num(artifact.get('bundle_bytes'))},
        'foam_core': {'foam_core_failures': _flag(closure.get('foam_core'))},
        'segmented_structure': {'segmented_structure_failures': _flag(closure.get('segmented_structure'))},
    })
    return raw


def _split(report: dict) -> dict:
    """``{check_id: (ordered [(name, unit, value)], limits, missing names)}`` for the required checks."""
    spec = report['specification']
    table = GLIDER.expected_limits(spec)
    raw = _raw(report)
    split = {}
    for check_id in GLIDER.required_checks(spec):
        values = raw.get(check_id, {})
        measurements, missing = [], []
        for limit in table[check_id]:
            value = values.get(limit.name)
            if value is None:
                missing.append(limit.name)
                value = _sentinel(limit)
            measurements.append((limit.name, limit.unit, value))
        known = {n for n, _, _ in measurements}
        measurements += [(n, EXTRA_UNITS[n], v) for n, v in values.items() if n not in known and v is not None]
        split[check_id] = (measurements, table[check_id], missing)
    return split


def _verdict_reason(limits, measured, missing) -> str:
    from .policy import _comparison
    if missing:
        return ('Evidence absent from the CEM report: ' + ', '.join(missing))[:240]
    out = [l.name for l in limits if not _comparison(measured[l.name], l)]
    return ('Outside the pinned floor: ' + ', '.join(out))[:240]


def assemble_evaluation(candidate: GeneratorCandidate, *, discovery: bytes, design: bytes | None,
                        bundle: bytes | None) -> dict:
    """Pure: the evaluation dict ``assess_generator`` documents, from captured bytes."""
    from .policy import _comparison, report_digest
    spec = candidate.content.specification
    found = _strict_loads(discovery)
    candidates = found['candidates']
    archive = sum(len(p['archive']) for p in found['per_process'].values())
    selected = max(candidates, key=lambda e: e['result']['objective_value']) if candidates else None
    report_design = None if design is None else _strict_loads(design)
    if (report_design is None) != (selected is None) or (design is None) != (bundle is None):
        raise GliderInconsistent('Design evidence does not match the discovery selection')
    failed_binding = []
    for label, ok in (
            ('discovery requirements', found.get('requirements_sha256') == candidate.candidate_sha256),
            ('discovery calibration', found.get('calibration_version') == spec['calibration_version'])):
        if not ok:
            failed_binding.append(label)
    artifact = None
    if report_design is not None:
        for label, ok in (
                ('design requirements', report_design.get('requirements_sha256') == candidate.candidate_sha256),
                ('design calibration', report_design.get('calibration_version') == spec['calibration_version']),
                ('selected elite', report_design.get('elite_id') == selected['elite_id']),
                ('output id', report_design.get('output_id') == found.get('output_id')),
                ('cem version', report_design.get('cem_version') == found.get('cem_version')),
                ('design revision', isinstance(report_design.get('design_revision_sha256'), str))):
            if not ok:
                failed_binding.append(label)
        mismatches = int(_sha(bundle) != report_design.get('bundle_sha256'))
        parts = _dig(report_design, 'bundle', 'parts')
        parts = parts if isinstance(parts, list) else []
        try:
            with zipfile.ZipFile(io.BytesIO(bundle)) as z:
                names = set(z.namelist())
                if sum(i.file_size for i in z.infolist()) > ZIP_EXPANDED_BYTES:
                    raise ValueError('Archive budget exceeded')
                extra = tuple((z.read(p['file']), p['sha256']) for p in parts if p['file'] in names)
                mismatches += sum(1 for p in parts if p['file'] not in names)
        except (zipfile.BadZipFile, ValueError, KeyError, TypeError):
            extra, mismatches = (), mismatches + 1
        artifact = dict(hash_mismatches=mismatches, bundle_bytes=len(bundle))
    report = dict(schema=SCHEMA, specification=spec,
                  discovery=dict(result_sha256=_sha(discovery), output_id=found.get('output_id'),
                                 archive_elites=archive, candidates=len(candidates),
                                 selected_elite_id=None if selected is None else selected['elite_id']),
                  binding=dict(mismatches=len(failed_binding), failed=failed_binding),
                  artifact=artifact, design=report_design)
    split = _split(report)
    identity = () if bundle is None else (ArtifactIdentity(path=BUNDLE, sha256=_sha(bundle), bytes=len(bundle)),)
    checks = []
    for check_id in candidate.content.required_checks:
        measurements, limits, missing = split[check_id]
        measured = {n: v for n, _, v in measurements}
        passed = not missing and all(_comparison(measured[l.name], l) for l in limits)
        checks.append(GeneratorCheckResult(
            capability_id=GLIDER.id, check_id=check_id,
            state=CheckState.PASSED if passed else CheckState.FAILED,
            method=METHOD, method_version=METHOD_VERSION, brief_sha256=candidate.brief_sha256,
            candidate_sha256=candidate.candidate_sha256,
            measurements=tuple(Measurement(name=n, unit=u, value=v) for n, u, v in measurements),
            limits=tuple(limits), artifacts=identity,
            reason=None if passed else _verdict_reason(limits, measured, missing)))
    failed = [c for c in checks if c.state == CheckState.FAILED]
    if report_design is not None and (report_design.get('status') == 'screened') != (not failed):
        raise GliderInconsistent('The CEM verdict contradicts its own evidence floors')
    if report_design is not None and report_design.get('status') not in ('screened', 'rejected'):
        raise GliderInconsistent('Unknown CEM design status')
    result_bytes = design if design is not None else discovery
    accepted = not failed
    report_bytes = canonical_json(report).encode()
    stated = {RESULT: _sha(result_bytes)}
    if accepted:
        stated[BUNDLE] = report_design['bundle_sha256']
    return dict(
        candidate=candidate, checks=tuple(checks),
        outcome='accepted' if accepted else 'rejected', passed=accepted,
        spec_sha256=(report_design or found)['requirements_sha256'],
        report=report, report_sha256=report_digest(report), report_bytes=report_bytes,
        artifacts={BUNDLE: bundle, RESULT: result_bytes} if accepted else {},
        diagnostics={} if accepted else {RESULT: result_bytes},
        stated_digests=stated, extra_digests=extra if bundle is not None else ())


async def _cancel_owned(task) -> None:
    """Cancel the CEM task and await it, shielded, as ``cem.api.Jobs.close`` does."""
    if not task.done():
        task.cancel()
    cleanup = asyncio.gather(task, return_exceptions=True)
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue


async def _await_task(task) -> None:
    try:
        await asyncio.shield(task)
    except asyncio.CancelledError:
        if not task.done():
            await _cancel_owned(task)
            raise
        if task.cancelled():
            raise EngineeringUnavailable('CEM task cancelled; no outputs accepted') from None
        raise
    except Exception:
        pass    # ``Jobs.run`` records its own failure; the job status decides below


async def _stage(jobs, payload: dict, validate, owned: list):
    """Start one CEM stage and return ``(result bytes, parsed report, job)`` for a completed, validated job."""
    try:
        started = jobs.start(payload)      # a 429/slot refusal raises here
    except Exception:
        raise EngineeringUnavailable('A CEM task is running or unavailable; retry after it completes') from None
    key = started['job_id']
    owned.append(key)
    await _await_task(jobs.task)
    try:
        job = jobs.completed(key, payload['kind'])
        path = job['directory'] / 'result.json'
        if path.is_symlink() or not path.is_file() or path.stat().st_size > RESULT_BYTES:
            raise ValueError('Report budget exceeded')
        data = path.read_bytes()
        report = _strict_loads(data)
        validate(report, payload, job['identity'], job['directory'])
    except Exception:
        raise EngineeringUnavailable('CEM evidence unavailable; no outputs accepted') from None
    return data, report, job


class GliderAdapter:
    descriptor = GLIDER

    def candidates(self, brief) -> tuple[GeneratorCandidate, ...]:
        content = getattr(brief, 'content', None)
        if not isinstance(content, GeneratorBriefContent) or content.capability.id != GLIDER.id:
            raise UnsupportedCapability('Brief does not belong to the glider capability')
        spec = content.specification
        refused = GLIDER.refuse(spec)
        if refused:
            raise UnsupportedCapability(refused[0][1])
        try:
            requirements = GLIDER.parse_spec(spec).requirements
        except ValidationError:
            raise UnsupportedCapability('Unsupported glider specification') from None
        return (GeneratorCandidate.from_brief(brief, digest(requirements.model_dump(mode='json'))),)

    def measurements(self, report_bytes: bytes) -> dict:
        split = _split(_strict_loads(report_bytes))
        return {check_id: {n: v for n, _, v in measurements}
                for check_id, (measurements, _, _) in split.items()}

    async def evaluate(self, candidate) -> dict:
        candidate = GeneratorCandidate.model_validate(candidate.model_dump())
        spec = candidate.content.specification
        refused = GLIDER.refuse(spec)
        if refused:
            raise UnsupportedCapability(refused[0][1])
        try:
            api = importlib.import_module('cem.api')   # honours sys.modules, unlike ``from cem import api``
            from cem.evidence import validate
            from cem.glider import calibration
            loaded = calibration.load(spec['calibration_version'])
            requirements = GliderMissionRequirements.model_validate(spec['requirements']).model_dump(mode='json')
        except Exception:
            raise EngineeringUnavailable('CEM runtime or calibration unavailable') from None
        jobs, owned = api.jobs, []
        try:
            found_bytes, found, _ = await _stage(
                jobs, dict(kind='discovery', requirements=requirements, calibration=loaded), validate, owned)
            design_bytes = bundle = None
            if found['candidates']:
                # The GliderCEM.size rule: highest objective value, first on ties.
                elite = max(found['candidates'], key=lambda e: e['result']['objective_value'])
                _, _, job = await _stage(
                    jobs, dict(kind='design', requirements=found['requirements'],
                               calibration=found['calibration'], elite=elite), validate, owned)
                design_bytes = (job['directory'] / 'result.json').read_bytes()
                zip_path = job['directory'] / 'bundle.zip'
                if zip_path.is_symlink() or not zip_path.is_file() or zip_path.stat().st_size > RUN_BYTES:
                    raise EngineeringUnavailable('CEM bundle exceeds the run budget')
                bundle = zip_path.read_bytes()
            if len(found_bytes) + len(design_bytes or b'') + len(bundle or b'') > RUN_BYTES:
                raise EngineeringUnavailable('CEM run exceeds the run budget')
            try:
                return assemble_evaluation(candidate, discovery=found_bytes, design=design_bytes, bundle=bundle)
            except EngineeringUnavailable:
                raise
            except Exception:
                raise EngineeringUnavailable('CEM report could not be split into checks') from None
        finally:
            for key in owned:
                job = jobs.jobs.pop(key, None)
                if job is not None:
                    shutil.rmtree(job['directory'], ignore_errors=True)
