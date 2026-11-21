"""Numerical evidence for the E03 rectangular Kirchhoff plate contract."""

import json
import subprocess
import sys
from functools import lru_cache
from itertools import product
from pathlib import Path

import pytest

from engineering.plate_fea import solve_levels, solve_plate
from engineering.plate_reference import fd_plate, navier_ss, strip_limits
from engineering.plate_spec import PLATE_NUMERICAL_V1 as POLICY


FIXTURE = Path(__file__).parent / "fixtures/engineering/plate_benchmarks-v1.json"
BOOK = json.loads(FIXTURE.read_text(encoding="utf-8"))
SS, CL = "simply_supported", "clamped"
STRIP_RATIO = POLICY["strip_limit_aspect_ratio"]
RATIOS = (1.0, 1.2, 1.5, 2.0, 3.0, STRIP_RATIO)
MATRIX = tuple(product((SS, CL), RATIOS))
# Table 35 cells where the converged FD reference (R2) and the FE agree with each other but
# not with the printed (Evans 1939) value; asserted explicitly below, reported to the lead.
KNOWN_DISCREPANCIES = {(d["edge_condition"], d["ratio"], key): next(
    r for r in BOOK["rows"] if (r["edge_condition"], r["ratio"]) == (d["edge_condition"], d["ratio"])
)["coefficients"][key]["printed"] for d in BOOK["known_discrepancies"] for key in d["fields"]}
B, H, Q, E = 1000.0, 10.0, 0.01, 200000.0
NU = POLICY["r3_nu"]
D = E * H**3 / (12 * (1 - NU**2))
W_UNIT = Q * B**4 / D
M_UNIT = Q * B**2


def _inputs(edge, ratio):
    return dict(length_a_mm=ratio * B, width_b_mm=B, thickness_mm=H, pressure_mpa=Q,
                youngs_modulus_mpa=E, poissons_ratio=NU, edge_condition=edge)


@lru_cache(maxsize=None)
def _levels(edge, ratio):
    return solve_levels(**_inputs(edge, ratio))


@lru_cache(maxsize=None)
def _finest(edge, ratio):
    if (edge, ratio) in MATRIX:
        return _levels(edge, ratio)[-1]
    return solve_plate(**_inputs(edge, ratio), n_b=POLICY["mesh_levels_n_b"][-1])


@lru_cache(maxsize=None)
def _r1(ratio):
    return navier_ss(ratio * B, B, H, Q, E, NU)


@lru_cache(maxsize=None)
def _r2(edge, ratio):
    return fd_plate(ratio * B, B, H, Q, E, NU, edge)["extrapolated"]


@lru_cache(maxsize=None)
def _r4():
    return strip_limits(B, H, Q, E, NU)


def _rel(value, reference):
    return abs(value / reference - 1)


def _band(cell):
    decimals = len(cell["printed"].split(".")[1])
    return 0.5 * 10**(-decimals) + POLICY["r3_relative_tolerance"] * abs(cell["value"])


def test_fixture_integrity():
    assert BOOK["schema"] == "plate_benchmarks_v1"
    assert BOOK["nu"] == NU
    source_ids = {s["id"] for s in BOOK["sources"]}
    expected = {SS: {"1.0", "1.2", "1.5", "1.7", "2.0"},
                CL: {f"{n / 10:.1f}" for n in range(10, 21)} | {"inf"}}
    fields = {SS: {"w", "beta", "beta1"},
              CL: {"w", "edge_mx", "edge_my", "centre_mx", "centre_my"}}
    for edge in (SS, CL):
        rows = [row for row in BOOK["rows"] if row["edge_condition"] == edge]
        assert sorted(r["ratio"] for r in rows) == sorted(expected[edge])
        for row in rows:
            assert set(row["coefficients"]) == fields[edge]
    assert len(BOOK["rows"]) == sum(map(len, expected.values()))
    for row in BOOK["rows"] + BOOK["cross_checks"]:
        assert row["source_ids"] and set(row["source_ids"]) <= source_ids
        assert row["confidence"]
        assert row["ratio_value"] == (None if row["ratio"] == "inf" else float(row["ratio"]))
        for cell in row["coefficients"].values():
            assert isinstance(cell["printed"], str)
            assert float(cell["printed"]) == cell["value"]

    assert KNOWN_DISCREPANCIES == {(CL, "1.0", "centre_mx"): "0.0231", (CL, "1.0", "centre_my"): "0.0231",
                                   (CL, "1.1", "centre_mx"): "0.0264"}
    for (edge, ratio, key), printed in KNOWN_DISCREPANCIES.items():
        row = next(r for r in BOOK["rows"] if (r["edge_condition"], r["ratio"]) == (edge, ratio))
        assert row["coefficients"][key]["printed"] == printed


