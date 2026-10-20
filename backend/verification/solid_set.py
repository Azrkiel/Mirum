"""Native STEP structural inspection, separate from the legacy certificate.

Called inside the application's bounded ProcessRunner worker; no process is
spawned here. This is kernel/numeric evidence, not engineering qualification.
"""
import math
import os

SCOPE = "structural_solid_set_v1"
MAX_STEP_BYTES = 500 * 1024 * 1024
# Covers AssemblyManifest's 50-part limit; pairwise checks are broad-phase pruned.
MAX_SOLIDS = 64
MAX_TOPOLOGY_ELEMENTS = 100000
# Application numerical policy, not a manufacturing tolerance.
MAX_TOPOLOGY_TOLERANCE_MM = 0.001


def inspect_step(step_path: str) -> dict:
    """Inspect the imported solid set using deployment OCP bindings only."""
    result = {
        "passed": False, "checks": {}, "failures": [],
        "volume_mm3": None, "surface_area_mm2": None,
        "bounding_box": {"x_mm": None, "y_mm": None, "z_mm": None},
        "solid_count": 0, "max_topology_tolerance_mm": None, "scope": SCOPE,
    }

    def require(name, condition, issue):
        result["checks"][name] = bool(condition)
        if not condition:
            result["failures"].append(issue)
            raise _Rejected

    try:
        try:
            from OCP.STEPControl import STEPControl_Reader
            from OCP.IFSelect import IFSelect_RetDone
            from OCP.TopAbs import (TopAbs_COMPOUND, TopAbs_COMPSOLID, TopAbs_SOLID,
                                    TopAbs_SHELL, TopAbs_FACE, TopAbs_WIRE,
                                    TopAbs_EDGE, TopAbs_VERTEX)
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopoDS import TopoDS, TopoDS_Iterator
            from OCP.BRep import BRep_Tool
            from OCP.BRepGProp import BRepGProp
            from OCP.GProp import GProp_GProps
            from OCP.BRepBndLib import BRepBndLib
            from OCP.Bnd import Bnd_Box
            from OCP.BRepAlgoAPI import BRepAlgoAPI_Common
            from OCP.BRepCheck import BRepCheck_Analyzer
        except ImportError:
            require("native_backend", False, "Native OCP backend is unavailable.")
        require("native_backend", True, "Native OCP backend is unavailable.")
        require("step_file", os.path.isfile(step_path), "STEP file is unavailable.")
        require("step_size_limit", os.path.getsize(step_path) <= MAX_STEP_BYTES,
                "STEP exceeds the 500 MiB inspection limit.")
        reader = STEPControl_Reader()
        require("step_read", reader.ReadFile(step_path) == IFSelect_RetDone,
                "STEP read failed.")
        roots = reader.NbRootsForTransfer()
        require("step_transfer", roots > 0 and reader.TransferRoots() == roots,
                "STEP root transfer was incomplete or failed.")
        shape = reader.OneShape()
        require("non_null_shape", not shape.IsNull(), "STEP has no shape.")
        solids = []
        # Direct-child traversal accounts for *all* topology, not just solids
        # discovered by an explorer. Count occurrences, including shared nodes.
        allowed = {
            None: (TopAbs_COMPOUND, TopAbs_COMPSOLID, TopAbs_SOLID),
            TopAbs_COMPOUND: (TopAbs_COMPOUND, TopAbs_COMPSOLID, TopAbs_SOLID),
            TopAbs_COMPSOLID: (TopAbs_SOLID,),
            TopAbs_SOLID: (TopAbs_SHELL,), TopAbs_SHELL: (TopAbs_FACE,),
            TopAbs_FACE: (TopAbs_WIRE,), TopAbs_WIRE: (TopAbs_EDGE,),
            TopAbs_EDGE: (TopAbs_VERTEX,), TopAbs_VERTEX: (),
        }
        stack, nodes = [(shape, None)], 1
        while stack:
            node, parent = stack.pop()
            require("complete_solid_accounting", not node.IsNull()
                    and node.ShapeType() in allowed[parent],
                    "STEP contains topology outside complete solids.")
            kind = node.ShapeType()
            if kind == TopAbs_SOLID:
                solids.append(TopoDS.Solid_s(node))
                result["solid_count"] = len(solids)
                require("solid_limit", len(solids) <= MAX_SOLIDS,
                        f"STEP exceeds the {MAX_SOLIDS} solid inspection limit.")
            if kind == TopAbs_SHELL:
                require("closed_shells", BRep_Tool.IsClosed_s(node),
                        "Solid contains an open shell.")
            children = TopoDS_Iterator(node)
            count = 0
            while children.More():
                nodes += 1
                require("topology_limit", nodes <= MAX_TOPOLOGY_ELEMENTS,
                        "STEP exceeds the 100000 topology-element inspection limit.")
                stack.append((children.Value(), kind))
                count += 1
                children.Next()
            require("complete_solid_accounting", count > 0 or kind in (TopAbs_FACE, TopAbs_EDGE, TopAbs_VERTEX),
                    "STEP contains empty topology wrappers.")
        result["solid_count"] = len(solids)
        require("solid_presence", bool(solids), "STEP contains no solids.")
        volumes, areas = [], []
        for solid in solids:
            require("brep_valid_geometry", BRepCheck_Analyzer(solid, True).IsValid(),
                    "Solid failed native BRep geometry validation.")
            volume, area = GProp_GProps(), GProp_GProps()
            BRepGProp.VolumeProperties_s(solid, volume, True, False, False)
            BRepGProp.SurfaceProperties_s(solid, area, False, False)
            volumes.append(volume.Mass())
            areas.append(area.Mass())
        require("positive_finite_metrics", all(math.isfinite(v) and v > 0
                for v in volumes + areas), "Solid metrics are not finite and positive.")
        result["volume_mm3"] = math.fsum(volumes)
        result["surface_area_mm2"] = math.fsum(areas)
        def native_bounds(item):
            bounds = Bnd_Box()
            BRepBndLib.AddOptimal_s(item, bounds, False, False)
            require("finite_optimal_bounds", not bounds.IsVoid() and not bounds.IsOpen(),
                    "Shape bounds are not finite and positive.")
            coords = bounds.Get()
            dims = [coords[i + 3] - coords[i] for i in range(3)]
            require("finite_optimal_bounds", all(math.isfinite(v)
                    for v in coords + tuple(dims)) and all(v > 0 for v in dims),
                    "Shape bounds are not finite and positive.")
            return coords

        per_solid_bounds = [native_bounds(solid) for solid in solids]
        coords = native_bounds(shape)
        dims = [coords[i + 3] - coords[i] for i in range(3)]
        result["bounding_box"] = dict(zip(("x_mm", "y_mm", "z_mm"), dims))
        tolerances = []
        for kind, cast in ((TopAbs_FACE, TopoDS.Face_s), (TopAbs_EDGE, TopoDS.Edge_s),
                           (TopAbs_VERTEX, TopoDS.Vertex_s)):
            explorer = TopExp_Explorer(shape, kind)
            while explorer.More():
                tolerances.append(BRep_Tool.Tolerance_s(cast(explorer.Current())))
                explorer.Next()
        require("topology_tolerance_policy", bool(tolerances)
                and all(math.isfinite(t) and t > 0 for t in tolerances),
                "Topology tolerances must be finite and strictly positive.")
        result["max_topology_tolerance_mm"] = max(tolerances)
        require("topology_tolerance_policy", max(tolerances) <= MAX_TOPOLOGY_TOLERANCE_MM,
                "Topology tolerance exceeds the 0.001 mm application numeric policy.")
        for i, left in enumerate(solids):
            a = per_solid_bounds[i]
            for j in range(i + 1, len(solids)):
                b = per_solid_bounds[j]
                # Conservative broad phase only; no inflated imported tolerance
                # relaxes the Boolean acceptance policy.
                if any(a[k + 3] + 0.002 < b[k] or b[k + 3] + 0.002 < a[k]
                       for k in range(3)):
                    continue
                common = BRepAlgoAPI_Common(left, solids[j])
                common.Build()
                require("native_common", common.IsDone(),
                        "Native solid intersection operation failed.")
                intersection = common.Shape()
                require("native_common", not intersection.IsNull(),
                        "Native solid intersection operation failed.")
                props = GProp_GProps()
                BRepGProp.VolumeProperties_s(intersection, props, True, False, False)
                overlap = props.Mass()
                require("native_common", math.isfinite(overlap) and overlap >= 0,
                        "Native solid intersection metrics are invalid.")
                require("no_positive_volume_interpenetration", overlap == 0,
                        "Solids have positive-volume interpenetration.")
        result["checks"]["no_positive_volume_interpenetration"] = True
        result["passed"] = True
    except _Rejected:
        pass
    except Exception:
        result["checks"]["native_inspection"] = False
        result["failures"].append("Native STEP inspection failed.")
    return result


class _Rejected(Exception):
    """Internal static-issue control flow; never expose native exception text."""
