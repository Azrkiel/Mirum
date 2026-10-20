"""Behavioral regressions ported from Text-2-CAD's local compiler review.

The scanner inspects these strings; it never executes the rejected scripts.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from compiler import _check_ast_security


@pytest.mark.parametrize("script", [
    "import cadquery as cq\nresult = cq.occ_impl.shapes.sys",
    "from cadquery import os\nresult = os",
    "gen = (i for i in range(1))\nresult = gen.gi_frame.f_globals",
    "result = '{0.__class__}'.format(1)",
    "import cadquery as cq\nresult = cq.Workplane('XY')._plane",
    "import cadquery as cq\nresult = cq.numpy.load('data.npy')",
    "import cadquery as cq\nlib = cq.utils.CDLL('libc.so.6')",
    "from . import helper\nresult = None",
    "match ():\n    case tuple(__class__=c):\n        result = c\n",
])
def test_rejects_additional_reflective_and_module_paths(script):
    assert _check_ast_security(script) is not None


@pytest.mark.parametrize("script", [None, 123, "x = '\x00'"])
def test_malformed_input_is_a_rejection_not_an_exception(script):
    assert _check_ast_security(script) is not None


@pytest.mark.parametrize("script", [
    "import cadquery as cq\nresult = cq.Workplane('XY').box(10, 20, 3)",
    "from cad_aero import make_wing_segment\n_size = 3\nresult = _size * 2",
    "import cad_utils\nresult = None",
    "import math\nif __name__ == '__main__':\n    print(math.sqrt(4))",
])
def test_keeps_mirum_public_geometry_helpers(script):
    assert _check_ast_security(script) is None