def test_roark_consistency():
    tables = {(r["edge_condition"], r["ratio"]): r for r in BOOK["rows"]}
    compared = {SS: set(), CL: set()}
    for row in BOOK["cross_checks"]:
        edge, ratio = row["edge_condition"], row["ratio"]
        if (edge, ratio) not in tables:
            continue  # Roark also prints ratios outside the signed Table 8 rows.
        compared[edge].add(ratio)
        reference = tables[edge, ratio]["coefficients"]
        mapping = {"alpha": "w", "beta": "beta"} if edge == SS else {
            "alpha": "w", "beta1": "edge_mx", "beta2": "centre_mx",
        }
        assert set(row["coefficients"]) == set(mapping)
        for key, target in mapping.items():
            roark = row["coefficients"][key]
            factor = 12 * (1 - NU**2) if key == "alpha" else 6
            difference = abs(roark["value"] / factor - abs(reference[target]["value"]))
            assert difference <= _band(roark) / factor, (edge, ratio, key)
    assert compared[SS] == {"1.0", "1.2", "2.0"}
    assert compared[CL] == {"1.0", "1.2", "1.4", "1.6", "1.8", "2.0", "inf"}


def _matrix_values(edge, ratio):
    levels = _levels(edge, ratio)
    prev, fe = levels[-2:]
    ref = _r1(ratio) if edge == SS else _r2(edge, ratio)
    w_ref = ref["w_max" if edge == SS else "w_centre"]
    return dict(w=fe["w_max"] / W_UNIT, w_ref=w_ref / W_UNIT,
                vm=fe["vm_max_mpa"], vm_ref=ref["vm_max"],
                dw=_rel(fe["w_max"], w_ref), dvm=_rel(fe["vm_max_mpa"], ref["vm_max"]),
                mesh_w=_rel(fe["w_max"], prev["w_max"]),
                mesh_vm=_rel(fe["vm_max_mpa"], prev["vm_max_mpa"]),
                equilibrium=max(abs(lvl["equilibrium_relative_error"]) for lvl in levels),
                region=fe["vm_max_location"]["region"])


@pytest.mark.parametrize("edge,ratio", MATRIX)
def test_benchmark_matrix(edge, ratio):
    levels = _levels(edge, ratio)
    assert tuple(lvl["n_b"] for lvl in levels) == POLICY["mesh_levels_n_b"]
    for lvl in levels:
        assert 0 <= lvl["equilibrium_relative_error"] <= POLICY["equilibrium"]
        assert _rel(lvl["reaction_sum_n"], Q * ratio * B**2) <= POLICY["equilibrium"]
    v = _matrix_values(edge, ratio)
    assert v["dw"] <= POLICY["benchmark_deflection"]
    assert v["dvm"] <= POLICY["benchmark_stress"]
    assert v["mesh_w"] <= POLICY["mesh_convergence_deflection"]
    assert v["mesh_vm"] <= POLICY["mesh_convergence_stress"]


@pytest.mark.parametrize("ratio", RATIOS)
def test_independent_ss_references(ratio):
    navier, fd = _r1(ratio), _r2(SS, ratio)
    assert _rel(fd["w_centre"], navier["w_max"]) <= 0.001
    assert _rel(fd["vm_max"], navier["vm_max"]) <= 0.01


# Timoshenko's x runs across the short side, our y. Keep the mapping independent
# of the values under test so a swapped pair of fixture labels fails the check.
FIELDS = {
    SS: {"w": "w_max", "beta": "my_centre", "beta1": "mx_centre"},
    CL: {"w": "w_max", "edge_mx": "edge_moment_long_edge",
         "edge_my": "edge_moment_short_edge", "centre_mx": "my_centre", "centre_my": "mx_centre"},
}


