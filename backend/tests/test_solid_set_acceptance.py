"""Native STEP fixtures: structural solid-set acceptance, not legacy certification."""
import importlib
import math

import pytest
from OCP.BRep import BRep_Builder
from OCP.BRepPrimAPI import BRepPrimAPI_MakeBox
from OCP.IFSelect import IFSelect_RetDone
from OCP.STEPControl import STEPControl_Writer, STEPControl_AsIs
from OCP.TopoDS import TopoDS_Compound
from OCP.gp import gp_Pnt


def inspector():
    try:
        module = importlib.import_module("backend.verification.solid_set")
    except ModuleNotFoundError:
        pytest.fail("native structural solid-set inspector is missing")
    return module.inspect_step


def box(x=0):
    return BRepPrimAPI_MakeBox(gp_Pnt(x, 0, 0), 10, 10, 10).Solid()


def compound(*shapes):
    result = TopoDS_Compound()
    builder = BRep_Builder()
    builder.MakeCompound(result)
    for shape in shapes:
        builder.Add(result, shape)
    return result


def step(tmp_path, shape):
    path = tmp_path / "fixture.step"
    writer = STEPControl_Writer()
    assert writer.Transfer(shape, STEPControl_AsIs) == IFSelect_RetDone
    assert writer.Write(str(path)) == IFSelect_RetDone
    return str(path)


def first(shape, kind):
    from OCP.TopExp import TopExp_Explorer
    explorer = TopExp_Explorer(shape, kind)
    assert explorer.More()
    return explorer.Current()


@pytest.mark.parametrize("kind", ["FACE", "SHELL", "WIRE", "EDGE", "VERTEX"])
def test_solid_with_loose_topology_is_rejected(tmp_path, kind):
    from OCP import TopAbs
    loose = first(box(20), getattr(TopAbs, "TopAbs_" + kind))
    result = inspector()(step(tmp_path, compound(box(), loose)))
    assert not result["passed"], result
    assert "complete_solid_accounting" in result["checks"]
    assert result["failures"]


def test_open_shell_is_rejected(tmp_path):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shell
    shell = TopoDS_Shell()
    builder = BRep_Builder()
    builder.MakeShell(shell)
    faces = TopExp_Explorer(box(), TopAbs_FACE)
    for _ in range(5):
        builder.Add(shell, faces.Current())
        faces.Next()
    result = inspector()(step(tmp_path, shell))
    assert not result["passed"], result


@pytest.mark.parametrize("offset", [0, 5, 9.999])
def test_positive_volume_interpenetration_is_rejected(tmp_path, offset):
    result = inspector()(step(tmp_path, compound(box(), box(offset))))
    assert not result["passed"], result
    assert result["checks"]["no_positive_volume_interpenetration"] is False


@pytest.mark.parametrize("offset", [10, 20])
@pytest.mark.parametrize("wrapper", ["compound", "nested", "compsolid"])
def test_touching_and_separated_complete_bodies_are_preserved(tmp_path, offset, wrapper):
    from OCP.TopoDS import TopoDS_CompSolid
    if wrapper == "compsolid":
        shape = TopoDS_CompSolid()
        builder = BRep_Builder()
        builder.MakeCompSolid(shape)
        builder.Add(shape, box())
        builder.Add(shape, box(offset))
    else:
        shape = compound(box(), box(offset))
        if wrapper == "nested":
            shape = compound(compound(shape))
    result = inspector()(step(tmp_path, shape))
    assert result["passed"], result
    assert result["solid_count"] == 2
    assert result["volume_mm3"] == pytest.approx(2000)
    assert result["bounding_box"]["x_mm"] == pytest.approx(offset + 10)


@pytest.mark.parametrize("content", ["", "not STEP secret/internal/path", "ISO-10303-21;\nHEADER;\nENDSEC;\nDATA;\nENDSEC;\nEND-ISO-10303-21;"])
def test_empty_or_corrupt_step_fails_closed_without_native_details(tmp_path, content):
    path = tmp_path / "secret-file.step"
    path.write_text(content)
    result = inspector()(str(path))
    assert not result["passed"]
    assert result["failures"]
    assert all("secret" not in issue and str(tmp_path) not in issue for issue in result["failures"])


def test_missing_file_fails_closed(tmp_path):
    result = inspector()(str(tmp_path / "absent.step"))
    assert not result["passed"] and result["failures"]


def test_step_size_limit_is_explicit(tmp_path):
    path = tmp_path / "too-large.step"
    with path.open("wb") as stream:
        stream.truncate(500 * 1024 * 1024 + 1)
    result = inspector()(str(path))
    assert not result["passed"]
    assert any("500 MiB" in issue for issue in result["failures"])


def test_solid_limit_is_explicit(tmp_path):
    result = inspector()(step(tmp_path, compound(*(box(i * 20) for i in range(33)))))
    assert not result["passed"], result
    assert any("32 solid" in issue for issue in result["failures"])


def test_topology_limit_is_explicit(tmp_path, monkeypatch):
    # Lower only the traversal budget, retaining a real transferred STEP box.
    module = importlib.import_module("backend.verification.solid_set")
    monkeypatch.setattr(module, "MAX_TOPOLOGY_ELEMENTS", 10, raising=False)
    result = inspector()(step(tmp_path, box()))
    assert not result["passed"], result
    assert any("topology-element" in issue for issue in result["failures"])


