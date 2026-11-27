"""N1c-3a: native plate conformance, mirroring every OCCT P04/P05 plate row against NativePlateAdapter.

Layering.
- Fake-binary rows (no kernel, no OCCT) always run: unsupported requests, missing binary, SHA mismatch,
  deadline, cancel, mock, the negative table and the P05 declarations.
- Real rows need the pinned binary and the OCCT plate worker. Set MIRUM_NATIVE_BIN to the binary (it must
  match native_pin) and, when this interpreter has no CadQuery, MIRUM_NATIVE_OCCT_PYTHON to one that has it.
  The older MIRUM_N1C2_REAL_BIN / MIRUM_N1C2_OCCT_PYTHON names are still read. Without the binary they skip.
- MIRUM_NATIVE_GATE_IMAGE=1 together with MIRUM_NATIVE_REQUIRE_BINARY=1 marks an image that must carry the kernel
  (default path /opt/mirum-native/bin/mirum-native): there the `real` fixture (only) FAILS instead of skipping
  when the binary or CadQuery is missing. CI already sets MIRUM_NATIVE_GATE_IMAGE=1, but the gate image cannot
  carry the binary until N1c-3b (the GHCR package is private), so N1c-3b adds MIRUM_NATIVE_REQUIRE_BINARY=1 to
  the gate-image runs. The other N1c-3 test files use fakes and never skip for this reason.
"""
from __future__ import annotations

import asyncio
import hashlib
import math
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from engineering.language.semantic import compile_source
from engineering.program.adapters import adapter_for
from engineering.program.adapters.native import NativePlateAdapter
from engineering.program.adapters.native_pin import BINARY_PATH, PIN
from engineering.program.kernel import ExecutionContext, NATIVE_CLAIMS, check_plan, plan_for
from engineering.program.planning import generator_candidates
from tests.test_program_n1c2_adapter import (  # noqa: F401  (fixtures and fake-binary helpers)
    adapter_of, candidate, compiled, context, fake_occt, kernel_enabled, make_fake, native_plan, wide_candidate,
)

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='ProcessRunner needs posix process groups')

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / 'backend' / 'engineering' / 'fixtures'
EDGE = (FIX / 'editable_plate' / 'edge.mir').read_bytes()
DATUM = (FIX / 'editable_plate' / 'datum.mir').read_bytes()
NATIVE = 'mirum-native'
GATE = os.environ.get('MIRUM_NATIVE_GATE_IMAGE') == '1' and os.environ.get('MIRUM_NATIVE_REQUIRE_BINARY') == '1'
BIN = os.environ.get('MIRUM_NATIVE_BIN') or os.environ.get('MIRUM_N1C2_REAL_BIN') or (BINARY_PATH if GATE else None)
OCCT_PYTHON = (os.environ.get('MIRUM_NATIVE_OCCT_PYTHON') or os.environ.get('MIRUM_N1C2_OCCT_PYTHON')
               or sys.executable)


def run(coro):
    return asyncio.run(coro)


def native_of(source, width=80):
    return source.replace(b'backend = "occt"', b'backend = "mirum-native"').replace(b'80 mm', f'{width} mm'.encode())


def plan_of(source, width, backend=NATIVE, exports=('step',)):
    text = native_of(source, width) if backend == NATIVE else source.replace(b'80 mm', f'{width} mm'.encode())
    candidate_ = generator_candidates(compile_source(text).brief_content)[0]
    return plan_for('editable_plate', '1.0.0', backend, candidate_, exports)


def analytic_volume(width, height=50.0, thickness=6.0):
    return width * height * thickness - 4 * math.pi * 9 * thickness


# ============================================================ fake-binary rows (always run)

def test_native_claims_pin_and_every_completed_result_is_geometry_checks_only(tmp_path, candidate, fake_occt):
    assert dict(NATIVE_CLAIMS) == {'editable_plate@1.0.0': 'geometry_checks_only'}
    binary, pin = make_fake(tmp_path / 'b')
    built = run(adapter_of(binary, pin).build(native_plan(candidate), context()))
    assert built.result.outcome == 'completed' and built.result.claim_scope == NATIVE_CLAIMS['editable_plate@1.0.0']