def _r3_values(row):
    edge = row["edge_condition"]
    if row["ratio"] == "inf":
        strip = _r4()[CL]
        # Cylindrical bending gives My=q b^2/24 and Mx=nu My at midspan.
        # The strip has no short end at which to predict the Table 35 end moment.
        return dict(w=strip["w"] / W_UNIT, edge_mx=-strip["edge_moment"] / M_UNIT,
                    centre_mx=1 / 24, centre_my=NU / 24)
    fe = _finest(edge, row["ratio_value"])
    return {key: fe[field] / (W_UNIT if key == "w" else M_UNIT)
            for key, field in FIELDS[edge].items()}


@pytest.mark.parametrize("row", BOOK["rows"], ids=lambda r: f"{r['edge_condition']}-{r['ratio']}")
def test_r3_printed_coefficients(row):
    edge = row["edge_condition"]
    for key, actual in _r3_values(row).items():
        cell = row["coefficients"][key]
        assert cell["our_label"] == FIELDS[edge][key]
        sign = -1 if key in {"edge_mx", "edge_my"} else 1
        assert sign * actual > 0 and sign * cell["value"] > 0, key
        error = abs(abs(actual) - abs(cell["value"]))
        if (edge, row["ratio"], key) in KNOWN_DISCREPANCIES:
            fd = _r2(edge, row["ratio_value"])[FIELDS[edge][key]] / M_UNIT
            assert _rel(actual, fd) <= POLICY["r3_relative_tolerance"], key
            assert error > _band(cell) and abs(fd - cell["value"]) > _band(cell), key
        else:
            assert error <= _band(cell), (key, actual, cell["printed"])


STRIP_CHECKS = ((SS, "w_max"), (CL, "w_max"), (CL, "edge_moment_long_edge"))


def _strip_values(edge, field):
    fe = _finest(edge, STRIP_RATIO)[field]
    if field == "w_max":
        return fe, _r4()[edge]["w"]
    return -fe, _r4()[edge]["edge_moment"]


@pytest.mark.parametrize("edge,field", STRIP_CHECKS)
def test_r4_strip_limit(edge, field):
    value, limit = _strip_values(edge, field)
    assert _rel(value, limit) <= POLICY["strip_limit_relative"]


def test_ss_square_corner():
    assert _finest(SS, 1.0)["vm_max_location"]["region"] == "corner"
    assert _r1(1.0)["vm_max_kind"] == "corner"


def test_plate_imports_without_cad_or_scipy():
    code = """
import sys
sys.path.insert(0, 'backend')
import engineering.plate_spec, engineering.plate_fea, engineering.plate_reference
blocked = {'cadquery', 'OCP', 'scipy'}.intersection(name.split('.')[0] for name in sys.modules)
if blocked:
    raise RuntimeError(sorted(blocked))
"""
    subprocess.run([sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[2], check=True)


def test_evidence_table(capsys):
    labels = {SS: "SS", CL: "CL"}
    with capsys.disabled():
        print("\na/b edge FE_w_coeff ref_w_coeff rel_w FE_vm_MPa ref_vm_MPa rel_vm "
              "mesh_w mesh_vm equilibrium_max vm_region")
        for edge, ratio in MATRIX:
            v = _matrix_values(edge, ratio)
            print(f"{ratio:g} {labels[edge]} " + (
                "{w:.7f} {w_ref:.7f} {dw:.3%} {vm:.4f} {vm_ref:.4f} {dvm:.3%} "
                "{mesh_w:.3%} {mesh_vm:.3%} {equilibrium:.2e} {region}"
            ).format(**v))
        print("R3 relative errors against printed coefficients (inf uses R4)")
        for row in BOOK["rows"]:
            errors = " ".join(f"{key}={_rel(value, row['coefficients'][key]['value']):.3%}"
                              for key, value in _r3_values(row).items())
            note = " (known Table 35 centre discrepancy)" if any(
                (row["edge_condition"], row["ratio"], k) in KNOWN_DISCREPANCIES for k in row["coefficients"]) else ""
            print(f"R3 {labels[row['edge_condition']]} {row['ratio']} {errors}{note}")
        for edge, field in STRIP_CHECKS:
            value, limit = _strip_values(edge, field)
            print(f"R4 {labels[edge]} {STRIP_RATIO:g} {field} FE={value:.6g} "
                  f"strip={limit:.6g} rel={_rel(value, limit):.3%}")
