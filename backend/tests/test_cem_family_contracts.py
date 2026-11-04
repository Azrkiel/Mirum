"""C/A contract and C/B load handoff fixtures; never native qualification."""
import numpy as np
import pytest

from cem.core import canonical_json, digest
from cem.aero.contracts import (BodyStation, InstalledGeometry, LiftingSurfaceLoads,
    OperatingPoint, StructuralLoadCase, StripLoad)
from cem.aero.fixtures import structural_fixture
from cem.aero.lifting import solve
from cem.aero.fixtures import analytic_bank
from cem.glider.families import build
from cem.glider.families.contracts import (FamilyAdapter, Hardware, structural_case,
    family_reactions, validate_family_reactions)


def installed(family='canard', **context):
    a = build(family)
    h = Hardware(body=(BodyStation(x_m=0.,width_m=.02,height_m=.02,exponent=2.),
        BodyStation(x_m=.7,width_m=.02,height_m=.02,exponent=2.)),
        control_deflections_deg={c.name: 0. for c in a.controls})
    ctx = dict(mission={'launch': {'method': 'drop', 'height_m': 10.}},
               process={'name': 'balsa', 'construction': 'screening_oml'},
               calibration={'version': 'development-fixture'})
    ctx.update(context)
    return a, h, FamilyAdapter(family).prepare(a.parameters,h,**ctx)


def loads_for(g):
    """Explicit analytic force fixture on every installed half-wing."""
    weight = g.reference.mass_kg*9.80665
    op = OperatingPoint(velocity_m_s=6.,alpha_deg=3.,density_kg_m3=1.225,viscosity_pa_s=1.8e-5)
    strips = []
    for i,surface in enumerate(g.surfaces):
        fraction = (.2,.8)[i] if g.family == 'canard' else .5
        for sign in (-1.,1.):
            sec = surface.sections[1]
            center = (sec.leading_edge_m[0]+sec.chord_m*.25,
                      sec.leading_edge_m[1]*sign,sec.leading_edge_m[2])
            fz = weight*fraction/2
            moment = ((center[1]-g.reference.cg_m[1])*fz,
                      -(center[0]-g.reference.cg_m[0])*fz,0.)
            strips.append(StripLoad(surface=surface.name,center_m=center,area_m2=.02,
                chord_m=sec.chord_m,velocity_m_s=(6.,0.,0.),induced_velocity_m_s=(0.,0.,0.),
                effective_alpha_deg=3.,reynolds=6*1.225*sec.chord_m/1.8e-5,
                force_n=(0.,0.,fz),moment_cg_nm=moment))
    return LiftingSurfaceLoads(binding=g.binding,operating_point=op,strips=tuple(strips),
        force_n=tuple(sum(s.force_n[i] for s in strips) for i in range(3)),
        moment_cg_nm=tuple(sum(s.moment_cg_nm[i] for s in strips) for i in range(3)),
        induced_drag_n=0.,profile_drag_n=0.,circulation_residual=0.,force_closure_n=0.,moment_closure_nm=0.)


@pytest.mark.parametrize('family,roles',[('canard',['canard','wing']),('tandem',['forewing','aftwing'])])
def test_shared_geometry_roundtrip_roles_inventory_and_neutral_controls(family,roles):
    a,h,g = installed(family)
    restored = InstalledGeometry.model_validate_json(canonical_json(g))
    assert restored == g
    assert [s.role for s in g.surfaces] == roles
    assert g.reference.mass_kg == a.mass_summary()['mass_kg']
    assert g.reference.cg_m == tuple(a.mass_summary()['cg_m'])
    assert min(g.reference.inertia_kg_m2) > 0
    assert g.binding.geometry_sha256 == digest(g.geometry_payload())
    assert FamilyAdapter(family).installed_geometry(a.parameters,h,g.binding)==g
    assert all(s.mirror_y for s in g.surfaces)


def test_shared_binding_covers_hardware_mission_process_calibration():
    a,h,g = installed()
    for key in ('mission','process','calibration'):
        changed = installed(**{key:{'changed':True}})[2]
        assert getattr(changed.binding,key+'_sha256') != getattr(g.binding,key+'_sha256')
        assert changed.binding.geometry_sha256 == g.binding.geometry_sha256
    wrong = g.binding.model_copy(update={'hardware_sha256':'f'*64})
    with pytest.raises(ValueError,match='context'):
        FamilyAdapter('canard').installed_geometry(a.parameters,h,wrong)
    changed = dict(a.parameters)
    changed['main_x_m'] = .41
    with pytest.raises(ValueError,match='context'):
        FamilyAdapter('canard').installed_geometry(changed,h,g.binding)


