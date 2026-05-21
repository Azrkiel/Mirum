"""Smoke-test the five Domain F primitives against the four reference prompts.

Prints isValid / volume / key dimensions for each. Runnable both on host
(cadquery 2.4) and inside the production cadenv container (cadquery 2.7).
"""
from __future__ import annotations

import math
import sys
import traceback


def banner(name: str) -> None:
    print(f"\n=== {name} ===", flush=True)


def report(label: str, payload: object, err: str | None = None) -> None:
    status = "OK" if err is None else "FAIL"
    print(f"[{status}] {label}: {payload}", flush=True)
    if err:
        print(err, flush=True)


def metrics(wp) -> dict:
    val = wp.val()
    return {
        "isValid": val.isValid(),
        "volume_mm3": round(val.Volume(), 2),
        "type": type(val).__name__,
    }


def main() -> int:
    try:
        from cad_utils import (
            make_combustion_chamber,
            make_conical_nozzle,
            make_helical_cooling_channels,
            make_injector_plate,
            make_rao_bell_nozzle,
        )
    except Exception:
        print("Failed to import cad_utils primitives:")
        traceback.print_exc()
        return 1

    # ─────────────────── Reference prompt #1 ───────────────────
    banner("Ref #1 — conical bell nozzle")
    try:
        n1 = make_conical_nozzle(
            throat_radius=25.0,
            exit_radius=75.0,
            half_angle_deg=15.0,
            converging_angle_deg=30.0,
            chamber_radius=50.0,
        )
        m = metrics(n1)
        m["expansion_ratio"] = round((75.0 / 25.0) ** 2, 2)
        report("conical nozzle r_t=25 r_e=75 eps=9", m)
    except Exception:
        report("conical nozzle r_t=25 r_e=75 eps=9", None, traceback.format_exc())

    # ─────────────────── Reference prompt #2 ───────────────────
    banner("Ref #2 — Rao bell 80% length")
    try:
        n2 = make_rao_bell_nozzle(
            throat_radius=20.0,
            expansion_ratio=16.0,
            length_fraction=0.8,
            chamber_radius=60.0,
        )
        m = metrics(n2)
        m["expected_exit_r_mm"] = round(20.0 * math.sqrt(16.0), 2)
        report("Rao bell r_t=20 eps=16 Lf=0.8", m)
    except Exception:
        report("Rao bell r_t=20 eps=16 Lf=0.8", None, traceback.format_exc())

    # ─────────────────── Reference prompt #3 ───────────────────
    banner("Ref #3 — regen-cooled bell with 60 helical channels")
    try:
        nz = make_rao_bell_nozzle(
            throat_radius=25.0,
            expansion_ratio=12.0,
            length_fraction=0.8,
            chamber_radius=60.0,
        )
        nz_outer_radius = 25.0 + 1.0  # throat + wall
        n3 = make_helical_cooling_channels(
            nozzle_solid=nz,
            nozzle_outer_radius=nz_outer_radius + 3.0,  # outer cylinder of channels
            channel_count=60,
            channel_width=2.0,
            channel_depth=3.0,
            helix_angle_deg=15.0,
            axial_length=80.0,
        )
        report("regen cooled bell (60ch x 2x3 mm 15°)", metrics(n3))
    except Exception:
        report("regen cooled bell (60ch x 2x3 mm 15°)", None, traceback.format_exc())

    # ─────────────────── Reference prompt #4 ───────────────────
    banner("Ref #4 — full thrust chamber assembly")
    try:
        chamber = make_combustion_chamber(
            radius=40.0, l_star=1.0, throat_radius=20.0,
            include_converging_section=False,
        )
        m_c = metrics(chamber)
        report("chamber r=40 L*=1.0 r_t=20", m_c)

        nozzle = make_rao_bell_nozzle(
            throat_radius=20.0,
            expansion_ratio=16.0,
            length_fraction=0.8,
            chamber_radius=40.0,
        )
        m_n = metrics(nozzle)
        report("Rao bell paired with chamber", m_n)

        injector = make_injector_plate(
            plate_radius=45.0,
            plate_thickness=6.0,
            orifice_count=24,
            orifice_diameter=1.5,
            pattern="impinging_doublet",
            distribution_radius=30.0,
            impingement_angle_deg=30.0,
        )
        m_i = metrics(injector)
        report("injector plate (24 impinging-doublet pairs)", m_i)
    except Exception:
        report("Ref #4 components", None, traceback.format_exc())

    # ─────────────────── Edge cases / negative tests ───────────────────
    banner("Negative tests")
    try:
        make_conical_nozzle(throat_radius=25.0, half_angle_deg=15.0,
                            converging_angle_deg=30.0, chamber_radius=50.0)
        print("[FAIL] missing exit_radius/expansion_ratio did not raise")
    except ValueError as e:
        print(f"[OK]  missing exit/eps -> {e}")
    try:
        make_helical_cooling_channels(
            nozzle_solid=cq_workplane_box(),  # not a real nozzle but a Workplane
            nozzle_outer_radius=2.0,
            channel_count=2,
            channel_width=1.0,
            channel_depth=5.0,  # > outer radius
        )
        print("[FAIL] channel_depth >= outer_radius did not raise")
    except ValueError as e:
        print(f"[OK]  channel_depth >= outer -> {e}")
    except Exception:
        traceback.print_exc()

    return 0


def cq_workplane_box():
    import cadquery as cq
    return cq.Workplane("XY").box(10, 10, 10)


if __name__ == "__main__":
    sys.exit(main())
