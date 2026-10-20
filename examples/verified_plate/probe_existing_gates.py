"""Exercise actual published and dirty-local geometry certificate against native STEP."""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import cadquery as cq
from OCP.STEPControl import STEPControl_Reader
from OCP.BRep import BRep_Builder
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.BRepBndLib import BRepBndLib
from OCP.Bnd import Bnd_Box
from OCP.BRepCheck import BRepCheck_Analyzer
from OCP.TopExp import TopExp_Explorer
from OCP.TopAbs import TopAbs_FACE, TopAbs_SOLID
from OCP.IFSelect import IFSelect_RetDone

# Adapter isolates old checker semantics from its missing pythonocc binding.
# Native OCP operations, not a fake response. Keep unmodified result separate.
def adapter():
    return dict(STEPControl_Reader=STEPControl_Reader, BRep_Builder=BRep_Builder,
                brepgprop_VolumeProperties=BRepGProp.VolumeProperties_s,
                brepgprop_SurfaceProperties=BRepGProp.SurfaceProperties_s,
                GProp_GProps=GProp_GProps, brepbndlib_Add=BRepBndLib.Add_s,
                Bnd_Box=Bnd_Box, BRepCheck_Analyzer=BRepCheck_Analyzer,
                TopExp_Explorer=TopExp_Explorer, TopAbs_FACE=TopAbs_FACE,
                TopAbs_SOLID=TopAbs_SOLID, IFSelect_RetDone=IFSelect_RetDone)

box = cq.Workplane('XY').box(10,20,30).val()
face = cq.Face.makePlane(5,5, cq.Vector(40,0,0))
overlap = cq.Compound.makeCompound([box,box.translate((5,0,0))])
fixtures = {'box':box, 'solid_plus_loose_face':cq.Compound.makeCompound([box,face]),
            'overlapping_solids':overlap}
result={}
for label, root in [('published','/audit'),('current_local','/local')]:
    spec=importlib.util.spec_from_file_location(label,root+'/backend/verification/geometry.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    result[label]={}
    for name, shape in fixtures.items():
        path='/tmp/'+name+'.step'; cq.exporters.export(shape,path)
        cert=module.certify_geometry(path)
        result[label][name]=cert
    if label=='published':
        module._import_occ=adapter
        result[label]['ocp_adapter_semantics_only']={}
        for name in fixtures:
            result[label]['ocp_adapter_semantics_only'][name]=module.certify_geometry('/tmp/'+name+'.step')
    else:
        result[label]['box_with_false_nozzle_spec']=module.certify_geometry('/tmp/box.step',
            SimpleNamespace(throat_diameter_mm=40,exit_diameter_mm=130))
print(json.dumps(result,indent=2))
