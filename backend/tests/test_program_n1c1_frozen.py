"""N1c-1: bytes and hashes that must not change when mirum-native is registered.

Every pin below was computed on main (9e43786) BEFORE the N1c-1 change.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from engineering.language.semantic import compile_source
from engineering.program import schema_export as se
from engineering.program.hashing import (
    canonical_json, execution_lock_sha256, execution_lock_v0_2_sha256, semantic_sha256,
)
from engineering.program.ir import execution_lock_v0_2, load_execution_lock_json
from engineering.program.kernel import (
    ExecutionLockV0_2, NativeVersion, ProbeResult, backend_lock, make_description,
)

REPO = Path(__file__).resolve().parents[2]
FIXTURES = Path(__file__).resolve().parent / 'fixtures' / 'program' / 'n1c1'

FROZEN_SCHEMA_SHA256 = {
    'mirum_execution_lock_v0_1.schema.json':
        'b1a5068a6a4f8192b2beb555cc9403ec035fa6950896660feb111cfd34186deb',
    'mirum_execution_lock_v0_2.schema.json':
        '91101c8df9a0943a230c4c320c31a4c184705b7a0ecb19c81208b5756d30a97e',
    'mirum_operation_registry_v0_1.json':
        '0f20ac6e522e485b787ecc2e54feb55def59868ec13ceea574ae1c4d932c54df',
    'mirum_operation_registry_v0_2.json':
        '5599b303e941a7374e0974f1453b3aeb1311d7ff1d8efe87756cd344a7309d8e',
    'mirum_operation_registry_v0_3.json':
        '6f2df9b04e280455c181b6e54d221f3d26d43cf7c4e1f815c2879ca6bf4b19e5',
    'mirum_operation_registry_v0_4.json':
        '46579c3b7c03ad6b7bbb5d5ceff0adebacf2cbf24a41d835b71dc1a334f4efb2',
    'mirum_operation_registry_v0_5.json':
        'f3b6aa21cc0bc191a1ae3b960a605a135507af00fade83139cd02895daa311d6',
}

# (ir, semantic, execution lock v0_1, execution lock v0_2 over a synthetic native backend lock)
MEL_PINS = {
    'backend/engineering/fixtures/editable_plate/datum.mir': (
        '904b2f5fdde3d6ae2641fa69d755637d24bb159541d756b9265873ff2cdb89e1',
        '18e3f886470f2beb6f7a7132e8ed482f9fcb99403d92f5f354f038dee1e102bf',
        '707cc83a2bc1f37f3368cbfd9d08581996c30718549230a608558e0e96c2804a',
        'dc9f96c11bdafe2e7d6a1a2b5847a680ee8b435f12f58c0ec8f54401caa3645c'),
    'backend/engineering/fixtures/editable_plate/edge.mir': (
        'f626e536eaac1f794f098fa519dbbcab4602995a26165f9a0af42a736d9c053b',
        'bf4c11c18dcc96e466a2e541d55385ee27798f7a741e06b8234ea2547ea5149f',
        '707cc83a2bc1f37f3368cbfd9d08581996c30718549230a608558e0e96c2804a',
        'dc9f96c11bdafe2e7d6a1a2b5847a680ee8b435f12f58c0ec8f54401caa3645c'),
    'backend/engineering/fixtures/equipment_bay/nominal.mir': (
        '7611c2e9a79b32a4c2b828b997ec73723fd63b73781df51081e62e1fbb91cc18',
        '6e721d2fcbef9e0fd5b15e88b582b8829747049390c8d790b76f457a6f43314e',
        '0d6c98568c3b9c7539bd44af8dc98df65da5c3abf03d7602a7eba71f804c83c1',
        'f8bbef494260818cb90a09e0db1c04e435497537b56d71339493002218943ac0'),
    'backend/engineering/fixtures/picogk/reference.mir': (
        '9bb4c0ce11a0678f00cbf2ca60fc93375218001738ce13e4d056c655f2791016',
        '657c5bd472d7919e67e2bc480892d908ec5a708e8e7d16991bb2f405455d9b78',
        'be0d6a80873ad26b531164503a649621e802d16e84e13d9b253408faa3635265',
        'd6011fdd594009b14429aa7c9153da585283996b210b3b8fdd0e17f33f8a984d'),
    'backend/engineering/fixtures/structural/cantilever.mir': (
        '6fc493800a85bf7e62eef3e5a2cabbefb21ddbf404a13649c95bb8b5657fb621',
        'bb56efc286e64dcad01151f5f93f2db4a12146faba913845128fa7aa25d2545a',
        '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e',
        '68116327b2208f88cc9d6af2d6fd1759ebe23d92cd97223dee3db4ebab4081b5'),
    'backend/engineering/fixtures/thermal/bar.mir': (
        '6de2a831f2e6a0c0fcb1879d787213d378dc2ce6d1153899e63b636b91fe43da',
        '8aa711c656cea924ee2f175fd2fbb2e266c4b4fbe932422a7d5624feb43b8791',
        'b537d92fa8eca60e3c000e23743b22829526983643aef5e3e934cd4671a23c71',
        '652a50e93885ca8c13f722d9bff0f5addd1d5fa5e3be81351d9b1b0162d5fda2'),
    'backend/tests/fixtures/program/f06/beam_5_10.mir': (
        '621bbf61307a6df3e2dc1b9e956059ca1a64edd7bfc433ce5278178f5ce77507',
        'da169ebe7ed3e523f8fd6dec6296c74146613d7ca8fd3c949742e9c164fcd836',
        '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e',
        '68116327b2208f88cc9d6af2d6fd1759ebe23d92cd97223dee3db4ebab4081b5'),
    'backend/tests/fixtures/program/f06/beam_heights.mir': (
        '3593f52d655d48c894baa7b5da605d7f5fc363947d4fb8ced71dbbb4fcc281ca',
        '615e7c3d60728f364625a145bc8443e792fdb26deb56cc36e55fb69cef39c215',
        '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e',
        '68116327b2208f88cc9d6af2d6fd1759ebe23d92cd97223dee3db4ebab4081b5'),
    'backend/tests/fixtures/program/f06/beam_tip60.mir': (
        '2c7541fd6be6d91ae82b09acfbcd46634a76223d4704003cb67956f6dc5b578c',
        '8811c6b0c339fd99fbe9937cf5721a6fb345fa84b987d1d21b079308410e126a',
        '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e',
        '68116327b2208f88cc9d6af2d6fd1759ebe23d92cd97223dee3db4ebab4081b5'),
    'backend/tests/fixtures/program/f06/beam_whitespace.mir': (
        'e64303f3f858aa851b1510efe6c54e8a49db70a6b04e7113864106e15c8adf2c',
        '4d81808ade568a03fd196034652f3c4d1257b94623be178707202f91cd36c068',
        '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e',
        '68116327b2208f88cc9d6af2d6fd1759ebe23d92cd97223dee3db4ebab4081b5'),
    'backend/tests/fixtures/program/f08/beam.mir': (
        '345eec424a2d346005e3ea6c0ce9c83171c48524e8d6e78ca33819cbfb14e15f',
        '4d81808ade568a03fd196034652f3c4d1257b94623be178707202f91cd36c068',
        '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e',
        '68116327b2208f88cc9d6af2d6fd1759ebe23d92cd97223dee3db4ebab4081b5'),
    'backend/tests/fixtures/program/f08/beam_5_10.mir': (
        '621bbf61307a6df3e2dc1b9e956059ca1a64edd7bfc433ce5278178f5ce77507',
        'da169ebe7ed3e523f8fd6dec6296c74146613d7ca8fd3c949742e9c164fcd836',
        '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e',
        '68116327b2208f88cc9d6af2d6fd1759ebe23d92cd97223dee3db4ebab4081b5'),
    'backend/tests/fixtures/program/p03/beam.mir': (
        '345eec424a2d346005e3ea6c0ce9c83171c48524e8d6e78ca33819cbfb14e15f',
        '4d81808ade568a03fd196034652f3c4d1257b94623be178707202f91cd36c068',
        '852fa8eaf0e94d5c64662e260fd839afb34dfdd3939b30695b3688aa7d4eb95e',
        '68116327b2208f88cc9d6af2d6fd1759ebe23d92cd97223dee3db4ebab4081b5'),
}

DESCRIPTION_PINS = {
    'occt_description': 'c600f6655524de17a13bc3be38fc3a25204f96262dcdb68a2837896a8570e7e5',
    'occt_mock_description': '0cce3965352f07eccba5059d1d13b2cf2014458293d60dce8384b243000cef62',
    'picogk_description': 'a6f4ea8488694f1943dbc9c2ed0a1b7435d1bb80d3cf9c0ac1754cb54909761f',
    'picogk_mock_description': '3f9ad3f60a97b4d9837780405b5318f705a7bd8d72bf0eaab0692d49516ec853',
}

LOCK_FIXTURE_SHA256 = {
    'execution-lock-v0_1-beam.json': '1d67442ec3e5a120435ce07ccffabd40685d0a19ac26be48541b444c68f543ce',
    'execution-lock-v0_2-beam.json': 'fe4c38538272472aec91a520f1c085cfc39896d822b72bde8d0e8146b09b8f30',
}


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize('name', sorted(FROZEN_SCHEMA_SHA256))
def test_frozen_schema_and_registry_files_are_byte_identical(name):
    assert _sha((se.SCHEMA_DIR / name).read_bytes()) == FROZEN_SCHEMA_SHA256[name]


@pytest.mark.parametrize('rel', sorted(MEL_PINS))
def test_existing_mel_fixtures_compile_to_identical_ir_semantic_and_lock_hashes(rel):
    ir = compile_source((REPO / rel).read_bytes()).ir
    ir_sha, semantic, lock_v1, lock_v2 = MEL_PINS[rel]
    assert _sha(canonical_json(ir).encode()) == ir_sha
    assert semantic_sha256(ir) == semantic
    assert execution_lock_sha256(ir) == lock_v1
    be = ir.execution.backend
    probe = ProbeResult(backend=be, mode='native', available=True,
                        native_versions=(NativeVersion(name='x', version='1'),))
    lock = execution_lock_v0_2(ir, backend_lock(make_description(be, mode='native', adapter_version='adapter_1'),
                                                probe))
    assert execution_lock_v0_2_sha256(lock) == lock_v2


@pytest.mark.parametrize('backend', ['occt', 'picogk'])
def test_existing_backend_descriptions_hash_identically(backend):
    native = make_description(backend, mode='native', adapter_version='adapter_1')
    mock = make_description(backend, mode='mock', adapter_version='adapter_1')
    assert _sha(canonical_json(native).encode()) == DESCRIPTION_PINS[f'{backend}_description']
    assert _sha(canonical_json(mock).encode()) == DESCRIPTION_PINS[f'{backend}_mock_description']


@pytest.mark.parametrize('name', sorted(LOCK_FIXTURE_SHA256))
def test_committed_lock_fixtures_are_unchanged_bytes(name):
    assert _sha((FIXTURES / name).read_bytes()) == LOCK_FIXTURE_SHA256[name]


def test_committed_v0_2_lock_loads_round_trips_and_keeps_its_hash():
    raw = (FIXTURES / 'execution-lock-v0_2-beam.json').read_bytes()
    lock = load_execution_lock_json(raw)
    assert type(lock) is ExecutionLockV0_2
    assert lock.backend == 'occt' and lock.backend_lock.schema_version == 'mirum_kernel_v0_1'
    assert (canonical_json(lock) + '\n').encode() == raw
    assert execution_lock_v0_2_sha256(lock) == _sha(canonical_json(lock).encode())


def test_committed_v0_1_lock_loads_and_keeps_its_hash():
    raw = (FIXTURES / 'execution-lock-v0_1-beam.json').read_bytes()
    lock = load_execution_lock_json(raw)
    assert lock.schema_version == 'mirum_execution_lock_v0_1'
    assert (canonical_json(lock) + '\n').encode() == raw
    assert execution_lock_sha256(lock) == MEL_PINS['backend/engineering/fixtures/structural/cantilever.mir'][2]