def reader_with_native_shape(monkeypatch, shape):
    """Reader seam only: native malformed topology may be healed by STEP import."""
    import OCP.STEPControl as native
    real_reader = native.STEPControl_Reader

    class ShapeReader:
        def __init__(self):
            self.reader = real_reader()

        def __getattr__(self, name):
            return getattr(self.reader, name)

        def OneShape(self):
            return shape

    monkeypatch.setattr(native, "STEPControl_Reader", ShapeReader)


def test_excessive_native_tolerance_is_rejected(tmp_path, monkeypatch):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopoDS import TopoDS
    shape = box()
    BRep_Builder().UpdateFace(TopoDS.Face_s(first(shape, TopAbs_FACE)), 0.01)
    path = step(tmp_path, shape)
    # This deployment's STEP reader normalizes the .01 native tolerance away.
    # Exercise the rejection against the real native shape at the reader seam.
    reader_with_native_shape(monkeypatch, shape)
    result = inspector()(path)
    assert not result["passed"], result
    assert result["checks"]["topology_tolerance_policy"] is False


def test_empty_nested_wrapper_is_rejected_at_native_reader_seam(tmp_path, monkeypatch):
    path = step(tmp_path, box())
    reader_with_native_shape(monkeypatch, compound(box(), compound()))
    result = inspector()(path)
    assert not result["passed"], result
    assert any("empty topology" in issue for issue in result["failures"])


def test_open_solid_is_rejected_at_native_reader_seam(tmp_path, monkeypatch):
    from OCP.TopAbs import TopAbs_FACE
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS_Shell, TopoDS_Solid
    shell, solid = TopoDS_Shell(), TopoDS_Solid()
    builder = BRep_Builder()
    builder.MakeShell(shell)
    builder.MakeSolid(solid)
    faces = TopExp_Explorer(box(), TopAbs_FACE)
    for _ in range(5):
        builder.Add(shell, faces.Current())
        faces.Next()
    builder.Add(solid, shell)
    path = step(tmp_path, box())
    reader_with_native_shape(monkeypatch, solid)
    result = inspector()(path)
    assert not result["passed"]
    assert result["checks"].get("closed_shells") is False


def test_native_validity_and_closed_shell_checks_are_reported(tmp_path):
    result = inspector()(step(tmp_path, box()))
    assert result["checks"].get("brep_valid_geometry") is True
    assert result["checks"].get("closed_shells") is True


@pytest.mark.parametrize("mode", ["transfer_zero", "transfer_partial", "null", "read_exception"])
def test_native_reader_errors_fail_closed(tmp_path, monkeypatch, mode):
    import OCP.STEPControl as native
    from OCP.TopoDS import TopoDS_Shape
    path = step(tmp_path, box())
    real_reader = native.STEPControl_Reader

    class FaultReader:
        def __init__(self):
            self.reader = real_reader()

        def __getattr__(self, name):
            return getattr(self.reader, name)

        def ReadFile(self, path):
            if mode == "read_exception":
                raise RuntimeError("secret internal exception")
            return self.reader.ReadFile(path)

        def NbRootsForTransfer(self):
            return 2 if mode == "transfer_partial" else self.reader.NbRootsForTransfer()

        def TransferRoots(self):
            if mode == "transfer_zero":
                return 0
            return self.reader.TransferRoots()

        def OneShape(self):
            return TopoDS_Shape() if mode == "null" else self.reader.OneShape()

    monkeypatch.setattr(native, "STEPControl_Reader", FaultReader)
    result = inspector()(path)
    assert not result["passed"], result
    assert result["failures"] and all("secret" not in s for s in result["failures"])


def test_missing_ocp_never_falls_back_to_other_bindings(tmp_path, monkeypatch):
    import builtins
    path = step(tmp_path, box())
    real_import = builtins.__import__

    def unavailable(name, *args, **kwargs):
        if name == "OCP" or name.startswith("OCP."):
            raise ImportError("secret DLL path")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", unavailable)
    result = inspector()(path)
    assert result["passed"] is False
    assert result["checks"]["native_backend"] is False
    assert result["failures"] == ["Native OCP backend is unavailable."]


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0.0, -1.0])
def test_nonfinite_or_nonpositive_tolerance_fails_closed(tmp_path, monkeypatch, value):
    from OCP.BRep import BRep_Tool
    path = step(tmp_path, box())
    monkeypatch.setattr(BRep_Tool, "Tolerance_s", staticmethod(lambda shape: value))
    result = inspector()(path)
    assert not result["passed"], result
    assert result["checks"]["topology_tolerance_policy"] is False
    assert result["max_topology_tolerance_mm"] is None


def test_thirty_two_separated_solids_are_accepted(tmp_path):
    result = inspector()(step(tmp_path, compound(*(box(i * 20) for i in range(32)))))
    assert result["passed"], result
    assert result["solid_count"] == 32


def test_native_box_has_positive_finite_metrics(tmp_path):
    result = inspector()(step(tmp_path, box()))
    assert result["passed"], result
    assert result["scope"] == "structural_solid_set_v1"
    assert result["solid_count"] == 1
    assert result["volume_mm3"] == pytest.approx(1000)
    assert result["surface_area_mm2"] == pytest.approx(600)
    assert result["bounding_box"] == pytest.approx(dict(x_mm=10, y_mm=10, z_mm=10))
    assert 0 < result["max_topology_tolerance_mm"] <= 0.001
    assert result["checks"] and all(result["checks"].values())
    assert result["failures"] == []
    assert all(math.isfinite(result[key]) for key in ("volume_mm3", "surface_area_mm2"))
