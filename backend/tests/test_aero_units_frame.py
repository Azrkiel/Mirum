"""Domain D units and frame consistency (real CadQuery/OCP, no model calls).

1. cad_aero builders work in metres; Mirum's CAD/STEP unit is millimetres.
   to_mm() converts, and the critic rejects builder output left in metres.
2. Free-form RULE 2 sections share cad_aero's frame: LE at x=0, upper +Z.
3. schemas.py documents the deprecated absolute MatingRule.translation honestly.
"""
import asyncio
import os
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ.setdefault('GEMINI_API_KEY_HEAVY', 'offline-fixture-never-sent')

cq = pytest.importorskip('cadquery')
import agents
import cad_aero
import cad_utils
import schemas
import strategies

SPAR_KW = dict(span_m=5.36, root_chord_m=1.79, tip_chord_m=0.89, x_frac=0.25,
               web_thickness_m=0.0008)


def _spar():
    return cad_aero.make_spar(airfoil_points=cad_aero.naca4_points('2412', 1.0), **SPAR_KW)


def _step_span_mm(tmp_path, assembly):
    path = tmp_path / 'wing.step'
    assembly.save(str(path), exportType='STEP')
    assert 'SI_UNIT(.MILLI.,.METRE.)' in path.read_text()
    return cq.importers.importStep(str(path)).val().BoundingBox().ylen


# --- 1. Units -------------------------------------------------------------

def test_to_mm_scales_workplane_and_assembly_to_millimetres(tmp_path):
    spar_mm = cad_aero.to_mm(_spar())
    assert spar_mm.val().BoundingBox().ylen == pytest.approx(5360.0, rel=1e-6)
    assert spar_mm.val().Volume() == pytest.approx(_spar().val().Volume() * 1e9, rel=1e-6)

    metre_asm = cq.Assembly(name='wing')
    metre_asm.add(_spar(), name='front_spar')
    metre_asm.add(_spar(), name='moved', loc=cq.Location(cq.Vector(0.0, 1.0, 0.0)))
    mm_asm = cad_aero.to_mm(metre_asm)
    assert isinstance(mm_asm, cq.Assembly)
    assert {c.name for c in mm_asm.children} == {'front_spar', 'moved'}
    # The located child moves 1 m -> 1000 mm, so the assembly spans 6360 mm.
    assert _step_span_mm(tmp_path, mm_asm) == pytest.approx(6360.0, rel=1e-6)


def test_strategy_example_exports_the_intended_millimetre_span(tmp_path):
    prompt = strategies.get_strategy('D', 'NACA 2412 wing box with spars and ribs')
    example = prompt.split('Minimal example: ', 1)[1].split('result = asm', 1)[0] + 'result = asm'
    namespace = {}
    exec('\n'.join(line.strip() for line in example.split('; ')), namespace)
    assert agents.unconverted_metre_builders(example) is None
    assert _step_span_mm(tmp_path, namespace['result']) == pytest.approx(5360.0, rel=1e-3)


def test_canonical_wing_parts_can_be_built_in_millimetres():
    metres = cad_aero.canonical_wing_parts()
    millimetres = cad_aero.canonical_wing_parts(units='mm')
    assert set(metres) == set(millimetres)
    span_m = metres['front_spar'].val().BoundingBox().ylen
    assert millimetres['front_spar'].val().BoundingBox().ylen == pytest.approx(span_m * 1000, rel=1e-6)
    with pytest.raises(cad_aero.CadAeroError):
        cad_aero.canonical_wing_parts(units='inch')


@pytest.mark.parametrize('script, ok', [
    ("from cad_aero import naca4_points, make_spar\n"
     "result = make_spar(airfoil_points=naca4_points('2412', 1.0), span_m=1.0, root_chord_m=0.3,"
     " tip_chord_m=0.2, x_frac=0.25, web_thickness_m=0.002)\n", False),
    ("from cad_aero import naca4_points, make_spar, to_mm\n"
     "result = to_mm(make_spar(airfoil_points=naca4_points('2412', 1.0), span_m=1.0,"
     " root_chord_m=0.3, tip_chord_m=0.2, x_frac=0.25, web_thickness_m=0.002))\n", True),
    ("import cad_aero\nfs = cad_aero.make_rib(airfoil_points=cad_aero.naca4_points('0012', 1.0),"
     " chord_m=0.3, y_station_m=0.0, thickness_m=0.003, front_spar_x_frac=0.25,"
     " rear_spar_x_frac=0.65)\nresult = cad_aero.to_mm(fs)\n", True),
    ("import cadquery as cq\nfrom cad_aero import canonical_wing_parts\n"
     "parts = canonical_wing_parts()\n", False),
    ("from cad_aero import canonical_wing_parts\nparts = canonical_wing_parts(units='mm')\n", True),
    ("from cad_aero import naca4_points\nimport cadquery as cq\n"
     "pts = naca4_points('2412', 200.0)\nresult = cq.Workplane('XZ').polyline(pts).close().extrude(5)\n", True),
])
def test_critic_unit_check_flags_builder_output_left_in_metres(script, ok):
    message = agents.unconverted_metre_builders(script)
    assert (message is None) is ok, message
    if not ok:
        assert 'to_mm' in message


