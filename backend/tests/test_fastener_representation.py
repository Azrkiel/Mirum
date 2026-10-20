"""Defect D: fastener geometry is labelled by representation and never accepted as a thread fit.

The legacy preview pair's measured interference is retained as a failing
engineering-fit regression; the envelope pair is the non-interfering control.
"""
import math
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from verification.fasteners import assess_thread_fit, fastener_evidence  # noqa: E402

native = pytest.mark.skipif(sys.platform == "win32", reason="Native OCP geometry runs in Linux Docker")


def _overlap(a, b):
    return a.intersect(b).Volume()


def _cylinder_radii(solid):
    radii = set()
    for face in solid.Faces():
        if face.geomType() == "CYLINDER":
            radii.add(round(face.BoundingBox().xlen / 2.0, 6))
    return radii


@native
def test_recorded_preview_pair_interference_is_a_failing_fit():
    from cad_utils import make_hex_nut, make_metric_thread
    thread = make_metric_thread(10, 1.5, 12).val()
    nut = make_hex_nut(10, 1.5, 17, 8).val()
    overlap = _overlap(thread, nut)
    assert overlap == pytest.approx(82.02268474387722, rel=1e-6)  # unchanged legacy default
    fit = assess_thread_fit("preview", "preview")
    assert fit["passed"] is False and fit["status"] == "preview_only"


@native
def test_explicit_preview_mode_is_the_unchanged_default():
    from cad_utils import make_hex_bolt, make_hex_nut, make_metric_thread
    assert make_metric_thread(10, 1.5, 12, representation="preview").val().Volume() == pytest.approx(
        make_metric_thread(10, 1.5, 12).val().Volume(), rel=1e-12)
    assert make_hex_nut(10, 1.5, representation="preview").val().Volume() == pytest.approx(
        make_hex_nut(10, 1.5).val().Volume(), rel=1e-12)
    assert make_hex_bolt(10, 1.5, 30, representation="preview").val().Volume() == pytest.approx(
        make_hex_bolt(10, 1.5, 30).val().Volume(), rel=1e-12)


@native
def test_envelope_pair_does_not_interfere_and_reimports(tmp_path):
    import cadquery as cq
    from cad_utils import make_hex_bolt, make_hex_nut, make_metric_thread
    male = make_metric_thread(10, 1.5, 12, representation="envelope").val()
    female = make_hex_nut(10, 1.5, 17, 8, representation="envelope").val()
    assert male.isValid() and female.isValid()
    limit = 1e-7 * min(male.Volume(), female.Volume())
    assert _overlap(male, female) <= limit
    assert male.Volume() == pytest.approx(math.pi * 25 * 12, rel=1e-9)
    assert _cylinder_radii(female) == {5.0}
    # A preview bolt through an envelope nut also only touches it.
    assert _overlap(make_metric_thread(10, 1.5, 12).val(), female) <= limit
    bolt = make_hex_bolt(10, 1.5, 30, representation="envelope").val()
    assert len(bolt.Solids()) == 1 and bolt.isValid()
    for name, shape in (("male", male), ("female", female)):
        path = tmp_path / f"{name}.step"
        cq.exporters.export(cq.Workplane(obj=shape), str(path))
        back = cq.importers.importStep(str(path)).val()
        assert back.Volume() == pytest.approx(shape.Volume(), rel=1e-9)
        assert _cylinder_radii(back) == _cylinder_radii(shape)
    fit = assess_thread_fit("envelope", "envelope")
    assert fit["passed"] is False and fit["status"] == "envelope_only"


@native
@pytest.mark.parametrize("helper", ["make_metric_thread", "make_hex_bolt", "make_hex_nut"])
@pytest.mark.parametrize("mode", ["physical", "helical", "ISO-6g", None])
def test_unqualified_thread_modes_fail_closed(helper, mode):
    import cad_utils
    args = {"make_metric_thread": (10, 1.5, 12), "make_hex_bolt": (10, 1.5, 30),
            "make_hex_nut": (10, 1.5)}[helper]
    with pytest.raises(ValueError, match="representation"):
        getattr(cad_utils, helper)(*args, representation=mode)


@native
@pytest.mark.parametrize("args", [(10, 0, 12), (10, 9, 12), (float("nan"), 1.5, 12), (10, 1.5, -1),
                                  (10, 1.5, float("inf")), (True, 1.5, 12)])
def test_invalid_thread_dimensions_are_rejected(args):
    from cad_utils import make_metric_thread
    with pytest.raises(ValueError):
        make_metric_thread(*args)


@native
@pytest.mark.parametrize("d,p", [(3, 0.5), (6, 1.0), (10, 1.5), (20, 2.5)])
@pytest.mark.parametrize("engagement", [1.0, 2.0])
def test_bounded_kernel_robustness_across_sizes(d, p, engagement):
    from cad_utils import make_hex_bolt, make_hex_nut, make_metric_thread
    start = time.monotonic()
    for mode in ("preview", "envelope"):
        thread = make_metric_thread(d, p, engagement * d, representation=mode).val()
        bolt = make_hex_bolt(d, p, engagement * d, representation=mode).val()
        nut = make_hex_nut(d, p, representation=mode).val()
        for shape in (thread, bolt, nut):
            assert shape.isValid() and len(shape.Solids()) == 1
    envelope_overlap = _overlap(make_metric_thread(d, p, engagement * d, representation="envelope").val(),
                                make_hex_nut(d, p, representation="envelope").val())
    assert envelope_overlap <= 1e-6 * d ** 3
    assert time.monotonic() - start < 20.0


def test_static_evidence_labels_every_fastener_call():
    scripts = {
        "bolt": "from cad_utils import make_hex_bolt\nresult = make_hex_bolt(10, 1.5, 30)\n",
        "nut": "import cad_utils\nresult = cad_utils.make_hex_nut(10, 1.5, representation='envelope')\n",
        "odd": "from cad_utils import make_metric_thread\nm='envelope'\nresult = make_metric_thread(5, 0.8, 9, representation=m)\n",
        "plate": "import cadquery as cq\nresult = cq.Workplane().box(1, 2, 3)\n",
    }
    evidence = fastener_evidence(scripts)
    modes = {c["part_id"]: c["representation"] for c in evidence["fasteners"]}
    assert modes == {"bolt": "preview", "nut": "envelope", "odd": "unknown"}
    assert evidence["thread_fit_evaluated"] is False
    assert evidence["manufacturing_thread_geometry"] is False
    assert fastener_evidence({"plate": scripts["plate"]}) is None
