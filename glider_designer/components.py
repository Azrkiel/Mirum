"""
Parametric component generators: wings, fuselages and tails.

MOUNTING INTERFACE (what makes parts interchangeable)
-----------------------------------------------------
All geometry uses AeroSandbox geometry axes: x points aft, y to the right
wing, z up. Units are metres.

* Fuselage: nose tip at the origin, body along +x. It exposes two hard points:
    - wing_mount():  (x, z) where the wing root leading edge sits (on top of the body)
    - tail_mount():  (x, z) at the end of the boom, top surface
* Wing:     built in a local frame with its root leading edge at (0, 0, 0).
* Tail:     built in a local frame whose origin is the tail attach point. The
            root trailing edges of the tail surfaces sit at local x = 0, so the
            tail always ends flush with the end of the fuselage boom.

The assembler (assembly.py) just translates each part to the hard points, so
any wing can go on any fuselage with any tail.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, asdict

import numpy as np
import aerosandbox as asb

_AIRFOILS: dict[str, asb.Airfoil] = {}


def naca(code: str) -> asb.Airfoil:
    """Cached NACA 4-digit airfoil, e.g. naca('2410')."""
    if code not in _AIRFOILS:
        _AIRFOILS[code] = asb.Airfoil(f"naca{code}")
    return _AIRFOILS[code]


@dataclass
class Section:
    """One cross-section of a lifting surface: leading-edge point, chord, twist."""
    x: float
    y: float
    z: float
    chord: float
    twist: float  # degrees, positive = nose up

    def moved(self, dx=0.0, dy=0.0, dz=0.0, dtwist=0.0) -> "Section":
        return Section(self.x + dx, self.y + dy, self.z + dz, self.chord, self.twist + dtwist)


@dataclass
class Surface:
    """A lifting surface: sections from root to tip (one side if symmetric)."""
    name: str
    sections: list
    symmetric: bool
    airfoil: str          # NACA 4-digit code
    role: str             # "wing", "htail", "vtail", "vee"
    dihedral_deg: float = 0.0   # only used for V-tail area projection

    def moved(self, dx=0.0, dz=0.0, dtwist=0.0) -> "Surface":
        return Surface(self.name, [s.moved(dx, 0.0, dz, dtwist) for s in self.sections],
                       self.symmetric, self.airfoil, self.role, self.dihedral_deg)

    def to_asb(self) -> asb.Wing:
        af = naca(self.airfoil)
        return asb.Wing(
            name=self.name,
            symmetric=self.symmetric,
            xsecs=[asb.WingXSec(xyz_le=[s.x, s.y, s.z], chord=s.chord, twist=s.twist, airfoil=af)
                   for s in self.sections],
        )

    def strips(self, n_sub: int = 8, cg_chord_frac: float = 0.42):
        """Split the surface into small spanwise strips for the mass model.

        Returns a list of (area_m2, x, y, z) for ONE side; area is measured in the
        panel plane (true area, what you actually cut out of foam)."""
        out = []
        for a, b in zip(self.sections[:-1], self.sections[1:]):
            panel_len = math.hypot(b.y - a.y, b.z - a.z)
            for k in range(n_sub):
                t0, t1 = k / n_sub, (k + 1) / n_sub
                tm = 0.5 * (t0 + t1)
                c0 = a.chord + (b.chord - a.chord) * t0
                c1 = a.chord + (b.chord - a.chord) * t1
                cm = a.chord + (b.chord - a.chord) * tm
                area = 0.5 * (c0 + c1) * panel_len / n_sub
                x = a.x + (b.x - a.x) * tm + cg_chord_frac * cm
                y = a.y + (b.y - a.y) * tm
                z = a.z + (b.z - a.z) * tm
                out.append((area, x, y, z))
        return out

    def true_area(self) -> float:
        one_side = sum(s[0] for s in self.strips(n_sub=4))
        return one_side * (2 if self.symmetric else 1)


# ====================================================================== #
# WING
# ====================================================================== #
@dataclass
class WingParams:
    id: str
    style: str               # "tapered" | "elliptical" | "polyhedral"
    span: float
    root_chord: float
    taper: float
    sweep_qc_deg: float
    dihedral_deg: float
    washout_deg: float
    incidence_deg: float
    naca: str

    @staticmethod
    def random(rng: random.Random, R: dict, idx: int) -> "WingParams":
        u = lambda k: rng.uniform(*R[k])
        m = rng.choice(R["naca_camber_pct"])
        p = rng.choice(R["naca_camber_pos"]) if m > 0 else 0
        t = rng.choice(R["naca_thickness_pct"])
        return WingParams(
            id=f"W{idx:02d}", style=rng.choice(R["style"]), span=u("span_m"),
            root_chord=u("root_chord_m"), taper=u("taper"), sweep_qc_deg=u("sweep_qc_deg"),
            dihedral_deg=u("dihedral_deg"), washout_deg=u("washout_deg"),
            incidence_deg=u("incidence_deg"), naca=f"{m}{p}{t:02d}",
        )

    def chord_at(self, eta: float) -> float:
        cr, lam = self.root_chord, self.taper
        if self.style == "elliptical":
            # elliptical-ish distribution that never goes below taper * root chord
            return cr * (lam + (1.0 - lam) * math.sqrt(max(0.0, 1.0 - eta ** 2)))
        return cr * (1.0 - (1.0 - lam) * eta)

    def surface(self) -> Surface:
        half = self.span / 2.0
        if self.style == "elliptical":
            etas = [0.0, 0.3, 0.55, 0.75, 0.88, 0.96, 1.0]
        elif self.style == "polyhedral":
            etas = [0.0, 0.55, 1.0]
        else:
            etas = [0.0, 1.0]

        z_tip = half * math.tan(math.radians(self.dihedral_deg))
        x_qc_root = 0.25 * self.root_chord
        secs = []
        for eta in etas:
            c = self.chord_at(eta)
            y = eta * half
            x_qc = x_qc_root + y * math.tan(math.radians(self.sweep_qc_deg))
            if self.style == "polyhedral":
                # flat centre panel, all the dihedral in the outer panels
                z = 0.0 if eta <= 0.55 else z_tip * (eta - 0.55) / 0.45
            else:
                z = eta * z_tip
            secs.append(Section(x=x_qc - 0.25 * c, y=y, z=z, chord=c,
                                twist=self.incidence_deg - self.washout_deg * eta))
        return Surface("wing", secs, True, self.naca, "wing")

    def tip_chord(self) -> float:
        return self.chord_at(1.0)


# ====================================================================== #
# FUSELAGE
# ====================================================================== #
@dataclass
class FuselageParams:
    id: str
    style: str               # "pod_boom" | "torpedo" | "slab"
    length: float
    max_diameter: float
    nose_frac: float
    pod_frac: float
    boom_radius: float
    wing_station_in_pod: float   # 0 = wing LE at the end of the nose, 1 = at the end of the pod

    @staticmethod
    def random(rng: random.Random, R: dict, idx: int) -> "FuselageParams":
        u = lambda k: rng.uniform(*R[k])
        return FuselageParams(
            id=f"F{idx:02d}", style=rng.choice(R["style"]), length=u("length_m"),
            max_diameter=u("max_diameter_m"), nose_frac=u("nose_frac"), pod_frac=u("pod_frac"),
            boom_radius=u("boom_radius_m"), wing_station_in_pod=u("wing_station_in_pod"),
        )

    # Station list: (x, width, height, superellipse_shape)
    def stations(self):
        L, R, rb = self.length, self.max_diameter / 2.0, self.boom_radius
        ln, lp = self.pod_range()
        hr = 0.85 if self.style == "slab" else 1.0      # height / width
        shape = 5.0 if self.style == "slab" else 2.0     # 2 = ellipse, higher = boxier
        pts = []
        # elliptical nose
        for t in np.linspace(0, 1, 7):
            x = t * ln
            r = R * math.sqrt(max(0.0, 1.0 - (1.0 - t) ** 2))
            pts.append((x, r))
        if self.style == "torpedo":
            # full-diameter section along the pod, then a smooth cosine taper
            x_max = lp
            pts.append((x_max, R))
            for t in np.linspace(0, 1, 9)[1:]:
                x = x_max + t * (L - x_max)
                r = rb + (R - rb) * 0.5 * (1 + math.cos(math.pi * t))
                pts.append((x, r))
        else:
            x_taper_end = min(lp + 0.15 * L, 0.9 * L)
            pts.append((lp, R))
            for t in np.linspace(0, 1, 5)[1:]:
                x = lp + t * (x_taper_end - lp)
                r = rb + (R - rb) * 0.5 * (1 + math.cos(math.pi * t))
                pts.append((x, r))
            pts.append((L, 0.85 * rb))
        out = []
        for x, r in pts:
            r = max(r, 1e-4)
            out.append((x, 2 * r, 2 * r * hr, shape))
        return out

    def half_height_at(self, x: float) -> float:
        st = self.stations()
        return float(np.interp(x, [s[0] for s in st], [s[2] / 2 for s in st]))

    def half_width_at(self, x: float) -> float:
        st = self.stations()
        return float(np.interp(x, [s[0] for s in st], [s[1] / 2 for s in st]))

    # ---- hard points ----
    def pod_range(self):
        ln = self.nose_frac * self.length
        return ln, max(self.pod_frac * self.length, ln + 0.05 * self.length)

    def wing_mount(self):
        # The wing sits over the pod so the payload bay can reach under the wing,
        # which is where the balance point of a glider has to be.
        ln, lp = self.pod_range()
        x = ln + self.wing_station_in_pod * (lp - ln)
        return x, self.half_height_at(x)

    def tail_mount(self):
        return self.length, self.half_height_at(self.length)

    def payload_bay(self, diameter: float, length: float, wall: float):
        """Range of payload-centre positions (x_min, x_max) where a payload of
        this size fits inside the body, or None if it fits nowhere.
        The payload can slide inside this bay to help balance the glider."""
        need = diameter / 2 + wall
        xs = np.linspace(length / 2, self.length - length / 2, 300)
        fits = [x for x in xs
                if min(min(self.half_width_at(xx), self.half_height_at(xx))
                       for xx in (x - length / 2, x, x + length / 2)) >= need]
        if not fits:
            return None
        # keep the first contiguous run (the pod)
        step = xs[1] - xs[0]
        run = [fits[0]]
        for x in fits[1:]:
            if x - run[-1] > 1.5 * step:
                break
            run.append(x)
        return run[0], run[-1]

    def ballast_station(self) -> float:
        return 0.5 * self.nose_frac * self.length

    def to_asb(self) -> asb.Fuselage:
        return asb.Fuselage(
            name="fuselage",
            xsecs=[asb.FuselageXSec(xyz_c=[x, 0, 0], width=w, height=h, shape=s)
                   for x, w, h, s in self.stations()],
        )

    def shell_segments(self):
        """(surface_area_m2, x_mid) of each fuselage segment, for the mass model."""
        st = self.stations()
        segs = []
        for (x0, w0, h0, s0), (x1, w1, h1, _) in zip(st[:-1], st[1:]):
            p0, p1 = _perimeter(w0, h0, s0), _perimeter(w1, h1, s0)
            dr = 0.5 * ((w1 + h1) - (w0 + h0)) / 2
            slant = math.hypot(x1 - x0, dr)
            segs.append((0.5 * (p0 + p1) * slant, 0.5 * (x0 + x1)))
        return segs


def _perimeter(w: float, h: float, shape: float) -> float:
    a, b = w / 2, h / 2
    ellipse = math.pi * (3 * (a + b) - math.sqrt((3 * a + b) * (a + 3 * b)))  # Ramanujan
    box = 2 * (w + h)
    blend = min(1.0, max(0.0, (shape - 2.0) / 6.0))
    return ellipse + blend * (box - ellipse)


# ====================================================================== #
# TAIL
# ====================================================================== #
@dataclass
class TailParams:
    id: str
    kind: str                 # "conventional" | "T" | "V"
    airfoil: str
    h_span: float = 0.0
    h_root_chord: float = 0.0
    h_taper: float = 1.0
    v_height: float = 0.0
    v_root_chord: float = 0.0
    v_taper: float = 1.0
    v_sweep_le_deg: float = 0.0
    vt_panel_span: float = 0.0
    vt_root_chord: float = 0.0
    vt_taper: float = 1.0
    vt_dihedral_deg: float = 0.0

    @staticmethod
    def random(rng: random.Random, R: dict, idx: int) -> "TailParams":
        u = lambda k: rng.uniform(*R[k])
        kind = rng.choice(R["kind"])
        t = TailParams(id=f"T{idx:02d}", kind=kind, airfoil=rng.choice(R["airfoil"]))
        if kind == "V":
            t.vt_panel_span, t.vt_root_chord = u("vt_panel_span_m"), u("vt_root_chord_m")
            t.vt_taper, t.vt_dihedral_deg = u("vt_taper"), u("vt_dihedral_deg")
        else:
            t.h_span, t.h_root_chord, t.h_taper = u("h_span_m"), u("h_root_chord_m"), u("h_taper")
            t.v_height, t.v_root_chord = u("v_height_m"), u("v_root_chord_m")
            t.v_taper, t.v_sweep_le_deg = u("v_taper"), u("v_sweep_le_deg")
            if kind == "T":
                # a T-tail fin needs a wide tip to carry the stabiliser
                t.v_taper = rng.uniform(max(R["v_taper"][0], 0.75), 1.0)
        return t

    def fin_tip_chord(self) -> float:
        return self.v_root_chord * self.v_taper

    def surfaces(self, incidence_deg: float = 0.0) -> list:
        """Tail surfaces in the tail's local frame (origin = attach point,
        root trailing edges at x = 0). `incidence_deg` sets the stabiliser
        (or V-tail) incidence, which the analysis solves for trim."""
        out = []
        if self.kind == "V":
            c, lam, b = self.vt_root_chord, self.vt_taper, self.vt_panel_span
            g = math.radians(self.vt_dihedral_deg)
            x_le = -c
            secs = [Section(x_le, 0, 0, c, incidence_deg),
                    Section(x_le + 0.25 * c * (1 - lam), b * math.cos(g), b * math.sin(g), c * lam, incidence_deg)]
            out.append(Surface("vtail_V", secs, True, self.airfoil, "vee", self.vt_dihedral_deg))
            return out

        # vertical fin, root trailing edge at x = 0
        cv, hv = self.v_root_chord, self.v_height
        cvt = cv * self.v_taper
        fin_le = -cv
        fin_tip_le = fin_le + hv * math.tan(math.radians(self.v_sweep_le_deg))
        fin = Surface("vtail", [Section(fin_le, 0, 0, cv, 0.0), Section(fin_tip_le, 0, hv, cvt, 0.0)],
                      False, self.airfoil, "vtail")

        ch, bh = self.h_root_chord, self.h_span
        if self.kind == "T":
            x0, z0 = fin_tip_le, hv            # stab sits on top of the fin
        else:
            x0, z0 = -ch, 0.0                  # stab sits on the boom, TE flush with the end
        cht = ch * self.h_taper
        stab = Surface("htail", [Section(x0, 0, z0, ch, incidence_deg),
                                 Section(x0 + 0.25 * (ch - cht), bh / 2, z0, cht, incidence_deg)],
                       True, self.airfoil, "htail")
        out += [stab, fin]
        return out


def params_dict(p) -> dict:
    return asdict(p)
