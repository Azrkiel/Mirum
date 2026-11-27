"""N1c-3a: the delivered verifier accepts native plate locks and always measures with OCCT.

Host test (no binary, no OCCT): a stub verifier serves the delivered STEP and report from memory and a fake
OCCT plate worker measures it. Real-binary variants live in test_program_adapter_native_plate.py.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering.editable_plate import KERNEL_REVISION
from engineering.editable_plate_adapter import REPORT, STEP, bound_report
from engineering.language.semantic import compile_source
from engineering.program import kernel
from engineering.program.adapters.native import ADAPTER_VERSION, NativePlateAdapter
from engineering.program.adapters.native_pin import PIN
from engineering.program.delivered import DeliveredVerifier
from engineering.program.hashing import canonical_json, execution_lock_v0_2_sha256, semantic_sha256
from engineering.program.ir import execution_lock_v0_2, execution_lock_v0_3
from engineering.program.kernel import NativeVersion, ProbeResult
from engineering.program.planning import generator_candidates

REPO = Path(__file__).resolve().parents[2]
PLATE = (REPO / 'backend' / 'engineering' / 'fixtures' / 'editable_plate' / 'edge.mir').read_bytes()
NATIVE = 'mirum-native'
PIN_VERSIONS = (NativeVersion(name='mirum-native', version=PIN.version),
                NativeVersion(name='mirum-native-commit', version=PIN.commit),
                NativeVersion(name='mirum-native-sha256', version=PIN.sha256))


def native_source():
    return PLATE.replace(b'backend = "occt"', b'backend = "mirum-native"')


class World:
    """A published plate revision served from memory, for either backend."""

    def __init__(self, backend, step, *, adapter_version=None, versions=None, measure=None):
        source = native_source() if backend == NATIVE else PLATE
        self.compiled = compile_source(source)
        self.ir = self.compiled.ir
        self.candidate = generator_candidates(self.compiled.brief_content)[0]
        self.step = step
        if backend == NATIVE:
            description = NativePlateAdapter().describe()
            probe = ProbeResult(backend=NATIVE, mode='native', available=True,
                                native_versions=PIN_VERSIONS if versions is None else versions)
            lock = kernel.backend_lock(description, probe)
            if adapter_version is not None:
                lock = lock.model_copy(update={'adapter_version': adapter_version})
            self.lock = execution_lock_v0_3(self.ir, lock)
        else:
            from engineering.editable_plate_adapter import describe
            probe = ProbeResult(backend='occt', mode='native', available=True,
                                native_versions=(NativeVersion(name='cadquery', version='2.7.0'),))
            lock = kernel.backend_lock(describe(), probe)
            if adapter_version is not None:
                lock = lock.model_copy(update={'adapter_version': adapter_version})
            self.lock = execution_lock_v0_2(self.ir, lock)
        content = self.compiled.brief_content
        self.revision = SimpleNamespace(
            revision_id='a' * 32, job_id='b' * 32, brief_sha256=content.brief_sha256,
            fixed_requirements_sha256=content.fixed_requirements_sha256, semantic_sha256=semantic_sha256(self.ir),
            lock=self.lock, lock_sha256=execution_lock_v0_2_sha256(self.lock), bundle=None)
        self.factory_calls, self.measured = [], []
        spec = self.candidate.content.specification
        w, h, t = spec['width_mm'], spec['height_mm'], spec['thickness_mm']
        report = {'schema': 'editable_plate_measurement_v1', 'outcome': 'pass', 'diagnostics': [],
                  'step': {'bytes': len(step), 'sha256': hashlib.sha256(step).hexdigest()},
                  'specification': spec, 'envelope_mm': [w, h, t],
                  'volume_mm3': w * h * t - 4 * math.pi * 9 * t, 'bores': []}
        self.report = measure(report) if measure else report
        self.blobs = {STEP: step, REPORT: canonical_json(bound_report(self.report, step, self.candidate)).encode()}
        self.stored = []

    def verifier(self):
        world = self

        class Adapter:
            mode = 'native'

            async def measure_plate(self, step, plan):
                world.measured.append((hashlib.sha256(step).hexdigest(), plan.backend))
                return {'report': world.report}

        def factory(backend, **options):
            world.factory_calls.append((backend, options))
            return Adapter()

        verifier = DeliveredVerifier.__new__(DeliveredVerifier)
        verifier.adapter_factory, verifier.timeout_s = factory, 5
        verifier._semaphore = asyncio.Semaphore(1)
        verifier.publisher = SimpleNamespace(validate=lambda *a: None)
        verifier.evidence = SimpleNamespace(put=lambda record: world.stored.append(record) or record)
        run = SimpleNamespace(selected_candidate_sha256=self.candidate.candidate_sha256,
                              attempts=[SimpleNamespace(candidate=self.candidate)],
                              bundle=SimpleNamespace(manifest_sha256='c' * 64))
        verifier._read_run = lambda revision: run
        verifier._lease_all = lambda job_id, bundle, payloads=None: world.blobs
        return verifier

    def verify(self):
        return asyncio.run(self.verifier().verify(self.revision, self.ir))


def test_native_lock_verifies_with_the_same_checks_as_occt_and_measures_with_occt_only():
    native = World(NATIVE, b'NATIVE-STEP')
    occt = World('occt', b'OCCT-STEP')
    n, o = native.verify(), occt.verify()
    assert n.verdict == o.verdict == 'verified', (n.reasons, n.verdict)
    assert n.claim_scope == o.claim_scope == 'geometry_checks_only'
    assert [c.model_dump() for c in n.checks] == [c.model_dump() for c in o.checks]
    assert n.step_sha256 == hashlib.sha256(b'NATIVE-STEP').hexdigest() != o.step_sha256
    assert n.method_version == o.method_version
    assert [v.name for v in n.native_versions] == ['mirum-native', 'mirum-native-commit', 'mirum-native-sha256']
    # The native kernel never verifies: only the OCCT plate worker adapter was asked for, with the OCCT plan.
    assert native.factory_calls == [('occt', {'capability': 'editable_plate'})]
    assert native.measured == [(hashlib.sha256(b'NATIVE-STEP').hexdigest(), 'occt')]


def test_wrong_native_adapter_version_is_lock_mismatch():
    for version in ('mirum-native-plate-0.0.9', KERNEL_REVISION, 'x'):
        record = World(NATIVE, b'S', adapter_version=version).verify()
        assert record.verdict == 'unavailable' and 'lock_mismatch' in record.reasons, version


def test_an_occt_lock_with_the_native_adapter_version_is_lock_mismatch():
    record = World('occt', b'S', adapter_version=ADAPTER_VERSION).verify()
    assert record.verdict == 'unavailable' and 'lock_mismatch' in record.reasons


def _replace(versions, name, value):
    return tuple(x.model_copy(update={'version': value}) if x.name == name else x for x in versions)


@pytest.mark.parametrize('mutate', [
    lambda v: _replace(v, 'mirum-native-sha256', '0' * 64),
    lambda v: _replace(v, 'mirum-native-commit', '1' * 40),
    lambda v: _replace(v, 'mirum-native', '9.9.9'),
    lambda v: tuple(x for x in v if x.name != 'mirum-native-sha256'),
    lambda v: v + (NativeVersion(name='extra', version='1'),),
    lambda v: (),
])
def test_native_versions_must_record_the_pinned_binary(mutate):
    record = World(NATIVE, b'S', versions=mutate(PIN_VERSIONS)).verify()
    assert record.verdict == 'unavailable' and 'lock_mismatch' in record.reasons


def test_native_lock_for_another_capability_stays_out_of_scope():
    world = World(NATIVE, b'S')
    ir = SimpleNamespace(capability=SimpleNamespace(id='equipment_bay'))
    record = asyncio.run(world.verifier().verify(world.revision, ir))
    assert record.verdict == 'unavailable' and 'out_of_scope' in record.reasons
    assert world.factory_calls == []


def test_a_native_lock_cannot_hide_behind_an_occt_ir():
    world = World(NATIVE, b'S')
    occt_ir = World('occt', b'S').ir
    record = asyncio.run(world.verifier().verify(world.revision, occt_ir))
    assert record.verdict != 'verified'


def test_occt_rejection_of_the_native_step_is_not_verified():
    def broken(report):
        return dict(report, outcome='fail', diagnostics=[{'code': 'feature_missing', 'message': 'x'}])
    record = World(NATIVE, b'S', measure=broken).verify()
    assert record.verdict == 'failed' and record.reasons == ()


def test_method_version_covers_the_native_pin():
    from engineering.program import delivered
    assert 'native_pin.py' in [p.name for p in delivered.METHOD_SOURCES]
