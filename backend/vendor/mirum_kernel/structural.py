"""Linear-static analysis of a homogeneous rectangular Euler–Bernoulli beam."""

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import version
import json
import math
from pathlib import Path
from platform import python_version

SCHEMA = "rectangular_beam_analysis_v1"
MODEL = "named_regions_end_force_linear_elastic"
SOLVER_METHOD = "euler_bernoulli_beam_fem_v2"
MESH_LEVELS = (4, 8, 16)
TIP_ERROR_LIMIT = 0.02
MESH_CHANGE_LIMIT = 0.01
REACTION_ERROR_LIMIT = 0.01

ROOT = "beam.root_section"
INBOARD = "beam.inboard_section"
TIP = "beam.free_end"


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


class BoundaryError(ValueError):
    def __init__(self, role, reference, candidates):
        code = "boundary_ambiguous" if candidates else "boundary_missing"
        self.diagnostic = dict(code=code, role=role, reference=reference, candidates=candidates)
        super().__init__(f"{code}: {role} reference {reference!r}; candidates: {', '.join(candidates) or 'none'}")


def resolve_region(role, reference):
    allowed = (ROOT, INBOARD) if role == "support" else (TIP,)
    if reference in allowed:
        return reference
    # The deliberately underspecified alias exercises fail-closed reference resolution.
    candidates = (ROOT, TIP) if reference == "beam.ends" else ()
    raise BoundaryError(role, reference, candidates)


@dataclass(frozen=True)
class Case:
    """Dimensions in metres, modulus in pascals and force in newtons."""

    span_m: float
    width_m: float
    depth_m: float
    youngs_modulus_pa: float
    force_n: float
    load_region: str
    support_region: str
    units: str

    def __post_init__(self):
        values = (self.span_m, self.width_m, self.depth_m, self.youngs_modulus_pa, self.force_n)
        if self.units != "SI" or any(type(v) not in (int, float) or not math.isfinite(v) or v <= 0
                                    for v in values):
            raise ValueError("Expected positive finite case inputs in SI")


@lru_cache(maxsize=1)
def _implementation_sha256():
    paths = (Path(__file__), Path(__file__).with_name("beam_fea.py"))
    return digest([sha256(path.read_bytes()).hexdigest() for path in paths])


def _section(name, fraction, normal, case):
    return dict(name=name, span_fraction=fraction, normal=normal,
                centroid_m=[case.span_m * fraction, case.width_m / 2, case.depth_m / 2],
                area_m2=case.width_m * case.depth_m)


def prepare(case):
    """Resolve names and freeze analysis dependencies before performing any solve."""
    support = resolve_region("support", case.support_region)
    tip = resolve_region("load", case.load_region)
    fraction = 0.0 if support == ROOT else 0.25
    if (case.span_m * (1 - fraction)) < 10 * case.depth_m:
        raise ValueError("Free span must be at least ten times the bending depth")
    meshes = []
    for elements in MESH_LEVELS:
        coordinates = [case.span_m * i / elements for i in range(elements + 1)]
        clamp = int(elements * fraction)
        mesh = dict(element_type="two_node_cubic_hermite_beam",
                    elements=elements, nodes=elements + 1, spacing_m=case.span_m / elements,
                    clamp_node=clamp, tip_node=elements)
        meshes.append(dict(mesh, sha256=digest(dict(mesh, x_m=coordinates,
                                                    connectivity=[[i, i + 1] for i in range(elements)]))))
    geometry = dict(kind="rectangular_prism", frame="right_handed_positive_xyz_lower_corner",
                    dimensions_m=[float(case.span_m), float(case.width_m), float(case.depth_m)])
    return dict(
        schema=SCHEMA, units=dict(length="m", youngs_modulus="Pa", force="N", displacement="mm",
                                  solver="mm_N_MPa"),
        geometry=geometry, geometry_revision_sha256=digest(geometry),
        material=dict(model="homogeneous_linear_elastic", youngs_modulus_pa=float(case.youngs_modulus_pa),
                      poissons_ratio="unused_by_euler_bernoulli"),
        support=dict(_section(support, fraction, [-1, 0, 0] if fraction == 0 else [1, 0, 0], case),
                     kind="fixed_section", constraints=["uz", "rotation_y"],
                     witness="end_face" if fraction == 0 else "derived_inboard_cross_section"),
        load=dict(_section(tip, 1.0, [1, 0, 0], case), total_n=float(case.force_n), direction="+Z",
                  distribution="centroidal_nodal_resultant_without_applied_moment"),
        measurement=dict(region=tip, location="section_centroid_node", component="uz"),
        limits=dict(finest_relative_error=TIP_ERROR_LIMIT, finest_pair_relative_change=MESH_CHANGE_LIMIT,
                    reaction_relative_error=REACTION_ERROR_LIMIT),
        solver=dict(method=SOLVER_METHOD, implementation_sha256=_implementation_sha256(),
                    numpy=version("numpy"), python=python_version()),
        meshes=meshes,
    )


def _summarize(inputs, samples):
    span, width, depth = inputs["geometry"]["dimensions_m"]
    free_span = span * (1 - inputs["support"]["span_fraction"])
    force = inputs["load"]["total_n"]
    young = inputs["material"]["youngs_modulus_pa"]
    inertia = width * depth**3 / 12
    reference = 1000 * force * free_span**3 / (3 * young * inertia)
    errors = [abs(sample["tip_displacement_mm"] / reference - 1) for sample in samples]
    reactions = [abs(sample["root_reaction_force_n"] + force) / force for sample in samples]
    finest = samples[-1]["tip_displacement_mm"]
    change = abs(samples[-2]["tip_displacement_mm"] - finest) / (abs(finest) or reference)
    limits = inputs["limits"]
    passed = (errors[-1] <= limits["finest_relative_error"] and change < limits["finest_pair_relative_change"]
              and max(reactions) <= limits["reaction_relative_error"])
    return dict(state="passed" if passed else "failed", analytical_tip_displacement_mm=reference,
                finest_pair_relative_change=change, displacement_relative_errors=errors,
                reaction_relative_errors=reactions)


def analyze(inputs):
    from .beam_fea import solve_cantilever

    span, width, depth = inputs["geometry"]["dimensions_m"]
    samples = [solve_cantilever(
        length_mm=1000 * span, width_mm=1000 * width, height_mm=1000 * depth,
        youngs_modulus_mpa=1e-6 * inputs["material"]["youngs_modulus_pa"],
        tip_force_n=inputs["load"]["total_n"], elements=mesh["elements"], clamp_node=mesh["clamp_node"])
        for mesh in inputs["meshes"]]
    return dict(inputs=inputs, dependency_sha256=digest(inputs), samples=samples, **_summarize(inputs, samples))


def result_state(inputs, result):
    """Assess a captured result against the independently prepared current case."""
    if not isinstance(result, dict) or result.get("dependency_sha256") != digest(inputs):
        return "stale"
    if result.get("inputs") != inputs:
        return "stale"
    try:
        samples = result["samples"]
        if [s["elements"] for s in samples] != [m["elements"] for m in inputs["meshes"]]:
            return "failed"
        if any(type(value) not in (int, float) or not math.isfinite(value)
               for sample in samples for value in sample.values()):
            return "failed"
        summary = _summarize(inputs, samples)
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return "failed"
    if any(result.get(name) != value for name, value in summary.items()):
        return "failed"
    return summary["state"]


def resolve_result(inputs, previous=None):
    """Reuse a passing result only while every analysis dependency still matches."""
    if result_state(inputs, previous) == "passed":
        return previous, "reused"
    return analyze(inputs), "computed"