def test_p05_description_declares_representations_exports_and_tolerances(tmp_path):
    binary, pin = make_fake(tmp_path / 'b')
    d = adapter_of(binary, pin).describe()
    assert (d.backend, d.mode, d.representations, d.export_formats) == (NATIVE, 'native', ('brep', 'report'), ('step',))
    assert d.capabilities == ('editable_plate@1.0.0',)
    by = {t.name: t for t in d.tolerances}
    from vendor.mirum_editable_plate import AXIS_TOL, LINEAR_TOL, VOLUME_TOL
    assert (by['native_length'].value, by['native_length'].unit) == (1e-6, 'mm')
    assert (by['length'].value, by['axis'].value, by['volume'].value) == (LINEAR_TOL, AXIS_TOL, VOLUME_TOL)
    assert {by[n].source for n in ('length', 'axis', 'volume')} == {'editable-plate-occt-v1'}


@pytest.mark.parametrize('fmt', ['glb', 'stl'])
def test_glb_and_stl_are_unsupported_and_never_converted(tmp_path, candidate, fake_occt, fmt):
    binary, pin = make_fake(tmp_path / 'b')
    adapter = adapter_of(binary, pin)
    plan = plan_of(EDGE, 80, exports=(fmt,))
    built = run(adapter.build(plan, context()))
    assert built.result.outcome == 'unsupported' and 'export_not_supported' in built.result.reasons
    assert built.artifacts == () and not (binary.parent / 'record-plate.json').exists()
    (step,) = run(adapter.build(native_plan(candidate), context())).artifacts
    fake_artifact = SimpleNamespace(ref=step.ref.model_copy(update={'format': fmt}), data=step.data)
    assert run(adapter.inspect(fake_artifact, native_plan(candidate))).outcome == 'unsupported'
    assert adapter.export(step, fmt).outcome == 'unsupported'


def test_missing_binary_is_runtime_missing_and_sha_mismatch_is_probe_failed(tmp_path, candidate, fake_occt):
    binary, pin = make_fake(tmp_path / 'b')
    missing = adapter_of(tmp_path / 'b' / 'nope', pin)
    assert run(missing.probe()).reasons == ('runtime_missing',)
    built = run(missing.build(native_plan(candidate), context()))
    assert (built.result.outcome, built.result.reasons) == ('unavailable', ('runtime_missing',))
    from engineering.program.adapters.native_pin import NativePin
    bad = NativePin(sha256='0' * 64, commit=pin.commit, version=pin.version)
    assert run(adapter_of(binary, bad).probe()).reasons == ('probe_failed',)
    built = run(adapter_of(binary, bad).build(native_plan(candidate), context()))
    assert (built.result.outcome, built.result.reasons) == ('unavailable', ('probe_failed',))


def test_deadline_is_deadline_exceeded(tmp_path, wide_candidate, fake_occt):
    binary, pin = make_fake(tmp_path / 'b', sleep=5)
    built = run(adapter_of(binary, pin).build(native_plan(wide_candidate), context(deadline_s=2.0)))
    assert (built.result.outcome, built.result.reasons) == ('unavailable', ('deadline_exceeded',))


