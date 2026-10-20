"""Native STEP certification regressions for CadQuery's OCP runtime.

These tests intentionally require CadQuery/OCP: missing native support is a
failure, not a skip. Run in the deployment image without pythonocc-core.
Geometric validity does not establish engineering intent or design compliance.
"""

from __future__ import annotations

import importlib.util
import math

import cadquery as cq
import pytest

from verification import geometry


CHECK_NAMES = {
    "volume_positive",
    "surface_area_positive",
    "no_degenerate_faces",
    "bounding_box_finite",
    "brep_valid",
    "is_solid",
}
PROPULSION_NAMES = {
    "throat_area_mm2",
    "exit_area_mm2",
    "geometric_expansion_ratio",
    "channel_count_detected",
}
CERTIFICATE_NAMES = {
    "passed", "volume_mm3", "surface_area_mm2", "bounding_box", "checks", "failures",
} | PROPULSION_NAMES


def test_real_exported_box_is_certified_in_ocp_only_runtime(tmp_path):
    assert importlib.util.find_spec("OCC") is None, "Run in the OCP-only image"
    step_path = tmp_path / "box.step"
    cq.exporters.export(cq.Workplane("XY").box(10, 20, 30), str(step_path))

    cert = geometry.certify_geometry(str(step_path))

    assert cert["passed"] is True, cert["failures"]
    assert set(cert) == CERTIFICATE_NAMES
    assert cert["checks"] == dict.fromkeys(CHECK_NAMES, True)
    assert cert["failures"] == []
    assert cert["volume_mm3"] == pytest.approx(6000.0, abs=1e-6)
    assert cert["surface_area_mm2"] == pytest.approx(2200.0, abs=1e-6)
    # OCC's bounding boxes include the shape tolerance around each endpoint.
    assert cert["bounding_box"] == pytest.approx(
        {"x_mm": 10.0, "y_mm": 20.0, "z_mm": 30.0}, abs=1e-5
    )
    assert all(cert[name] is None for name in PROPULSION_NAMES)


def test_real_exported_open_shell_is_not_certified(tmp_path):
    box = cq.Workplane("XY").box(10, 20, 30).val()
    # Remove one face: this is a real five-face shell, not a mocked solid.
    faces = box.Faces()
    shell = cq.Shell.makeShell(faces[1:])
    step_path = tmp_path / "open_shell.step"
    cq.exporters.export(shell, str(step_path))

    cert = geometry.certify_geometry(str(step_path))

    assert cert["passed"] is False
    assert set(cert) == CERTIFICATE_NAMES
    assert cert["checks"]["is_solid"] is False
    assert any("no solid sub-shapes" in failure for failure in cert["failures"])
    # Prove the shell was actually loaded/measured, not rejected for imports.
    assert cert["surface_area_mm2"] == pytest.approx(
        2200.0 - faces[0].Area(), abs=1e-6
    )
    assert cert["checks"]["surface_area_positive"] is True
    assert cert["bounding_box"] == pytest.approx(
        {"x_mm": 10.0, "y_mm": 20.0, "z_mm": 30.0}, abs=1e-5
    )


def test_real_exported_empty_compound_is_not_certified(tmp_path):
    from OCP.BRep import BRep_Builder
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.STEPControl import STEPControl_AsIs, STEPControl_Writer
    from OCP.TopoDS import TopoDS_Compound

    compound = TopoDS_Compound()
    BRep_Builder().MakeCompound(compound)
    step_path = tmp_path / "empty_compound.step"
    writer = STEPControl_Writer()
    assert writer.Transfer(compound, STEPControl_AsIs) == IFSelect_RetDone
    assert writer.Write(str(step_path)) == IFSelect_RetDone

    cert = geometry.certify_geometry(str(step_path))

    assert cert["passed"] is False
    assert set(cert) == CERTIFICATE_NAMES
    assert cert["checks"]["volume_positive"] is False
    assert cert["checks"]["surface_area_positive"] is False
    assert cert["checks"]["is_solid"] is False
    assert cert["volume_mm3"] is None or cert["volume_mm3"] == 0.0
    assert cert["surface_area_mm2"] is None or cert["surface_area_mm2"] == 0.0
    assert cert["failures"]


@pytest.mark.parametrize("contents", [b"", b"not an ISO-10303-21 STEP file\n"])
def test_unreadable_step_returns_failed_certificate(tmp_path, contents):
    step_path = tmp_path / "unreadable.step"
    step_path.write_bytes(contents)

    cert = geometry.certify_geometry(str(step_path))

    assert set(cert) == CERTIFICATE_NAMES
    assert cert["passed"] is False
    assert cert["checks"] == dict.fromkeys(CHECK_NAMES, False)
    assert cert["volume_mm3"] is None
    assert cert["surface_area_mm2"] is None
    assert cert["bounding_box"] is None
    assert cert["failures"][0].startswith("STEP load error:")
    assert all(cert[name] is None for name in PROPULSION_NAMES)