def test_critic_loop_sends_metre_builder_scripts_back_without_executing(monkeypatch):
    unconverted = ("from cad_aero import naca4_points, make_spar\n"
                   "result = make_spar(airfoil_points=naca4_points('2412', 1.0), span_m=1.0,"
                   " root_chord_m=0.3, tip_chord_m=0.2, x_frac=0.25, web_thickness_m=0.002)\n")
    converted = unconverted.replace('make_spar\n', 'make_spar, to_mm\n').replace(
        'result = make_spar(', 'result = to_mm(make_spar(').rstrip('\n') + ')\n'
    machinist = AsyncMock(side_effect=[unconverted, converted])
    executed = []
    real_execute = agents.execute_cad_script

    async def recording_execute(code):
        executed.append(code)
        return await real_execute(code)

    monkeypatch.setattr(agents, 'run_machinist', machinist)
    monkeypatch.setattr(agents, 'execute_cad_script', recording_execute)
    monkeypatch.setattr(agents, 'classify_part', AsyncMock(return_value={'domain': 'D'}))
    monkeypatch.setattr(agents, '_score_and_log', AsyncMock(), raising=False)
    part = schemas.PartDefinition(part_id='spar', description='front spar', anchor_tags=['>Z'])
    code, _, _ = asyncio.run(agents.run_critic_loop(part, save_step=False))
    assert code == converted
    assert executed == [converted]
    assert 'to_mm' in machinist.await_args_list[1].kwargs['error_context']


# --- 2. Frame -------------------------------------------------------------

def _surface_z(wire, x):
    points = [wire.positionAt(i / 800) for i in range(801)]
    return sorted(p.z for p in points if abs(p.x - x) < 0.6)


def test_rule2_section_shares_the_cad_aero_frame():
    chord = 200.0
    wire = cad_utils.make_naca_wire_xz('2412', chord, y=50.0)
    points = [wire.positionAt(i / 800) for i in range(801)]
    # Sampled curve points: OCC's default bounding box is not tight on B-splines.
    assert min(p.x for p in points) == pytest.approx(0.0, abs=0.05)
    assert max(p.x for p in points) == pytest.approx(chord, abs=0.05)
    assert all(p.y == pytest.approx(50.0, abs=1e-9) for p in points)
    lower, upper = _surface_z(wire, 0.3 * chord)[0], _surface_z(wire, 0.3 * chord)[-1]
    ref = [z * chord for x, z in cad_aero.naca4_points('2412', 1.0) if abs(x - 0.3) < 0.02]
    assert upper == pytest.approx(max(ref), abs=0.3) and upper > 0
    assert lower == pytest.approx(min(ref), abs=0.3) and lower < 0


def test_rule2_prompts_use_the_shared_frame():
    prompt = strategies.get_strategy('D', 'NACA 2412 wing with ribs and spars')
    assert '(1,0,0), -90' not in prompt
    assert 'make_naca_wire_xz' in prompt
    assert '-root_chord / 2.0' not in prompt
    assert "'>Z' / '<Z' — suction (upper) / pressure (lower)" in prompt


def test_rule2_worked_example_geometry_spans_wing():
    root_c, tip_c, span = 200.0, 120.0, 500.0
    root = cad_utils.make_naca_wire_xz('2412', root_c)
    tip = cad_utils.make_naca_wire_xz('2412', tip_c, y=span)
    oml = cq.Workplane('XZ').newObject([cq.Solid.makeLoft([tip, root])])
    assert oml.val().isValid() and oml.val().Volume() > 0
    spar = (cq.Workplane('YZ').workplane(offset=root_c * 0.25).center(span / 2.0, 0)
            .rect(span * 1.1, root_c * 0.5).extrude(2.0)).intersect(oml)
    assert spar.val().BoundingBox().ylen == pytest.approx(span, rel=1e-3)
    rib = (cq.Workplane('XZ').center(root_c / 2.0, 0).rect(root_c * 1.2, root_c)
           .extrude(2.0).translate((0, 250.0 + 1.0, 0))).intersect(oml)
    assert rib.val().BoundingBox().xlen == pytest.approx(160.0, rel=1e-2)


# --- 3. Schema docstring ---------------------------------------------------

def test_schema_docstring_matches_translation_field():
    doc = schemas.__doc__
    assert 'FORBIDDEN' not in doc
    assert 'translation' in doc and 'deprecated' in doc.lower()
    assert 'DEPRECATED' in schemas.MatingRule.model_fields['translation'].description


def _le_and_te(wire):
    points = [wire.positionAt(i / 800) for i in range(801)]
    return min(points, key=lambda p: p.x), max(points, key=lambda p: p.x)


def test_planform_wires_share_the_frame_and_sweep_the_true_leading_edge():
    import math
    root, tip = cad_utils.make_planform_wires('2412', 200.0, 80.0, 500.0, sweep_le_deg=30.0)
    root_le, _ = _le_and_te(root)
    tip_le, _ = _le_and_te(tip)
    assert root_le.x == pytest.approx(0.0, abs=0.5)
    assert tip_le.x == pytest.approx(500.0 * math.tan(math.radians(30.0)), abs=0.5)
    assert tip_le.y == pytest.approx(500.0, abs=1e-6)
    assert _surface_z(root, 60.0)[-1] > 0 > _surface_z(root, 60.0)[0]
    assert abs(_surface_z(root, 60.0)[-1]) > abs(_surface_z(root, 60.0)[0])  # 2412 upper is thicker


def test_planform_positive_twist_is_nose_down_and_dihedral_raises_tip():
    _, flat = cad_utils.make_planform_wires('0012', 200.0, 100.0, 400.0)
    _, washed = cad_utils.make_planform_wires('0012', 200.0, 100.0, 400.0, twist_tip_deg=5.0)
    _, raised = cad_utils.make_planform_wires('0012', 200.0, 100.0, 400.0, dihedral_deg=5.0)
    le, te = _le_and_te(washed)
    assert te.z > le.z + 5.0  # trailing edge up = nose down
    assert le.x == pytest.approx(_le_and_te(flat)[0].x, abs=0.5)  # twist pivots on the LE
    assert raised.BoundingBox().zmin > flat.BoundingBox().zmin + 30.0
