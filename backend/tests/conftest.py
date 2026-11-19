"""Shared fixtures for the CEM native-tool tests."""
import pytest

ELF_MAGIC = b'\x7fELF'


@pytest.fixture
def native_xfoil():
    """Skip unless MIRUM_XFOIL_BIN resolves to the real natively built XFOIL.

    The offline CI image points MIRUM_XFOIL_BIN at analysis/tests/mock_xfoil, a
    Python script emulating a NACA 0012 polar. It resolves successfully but
    cannot produce the XFOIL 6.99 header, coordinate-airfoil or panel output
    these tests require. A compiled executable (ELF) is the capability check
    a script cannot satisfy. Build: python scripts/cem/build_native_aero.py
    """
    # Imported here, not at module level: this conftest also loads for suites
    # (e.g. the regen browser job) whose environment has no aerosandbox.
    from cem.aero.native import NativeFailure, resolve
    try:
        path, _ = resolve('xfoil')
    except NativeFailure:
        pytest.skip('Native XFOIL executable not selected')
    with path.open('rb') as stream:
        if stream.read(4) != ELF_MAGIC:
            pytest.skip('MIRUM_XFOIL_BIN is a script/mock, not the natively built XFOIL '
                        '(scripts/cem/build_native_aero.py)')