def test_unrepresentable_reflex_and_control_states_are_rejected():
    with pytest.raises(ValueError,match='reflex'):
        installed('tailless_swept')
    a,h,g = installed()
    for states in ({},{**h.control_deflections_deg,'foreplane_elevator':1.}):
        altered = h.model_copy(update={'control_deflections_deg':states})
        with pytest.raises(ValueError,match='control'):
            FamilyAdapter('canard').installed_geometry(a.parameters,altered,g.binding)


def test_body_must_be_supplied_in_full_and_in_order():
    a,h,g = installed()
    shortened = h.model_copy(update={'body':(h.body[0],h.body[1].model_copy(update={'x_m':.6}))})
    with pytest.raises(ValueError,match='body length'):
        FamilyAdapter('canard').installed_geometry(a.parameters,shortened,g.binding)


@pytest.mark.parametrize('family',['canard','tandem'])
def test_shared_loadcase_binds_every_surface_and_preserves_unqualified_admission(family):
    _,_,g = installed(family)
    loads = loads_for(g)
    case = structural_case(g,loads,name='fixture-steady',load_factor=2.)
    assert StructuralLoadCase.model_validate_json(canonical_json(case)) == case
    assert case.scope == 'rigid-steady-lifting-surfaces-only'
    assert case.aerodynamic_loads_sha256 == digest(loads)
    failures = FamilyAdapter(family).admit(g,loads)
    assert any('qualification incomplete' in f for f in failures)
    assert any('stall' in f for f in failures)
    assert any('construction' in f for f in failures)
    with pytest.raises(ValueError,match='qualification'):
        solve(g,loads.operating_point,analytic_bank(g))


def test_stale_missing_or_mislabeled_shared_loads_cannot_cross_handoff():
    _,_,g = installed()
    loads = loads_for(g)
    for corrupt in (loads.model_copy(update={'binding':structural_fixture()[0].binding}),
                    loads.model_copy(update={'strips':tuple(s for s in loads.strips if s.surface!='foreplane')})):
        with pytest.raises(ValueError):
            structural_case(g,corrupt,name='bad',load_factor=1.)


@pytest.mark.parametrize('family',['canard','tandem'])
def test_b_handoff_uses_actual_member_roles_spans_and_preserves_total_vectors(family):
    from cem.glider.structural_adapter import section_resultant
    _,_,g = installed(family)
    loads = loads_for(g)
    # Exercise drag/lateral force and all section-couple axes, not only lift.
    strips = []
    for s in loads.strips:
        force = np.array([.01,.002,s.force_n[2]])
        moment = np.cross(np.array(s.center_m)-g.reference.cg_m, force)+[.003,.004,.005]
        strips.append(s.model_copy(update={'force_n':tuple(force),'moment_cg_nm':tuple(moment)}))
    loads = LiftingSurfaceLoads.model_validate_json(canonical_json(loads.model_copy(update={
        'strips':tuple(strips),'force_n':tuple(np.sum([s.force_n for s in strips],axis=0)),
        'moment_cg_nm':tuple(np.sum([s.moment_cg_nm for s in strips],axis=0))})))
    case = structural_case(g,loads,name='fixture-vectors',load_factor=3.)
    roots = {s.name:(s.sections[0].leading_edge_m[0],0.,s.sections[0].leading_edge_m[2]) for s in g.surfaces}
    result = family_reactions(g,case,root_axes_m=roots)
    assert validate_family_reactions(result,g,case,root_axes_m=roots) is False
    changed = dict(result, state='passed', physical_construction=True)
    with pytest.raises(ValueError,match='reconstructed'):
        validate_family_reactions(changed,g,case,root_axes_m=roots)
    assert result['state']=='incomplete' and result['physical_construction'] is False
    assert result['force_n']==pytest.approx(np.array(loads.force_n)*3.)
    assert result['moment_cg_nm']==pytest.approx(np.array(loads.moment_cg_nm)*3.)
    for s in g.surfaces:
        member = result['members'][s.name]
        assert member['half_span_m']==max(sec.leading_edge_m[1] for sec in s.sections)
        assert member['omitted_surfaces']==sorted(v.name for v in g.surfaces if v.name!=s.name)
        for side in member['sides'].values():
            for row in side['strips']:
                assert row['couple_nm']==pytest.approx([.009,.012,.015])
        assert section_resultant(member,'right',member['half_span_m'])['force_n']==[0.,0.,0.]
    with pytest.raises(ValueError,match='root'):
        family_reactions(g,case,root_axes_m={})
    bad = dict(roots)
    bad[next(iter(roots))]=(0.,.01,0.)
    with pytest.raises(ValueError,match='reference'):
        family_reactions(g,case,root_axes_m=bad)
