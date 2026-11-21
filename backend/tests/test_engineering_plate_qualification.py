"""Q1 numerical qualification of the rectangular plate across the signed envelope."""

import json
from functools import lru_cache
from itertools import product

import pytest

from engineering.plate_fea import solve_levels
from engineering.plate_spec import EDGE_CONDITIONS, PLATE_NUMERICAL_V1 as POLICY
from engineering.plate_verify import _default_reference, _finite, _relative_change


B, H, Q, E = 1000.0, 10.0, 0.01, 200000.0
RATIOS = (
    POLICY["aspect_ratio_min"], 1.05, 1.1, 1.2, 1.3, 1.5, 1.75, 2.0, 2.5, 3.0, 4.0,
    POLICY["aspect_ratio_max"],
)
NUS = (POLICY["poissons_ratio_min"], POLICY["r3_nu"], POLICY["poissons_ratio_max"])
POINTS = tuple(product(RATIOS, EDGE_CONDITIONS, NUS))
CHECKS = (
    "mesh_convergence_deflection", "mesh_convergence_stress", "equilibrium",
    "benchmark_deflection", "benchmark_stress",
)


@lru_cache(maxsize=None)
def _measure(ratio, edge, nu):
    a = ratio * B
    levels = solve_levels(length_a_mm=a, width_b_mm=B, thickness_mm=H, pressure_mpa=Q,
                          youngs_modulus_mpa=E, poissons_ratio=nu, edge_condition=edge)
    mesh = tuple(level["n_b"] for level in levels)
    if mesh != POLICY["mesh_levels_n_b"]:
        raise ValueError(f"Unexpected FE mesh levels {mesh}")
    for level in levels:
        for key in ("w_max", "vm_max_mpa", "equilibrium_relative_error"):
            _finite(key, level[key])
    previous, finest = levels[-2:]
    w, vm = finest["w_max"], finest["vm_max_mpa"]
    ref = _default_reference(a, B, H, Q, E, nu, edge)
    return {
        "mesh_convergence_deflection": _relative_change(w, previous["w_max"]),
        "mesh_convergence_stress": _relative_change(vm, previous["vm_max_mpa"]),
        "equilibrium": max(level["equilibrium_relative_error"] for level in levels),
        "benchmark_deflection": abs(w / _finite("reference w_max", ref["w_max"]) - 1),
        "benchmark_stress": abs(vm / _finite("reference vm_max", ref["vm_max"]) - 1),
    }


def _summary(check, points, measured):
    limit = POLICY[check]
    available = [point for point in points if point in measured]
    worst_point = max(available, key=lambda p: measured[p][check], default=None)
    worst = measured[worst_point][check] if worst_point is not None else None
    return {
        "limit": limit, "worst": worst, "worst_margin": 1 - worst / limit if worst is not None else None,
        "worst_point": worst_point, "passed": sum(measured[p][check] <= limit for p in available),
    }


def _row(label, stats, total):
    worst = stats["worst"]
    margin = stats["worst_margin"]
    values = f"{worst:.8g} {margin:.3%}" if worst is not None else "n/a n/a"
    return f"{label} {stats['limit']:g} {values} {stats['worst_point']} {stats['passed']}/{total}"


@pytest.mark.parametrize("points", [pytest.param(POINTS, id="Q1")])
def test_plate_qualification(points, capsys):
    assert len(set(points)) == len(points) == 72
    measured = {}
    failures = []
    for point in points:
        try:
            values = _measure(*point)
            for check, value in values.items():
                _finite(check, value)
        except Exception as exc:
            # A missing solve must fail qualification and leave the other points observable.
            failures.append(f"{point}: {type(exc).__name__}: {exc}")
            continue
        measured[point] = values
        for check, value in values.items():
            if value > POLICY[check]:
                failures.append(f"{point}: {check}={value!r} exceeds {POLICY[check]!r}")

    checks = {check: _summary(check, points, measured) for check in CHECKS}
    report = {"points": len(points), "checks": checks, "all_passed": not failures}
    with capsys.disabled():
        print("\nE03-Q1-TABLE-BEGIN")
        print("check limit worst worst_margin (a/b, edge, nu) passed/points")
        for check, stats in checks.items():
            print(_row(check, stats, len(points)))
        for edge in EDGE_CONDITIONS:
            subset = [point for point in points if point[1] == edge]
            check = "mesh_convergence_stress"
            print(_row(f"{check}[{edge}]", _summary(check, subset, measured), len(subset)))
        print("E03-Q1-JSON " + json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False))
        print("E03-Q1-TABLE-END")

    assert not failures, "\n".join(["Q1 qualification failures (a/b, edge, nu)", *failures])