def test_cancellation_stops_the_build(tmp_path, wide_candidate, fake_occt):
    binary, pin = make_fake(tmp_path / 'b', sleep=20)
    adapter = adapter_of(binary, pin)

    async def go():
        task = asyncio.create_task(adapter.build(native_plan(wide_candidate), context(deadline_s=30)))
        await asyncio.sleep(1.5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    run(go())


def test_mock_adapter_cannot_build_or_complete():
    adapter = adapter_for(NATIVE, mode='mock')
    assert adapter.mode == 'mock' and run(adapter.probe()).reasons == ('mock_backend',)


@pytest.mark.parametrize('capability,directory', [
    ('equipment_bay', 'equipment_bay'), ('thermal_bar', 'thermal'), ('rectangular_cantilever', 'structural')])
def test_native_refuses_bay_thermal_and_beam(tmp_path, capability, directory):
    from engineering.program.planning import _program_candidates
    compiled_ = compile_source(sorted((FIX / directory).glob('*.mir'))[0].read_bytes())
    candidate_ = (_program_candidates(compiled_.brief_content)[0] if capability == 'rectangular_cantilever'
                  else generator_candidates(compiled_.brief_content)[0])
    plan = plan_for(capability, '1.0.0', NATIVE, candidate_, ('step',))
    binary, pin = make_fake(tmp_path / 'b')
    decision = adapter_of(binary, pin).supports(plan)
    assert not decision.supported and decision.reasons == ('capability_not_supported_by_backend',), decision
    built = run(adapter_of(binary, pin).build(plan, context()))
    assert built.result.outcome == 'unsupported'
    assert not (binary.parent / 'record-version.json').exists()  # refused before the binary was ever started


def test_native_plan_to_occt_and_occt_plan_to_native_are_backend_mismatch(tmp_path):
    from engineering.editable_plate_adapter import describe
    native_plan_ = plan_of(EDGE, 80)
    occt_plan = plan_of(EDGE, 80, backend='occt')
    binary, pin = make_fake(tmp_path / 'b')
    assert adapter_of(binary, pin).supports(occt_plan).reasons == ('backend_mismatch',)
    assert check_plan(describe(), native_plan_).reasons == ('backend_mismatch',)
    from engineering.program.adapters.occt import OcctAdapter
    assert OcctAdapter(capability='editable_plate').supports(native_plan_).reasons == ('backend_mismatch',)


# ============================================================ real-binary rows

@pytest.fixture(scope='module')
def real():
    """The pinned binary, OCCT worker and a set of builds; skips, or fails in a gate image, when absent."""
    def unavailable(why):
        if GATE:
            pytest.fail(f'MIRUM_NATIVE_GATE_IMAGE=1 but {why}')
        pytest.skip(why)

    if not BIN or not os.path.isfile(BIN):
        unavailable('the pinned mirum-native binary is missing (set MIRUM_NATIVE_BIN)')
    if hashlib.sha256(Path(BIN).read_bytes()).hexdigest() != PIN.sha256:
        pytest.fail('MIRUM_NATIVE_BIN does not match the pinned SHA-256')
    probe = subprocess.run([OCCT_PYTHON, '-c', 'import cadquery'], capture_output=True)
    if probe.returncode:
        unavailable('CadQuery is not importable (set MIRUM_NATIVE_OCCT_PYTHON)')
    patch = pytest.MonkeyPatch()
    patch.setenv('MIRUM_NATIVE_KERNEL', '1')
    patch.delenv('MIRUM_NATIVE_BIN', raising=False)
    from engineering.program.adapters.occt import OcctAdapter
    native = NativePlateAdapter(binary=BIN, python=OCCT_PYTHON)
    occt = OcctAdapter(capability='editable_plate', python=OCCT_PYTHON)
    ctx = ExecutionContext(deadline_s=120, max_artifact_bytes=1024 ** 2, workspace='n1c3-conformance')
    out = SimpleNamespace(native=native, occt=occt, ctx=ctx, native_builds={}, occt_builds={})
    assert run(native.probe()).available
    for name, source in (('edge', EDGE), ('datum', DATUM)):
        for width in (80, 100):
            plan = plan_of(source, width)
            out.native_builds[name, width] = (plan, run(native.build(plan, ctx)))
    for width in (80, 100):
        plan = plan_of(EDGE, width, backend='occt')
        out.occt_builds[width] = (plan, run(occt.build(plan, ctx)))
    yield out
    patch.undo()


def counts(step_bytes, tmp_path):
    path = tmp_path / 'count.step'
    path.write_bytes(step_bytes)
    script = ('import sys, cadquery as cq\n'
              's = cq.importers.importStep(sys.argv[1]).val()\n'
              'print(len(s.Vertices()), len(s.Edges()), len(s.Faces()))\n')
    out = subprocess.run([OCCT_PYTHON, '-c', script, str(path)], capture_output=True, text=True, check=True)
    return tuple(int(v) for v in out.stdout.split())


CASES = [('edge', 80), ('edge', 100), ('datum', 80), ('datum', 100)]


@pytest.mark.parametrize('name,width', CASES)
def test_real_nominal_plate_and_width_edits_are_accepted(real, name, width):
    plan, built = real.native_builds[name, width]
    result = built.result
    assert result.outcome == 'completed' and result.domain_outcome == 'accepted', result
    assert result.backend == NATIVE and result.mode == 'native'
    assert result.claim_scope == NATIVE_CLAIMS['editable_plate@1.0.0'] == 'geometry_checks_only'
    assert built.evaluation['report']['outcome'] == 'pass' and len(built.artifacts) == 1


@pytest.mark.parametrize('name,width', CASES)
def test_real_volume_and_bores_match_the_analytic_plate(real, name, width):
    plan, built = real.native_builds[name, width]
    volume = next(m for m in built.result.measurements if m.name == 'plate.volume')
    assert abs(volume.value - analytic_volume(width)) <= 1e-9 * analytic_volume(width)
    report = built.evaluation['report']
    assert abs(report['volume_mm3'] - analytic_volume(width)) <= 1e-9 * analytic_volume(width)
    x = 30 if (name == 'datum' or width == 80) else 40  # edge-driven: 10 mm side margin; datum-driven: x fixed
    by_id = {row['id']: row for row in report['bores']}
    assert len(report['bores']) == len(by_id) == 4
    expected = {'plate.bores.left.lower': (-x, -15), 'plate.bores.left.upper': (-x, 15),
                'plate.bores.right.lower': (x, -15), 'plate.bores.right.upper': (x, 15)}
    assert set(by_id) == set(expected)
    for bore_id, center in expected.items():
        row = by_id[bore_id]
        assert row['center_mm'] == pytest.approx(center, abs=1e-6)
        assert row['diameter_mm'] == pytest.approx(6, abs=1e-6)
        assert row['axis'] == pytest.approx([0, 0, 1], abs=1e-8)
        assert row['through'] is True


def test_real_topology_counts_are_v16_e24_f10_and_equal_to_occt(real, tmp_path):
    for width in (80, 100):
        native_step = real.native_builds['edge', width][1].artifacts[0].data
        occt_step = real.occt_builds[width][1].artifacts[0].data
        assert counts(native_step, tmp_path) == counts(occt_step, tmp_path) == (16, 24, 10), width


def test_real_bore_ids_are_stable_across_the_edits(real):
    def sides(report):
        """id -> (left/right sign, lower/upper sign) of the bore carrying it."""
        return {row['id']: (math.copysign(1, row['center_mm'][0]), math.copysign(1, row['center_mm'][1]))
                for row in report['bores']}

    for name in ('edge', 'datum'):
        before = sides(real.native_builds[name, 80][1].evaluation['report'])
        after = sides(real.native_builds[name, 100][1].evaluation['report'])
        assert len(before) == 4 and before == after  # 4/4 ids survive the width edit on the same bores
    assert sides(real.occt_builds[80][1].evaluation['report']) == sides(real.native_builds['edge', 80][1].evaluation['report'])


def test_real_native_report_matches_the_occt_report_shape_and_p05_tolerances(real):
    report = real.native_builds['edge', 80][1].evaluation['report']
    occt_report = real.occt_builds[80][1].evaluation['report']
    assert report['tolerances'] == {'length_mm': 1e-6, 'axis_component': 1e-8, 'volume_mm3': 1e-5}
    assert report['tolerances'] == occt_report['tolerances']
    assert report['envelope_mm'] == pytest.approx(occt_report['envelope_mm'], abs=1e-6)
    assert report['datum'] == occt_report['datum']


def test_real_wrong_intent_and_wrong_size_are_refused(real):
    adapter = real.native
    edge100 = real.native_builds['edge', 100][1].artifacts[0]
    datum100_plan = real.native_builds['datum', 100][0]
    edge80_plan = real.native_builds['edge', 80][0]
    assert run(adapter.inspect(edge100, real.native_builds['edge', 100][0])).outcome == 'consistent'
    for plan in (datum100_plan, edge80_plan):
        refused = run(adapter.inspect(edge100, plan))
        assert refused.outcome == 'precision_failure', (refused, plan.candidate.content.specification)
        assert 'measurement_out_of_tolerance' in refused.reasons


def test_real_inspect_and_export_behave_as_the_occt_rows(real):
    plan, built = real.native_builds['edge', 80]
    (step,) = built.artifacts
    inspected = run(real.native.inspect(step, plan))
    assert inspected.outcome == 'consistent' and inspected.artifact_sha256 == step.ref.sha256
    assert real.native.export(step, 'step').outcome == 'exported'
    assert real.native.export(step, 'glb').reasons == ('export_not_supported',)


def test_real_delivered_native_step_verifies_with_the_same_checks_as_occt(real):
    from engineering.program.adapters.occt import OcctAdapter  # noqa: F401
    from engineering.editable_plate_adapter import REPORT, STEP
    from tests.test_program_n1c3_delivered import World
    results = {}
    for backend, (plan, built) in (('native', real.native_builds['edge', 80]), ('occt', real.occt_builds[80])):
        world = World(NATIVE if backend == 'native' else 'occt', b'placeholder')
        assert world.candidate.candidate_sha256 == plan.candidate.candidate_sha256
        (step,) = built.artifacts
        world.blobs = {STEP: step.data, REPORT: built.evaluation['report_bytes']}
        verifier = world.verifier()
        requested = []

        def factory(name, **options):
            requested.append((name, options))
            return real.occt

        verifier.adapter_factory = factory
        record = asyncio.run(verifier.verify(world.revision, world.ir))
        assert record.verdict == 'verified', (backend, record.reasons)
        assert requested == [('occt', {'capability': 'editable_plate'})]
        results[backend] = record
    native, occt = results['native'], results['occt']
    assert [c.model_dump() for c in native.checks] == [c.model_dump() for c in occt.checks]
    assert native.claim_scope == occt.claim_scope == 'geometry_checks_only'
    assert native.step_sha256 != occt.step_sha256