def test_propulsion_values_remain_spec_metadata_not_measured_intent(tmp_path):
    from propulsion.specs import PropulsionSpec

    step_path = tmp_path / "box_with_nozzle_metadata.step"
    cq.exporters.export(cq.Workplane("XY").box(10, 20, 30), str(step_path))
    spec = PropulsionSpec(
        propulsion_type="nozzle", throat_diameter_mm=40.0, exit_diameter_mm=130.0
    )

    ordinary = geometry.certify_geometry(str(step_path))
    cert = geometry.certify_geometry(str(step_path), propulsion_spec=spec)

    assert cert["passed"] is True, cert["failures"]
    assert {key: value for key, value in cert.items() if key not in PROPULSION_NAMES} == {
        key: value for key, value in ordinary.items() if key not in PROPULSION_NAMES
    }
    assert cert["throat_area_mm2"] == pytest.approx(math.pi * 20.0 ** 2)
    assert cert["exit_area_mm2"] == pytest.approx(math.pi * 65.0 ** 2)
    assert cert["geometric_expansion_ratio"] == pytest.approx(10.5625)
    assert cert["channel_count_detected"] is None
    # The STEP is still only a box. Passing certifies geometry, NOT that this
    # box implements the supplied nozzle dimensions or any engineering intent.


def test_missing_step_returns_failed_certificate_without_loading_backend(tmp_path, monkeypatch):
    def unexpected_import():
        pytest.fail("A nonexistent STEP file must be rejected before backend imports")

    monkeypatch.setattr(geometry, "_import_occ", unexpected_import)
    cert = geometry.certify_geometry(str(tmp_path / "does_not_exist.step"))

    assert set(cert) == CERTIFICATE_NAMES
    assert cert["passed"] is False
    assert cert["checks"] == dict.fromkeys(CHECK_NAMES, False)
    assert cert["volume_mm3"] is None
    assert cert["surface_area_mm2"] is None
    assert cert["bounding_box"] is None
    assert cert["failures"][0].startswith("STEP file not found:")
    assert all(cert[name] is None for name in PROPULSION_NAMES)


def test_missing_backend_returns_failed_certificate(tmp_path, monkeypatch):
    step_path = tmp_path / "box.step"
    cq.exporters.export(cq.Workplane("XY").box(10, 20, 30), str(step_path))

    def unavailable_backend():
        raise ImportError("no OpenCASCADE binding")

    monkeypatch.setattr(geometry, "_import_occ", unavailable_backend)
    cert = geometry.certify_geometry(str(step_path))

    assert set(cert) == CERTIFICATE_NAMES
    assert cert["passed"] is False
    assert cert["checks"] == dict.fromkeys(CHECK_NAMES, False)
    assert cert["volume_mm3"] is None
    assert cert["surface_area_mm2"] is None
    assert cert["bounding_box"] is None
    assert "no OpenCASCADE binding" in cert["failures"][0]
    assert all(cert[name] is None for name in PROPULSION_NAMES)


def test_import_occ_dictionary_remains_injectable_with_real_native_operations(tmp_path, monkeypatch):
    step_path = tmp_path / "injected_box.step"
    cq.exporters.export(cq.Workplane("XY").box(10, 20, 30), str(step_path))
    native_operations = geometry._import_occ()
    calls = []

    def injected_backend():
        calls.append(True)
        return native_operations

    monkeypatch.setattr(geometry, "_import_occ", injected_backend)
    cert = geometry.certify_geometry(str(step_path))

    assert calls == [True]
    assert cert["passed"] is True, cert["failures"]
    assert cert["checks"] == dict.fromkeys(CHECK_NAMES, True)
    assert cert["volume_mm3"] == pytest.approx(6000.0, abs=1e-6)


def test_missing_ocp_measurement_capability_fails_closed(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import OCP.BRepGProp as native_gprop

    step_path = tmp_path / "box.step"
    cq.exporters.export(cq.Workplane("XY").box(10, 20, 30), str(step_path))
    # Simulate a binding lacking a required static method, not fake geometry.
    monkeypatch.setattr(
        native_gprop, "BRepGProp",
        SimpleNamespace(SurfaceProperties_s=native_gprop.BRepGProp.SurfaceProperties_s),
    )

    cert = geometry.certify_geometry(str(step_path))

    assert set(cert) == CERTIFICATE_NAMES
    assert cert["passed"] is False
    assert cert["checks"] == dict.fromkeys(CHECK_NAMES, False)
    assert cert["volume_mm3"] is None
    assert cert["surface_area_mm2"] is None
    assert cert["bounding_box"] is None
    assert "VolumeProperties_s" in cert["failures"][0]
