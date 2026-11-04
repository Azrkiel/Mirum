"""
Assembly: put a wing, fuselage and tail together, then work out mass,
centre of gravity (CG), ballast, inertia and tail volume coefficients.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import aerosandbox as asb

from .components import WingParams, FuselageParams, TailParams, Surface


@dataclass
class MassItem:
    name: str
    mass: float
    x: float
    y: float = 0.0
    z: float = 0.0


@dataclass
class Glider:
    wing: WingParams
    fuse: FuselageParams
    tail: TailParams
    cfg: dict
    ballast_kg: float = 0.0
    tail_incidence_deg: float = 0.0
    payload_x: float | None = None   # payload centre, set by balance()
    wing_x: float | None = None      # wing root LE, slid along the saddle by the analysis

    @property
    def id(self) -> str:
        return f"{self.wing.id}-{self.fuse.id}-{self.tail.id}"

    # ------------------------------------------------------------------ #
    # Geometry placement (the mounting interface in action)
    # ------------------------------------------------------------------ #
    def wing_saddle(self):
        """Allowed wing slide interval on the pod, including designer position locks."""
        nose, end = self.fuse.pod_range()
        lo, hi = self.cfg['ranges']['fuselage']['wing_station_in_pod']
        return nose + (end - nose) * lo, nose + (end - nose) * hi

    def wing_position(self):
        if self.wing_x is None:
            return self.fuse.wing_mount()
        return self.wing_x, self.fuse.half_height_at(self.wing_x)

    def wing_surface(self) -> Surface:
        x, z = self.wing_position()
        return self.wing.surface().moved(dx=x, dz=z)

    def tail_surfaces(self, incidence_deg: float | None = None) -> list:
        inc = self.tail_incidence_deg if incidence_deg is None else incidence_deg
        x, z = self.fuse.tail_mount()
        return [s.moved(dx=x, dz=z) for s in self.tail.surfaces(inc)]

    def airplane(self, xyz_ref=None, tail_incidence_deg: float | None = None) -> asb.Airplane:
        wings = [self.wing_surface().to_asb()] + [s.to_asb() for s in self.tail_surfaces(tail_incidence_deg)]
        airplane = asb.Airplane(
            name=self.id,
            xyz_ref=list(xyz_ref) if xyz_ref is not None else [0, 0, 0],
            wings=wings,                       # wings[0] = main wing -> reference S, c, b
            fuselages=[self.fuse.to_asb()],
        )
        # Trusted CEM configuration only. Legacy configurations have no context.
        airplane.cem_context = self.cfg.get('cem_context')
        return airplane

    # ------------------------------------------------------------------ #
    # Reference geometry
    # ------------------------------------------------------------------ #
    def wing_ref(self) -> dict:
        w = self.wing_surface().to_asb()
        return {"S": w.area(), "b": w.span(), "mac": w.mean_aerodynamic_chord(),
                "x_ac": float(w.aerodynamic_center()[0]), "AR": w.aspect_ratio()}

    def tail_volumes(self) -> dict:
        """Classic tail volume coefficients, arms measured wing AC -> tail AC.

        V-tail: projected areas S cos^2(G) (horizontal) and S sin^2(G) (vertical)."""
        ref = self.wing_ref()
        Sh = Sv = 0.0
        xh = xv = None
        for s in self.tail_surfaces(0.0):
            w = s.to_asb()
            area, x_ac = w.area(), float(w.aerodynamic_center()[0])
            if s.role == "htail":
                Sh, xh = area, x_ac
            elif s.role == "vtail":
                Sv, xv = area, x_ac
            elif s.role == "vee":
                g = math.radians(s.dihedral_deg)
                Sh, Sv, xh, xv = area * math.cos(g) ** 2, area * math.sin(g) ** 2, x_ac, x_ac
        lh, lv = xh - ref["x_ac"], xv - ref["x_ac"]
        return {
            "Sh": Sh, "Sv": Sv, "lh": lh, "lv": lv,
            "Vh": Sh * lh / (ref["S"] * ref["mac"]),
            "Vv": Sv * lv / (ref["S"] * ref["b"]),
        }

    # ------------------------------------------------------------------ #
    # Mass model
    # ------------------------------------------------------------------ #
    def mass_items(self, include_ballast: bool = True, include_payload: bool = True) -> list:
        mat, mis = self.cfg["materials"], self.cfg["mission"]
        items: list[MassItem] = []

        def add_surface(surf: Surface, density: float, frac: float):
            for area, x, y, z in surf.strips(cg_chord_frac=frac):
                items.append(MassItem(surf.name, area * density, x, y, z))
                if surf.symmetric:
                    items.append(MassItem(surf.name, area * density, x, -y, z))

        add_surface(self.wing_surface(), mat["wing_areal_density_kg_m2"], mat["wing_cg_chord_fraction"])
        for s in self.tail_surfaces(0.0):
            add_surface(s, mat["tail_areal_density_kg_m2"], 0.42)

        for area, x in self.fuse.shell_segments():
            items.append(MassItem("fuselage", area * mat["fuselage_shell_areal_density_kg_m2"], x))

        xw, zw = self.wing_position()
        items.append(MassItem("hardware", mat["fixed_hardware_mass_kg"], xw + 0.3 * self.wing.root_chord, 0, zw))
        if include_payload and mis["payload_mass_kg"] > 0:
            items.append(MassItem("payload", mis["payload_mass_kg"], self.payload_position()))
        if include_ballast and self.ballast_kg > 0:
            items.append(MassItem("ballast", self.ballast_kg, self.fuse.ballast_station()))
        return items

    def payload_bay(self):
        mis = self.cfg["mission"]
        return self.fuse.payload_bay(mis["payload_diameter_m"], mis["payload_length_m"],
                                     self.cfg["materials"]["wall_thickness_m"])

    def payload_position(self) -> float:
        if self.payload_x is not None:
            return self.payload_x
        bay = self.payload_bay()
        return 0.5 * (bay[0] + bay[1]) if bay else self.fuse.ballast_station()

    def mass_summary(self, include_ballast: bool = True, include_payload: bool = True) -> dict:
        items = self.mass_items(include_ballast, include_payload)
        m = sum(i.mass for i in items)
        cg = np.array([sum(i.mass * i.x for i in items), sum(i.mass * i.y for i in items),
                       sum(i.mass * i.z for i in items)]) / m
        Ixx = sum(i.mass * ((i.y - cg[1]) ** 2 + (i.z - cg[2]) ** 2) for i in items)
        Iyy = sum(i.mass * ((i.x - cg[0]) ** 2 + (i.z - cg[2]) ** 2) for i in items)
        Izz = sum(i.mass * ((i.x - cg[0]) ** 2 + (i.y - cg[1]) ** 2) for i in items)
        by_part: dict[str, float] = {}
        for i in items:
            by_part[i.name] = by_part.get(i.name, 0.0) + i.mass
        return {"mass": m, "cg": cg, "Ixx": Ixx, "Iyy": Iyy, "Izz": Izz, "by_part": by_part}

    def balance(self, x_cg_target: float) -> None:
        """Balance the glider at x_cg_target, the way you would at the workbench:
        1. slide the payload along its bay toward the spot that gives the target CG
        2. if that is not enough (tail-heavy), add nose ballast
        If it is still nose-heavy, the CG simply ends up ahead of the target and
        the static-margin check reports it."""
        mp = self.cfg["mission"]["payload_mass_kg"]
        self.ballast_kg = 0.0
        bay = self.payload_bay()
        if mp > 0 and bay:
            rest = self.mass_summary(include_ballast=False, include_payload=False)
            Mr, xr = rest["mass"], rest["cg"][0]
            xp = (x_cg_target * (Mr + mp) - Mr * xr) / mp
            self.payload_x = float(min(max(xp, bay[0]), bay[1]))
        mb = self.solve_ballast(x_cg_target)
        self.ballast_kg = mb if math.isfinite(mb) and mb > 0 else 0.0

    def solve_ballast(self, x_cg_target: float) -> float:
        """Nose ballast (kg) that puts the CG exactly at x_cg_target.

        Moment balance:  (M0 * x0 + mb * xb) / (M0 + mb) = x_target
                   -->   mb = M0 (x0 - x_target) / (x_target - xb)
        Negative result = already nose-heavy (no ballast can fix that)."""
        base = self.mass_summary(include_ballast=False)
        M0, x0 = base["mass"], base["cg"][0]
        xb = self.fuse.ballast_station()
        if x_cg_target <= xb:
            return float("inf")
        return M0 * (x0 - x_cg_target) / (x_cg_target - xb)
