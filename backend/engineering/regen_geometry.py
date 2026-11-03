"""Reference geometry for RegenChamberSpec, mirroring picogk-builder/Program.cs.

Geometry only (no physics). numpy only; no cadquery, no compiler import.

The builder renders  solid = Shell  minus  (helical capsule chains + 2 manifold ring chains + 2 radial ports).
This module models exactly that construction in float64:

* Shell: ri(z) <= rho <= ri(z)+wall, 0 <= z <= L (ri clamped to [0.01, L-0.01] like the builder).
* Channel c, step i: round-capped beam (capsule, radius channel_radius) between helix nodes
  Pt(c, z) = (r cos th, r sin th, z), r = ri(z)+wall/2, th = c*2pi/N + z*twist,
  z_k = z0 + (z1-z0)*k/steps, z0 = end_margin, z1 = L-end_margin, steps = ceil((z1-z0)/(1.5 v)).
  Chords are modelled, not the ideal curve, so there is no sagitta error.
* Manifold rings at z0 and z1: polygon chains of capsules (radius manifold_radius) at r = ri(mz)+wall/2.
* Radial port at theta=0 per ring: capsule from (r,0,mz) to (ri(mz)+wall+PORT_OVERSHOOT, 0, mz).
Overlaps (channel ends inside rings, port starts on a ring vertex) are inherent: all capsules are unioned.

Error model (explicit, no sample counts):
* ``surface_tolerance_mm``: where a voxelised/meshed surface may sit relative to the analytic surface
  = one voxel (iso-surface interpolation between samples <= v apart) + float32 slack of the builder.
* Shell distance is a *lower bound* on Euclidean distance (radial gap / sqrt(1+max_slope^2), axial exact).
* Coolant distance is exact for the modelled capsules wherever it is below ``max_distance_mm``;
  above that it is clamped to ``max_distance_mm`` (a lower bound on the true value).
* Centerline coverage samples are spaced so every coolant-surface point is within
  ``coverage_error_bound`` of a sample (Hausdorff, derived from the gap actually used).
Queries are batched with a fixed element budget and ordered deterministically; results do not depend on batching.
"""
from __future__ import annotations

import math
from contextlib import contextmanager
from typing import Iterator

import numpy as np

from .regen_spec import PORT_OVERSHOOT_MM, RegenChamberSpec, inner_radius

OUTSIDE, SOLID, COOLANT, UNCERTAIN = 0, 1, 2, -1
_ELEMENT_BUDGET = 150_000  # candidate (point, channel, step) triples per batch
_F32 = np.float32


class BudgetExceeded(Exception):
    """A deterministic, fail-closed verification resource limit."""


class Budget:
    """Verification-local conservative accounting, independent of process RSS.

    The 1 GiB default leaves headroom under the controller's 1.5 GiB tree cap.
    Estimates include overlapping numpy temporaries and Python/container overhead;
    this is an allocation guard, not a claim of validated large-mesh performance.
    """
    def __init__(self, limits=None, memory_bytes=1 << 30):
        self.limits = {'reference_budget': 60_000_000, 'reference_batch_budget': _ELEMENT_BUDGET,
                       'sample_budget': 4_000_000, 'sample_batch_budget': 20_000}
        self.limits.update(limits or {})
        self.memory_bytes = memory_bytes
        self.live = 16 << 20  # interpreter-independent bookkeeping / diagnostic allowance
        self.used = {}

    def check(self, name, amount):
        if amount > self.limits[name]:
            raise BudgetExceeded(f'{name} exhausted ({amount} > {self.limits[name]})')

    def spend(self, name, amount):
        amount = int(amount)
        total = self.used.get(name, 0) + amount
        self.check(name, total)
        self.used[name] = total

    def memory(self, size):
        if self.live + int(size) > self.memory_bytes:
            raise BudgetExceeded('memory_budget exhausted')

    def retain(self, size):
        self.memory(size)
        self.live += int(size)

    @contextmanager
    def hold(self, size):
        self.retain(size)
        try:
            yield
        finally:
            self.live -= int(size)

    @contextmanager
    def scope(self):
        """Release phase-local storage; cumulative work counters never reset."""
        before = self.live
        try:
            yield
        finally:
            self.live = before


def _ri(spec: RegenChamberSpec, z: np.ndarray) -> np.ndarray:
    """Vectorised inner_radius (same formula, float64)."""
    z = np.asarray(z, dtype=np.float64)
    cl, zt, L = spec.chamber_length_mm, spec.throat_z_mm, spec.total_length_mm
    rt, rc, re = spec.throat_radius_mm, spec.chamber_radius_mm, spec.exit_radius_mm
    t1 = np.clip((z - cl) / spec.convergent_length_mm, 0.0, 1.0)
    t2 = np.clip((z - zt) / spec.divergent_length_mm, 0.0, 1.0)
    conv = rt + (rc - rt) * 0.5 * (1 + np.cos(np.pi * t1))
    div = rt + (re - rt) * 0.5 * (1 - np.cos(np.pi * t2))
    out = np.where(z <= cl, rc, np.where(z <= zt, conv, np.where(z <= L, div, re)))
    return out


def _seg_dist(p: np.ndarray, a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Distance from p (...,3) to segments a-b (...,3); broadcasting."""
    ab = b - a
    ap = p - a
    den = np.einsum('...k,...k->...', ab, ab)
    t = np.einsum('...k,...k->...', ap, ab) / np.where(den > 0, den, 1.0)
    t = np.clip(np.where(den > 0, t, 0.0), 0.0, 1.0)
    d = ap - t[..., None] * ab
    return np.sqrt(np.einsum('...k,...k->...', d, d))


def _plan_chunks(cost: np.ndarray, budget=None) -> Iterator[np.ndarray]:
    """Count before broadcasting; even a single oversized item rejects."""
    budget = budget or Budget()
    budget.memory(len(cost) * 64)
    if len(cost):
        budget.check('reference_batch_budget', int(cost.max()))
    order = np.argsort(cost, kind='stable')
    n = len(order)
    s = 0
    while s < n:
        e = s + 1
        while e < n and (e + 1 - s) * int(cost[order[e]]) <= min(_ELEMENT_BUDGET, budget.limits['reference_batch_budget']):
            e += 1
        elements = (e - s) * int(cost[order[e - 1]])
        budget.spend('reference_budget', elements)
        budget.memory(elements * 512 + len(cost) * 64)
        yield order[s:e]
        s = e


class RegenGeometry:
    """Deterministic reference model of the specified part (shell minus coolant union)."""

    def __init__(self, spec: RegenChamberSpec, budget=None):
        self.budget = budget or Budget()
        self.spec = spec
        s = spec
        self.v = s.voxel_size_mm
        self.L = float(_F32(_F32(s.chamber_length_mm + s.convergent_length_mm) + _F32(s.divergent_length_mm)))
        self.z0 = float(s.end_margin_mm)
        self.z1 = self.L - self.z0
        self.rc = float(s.channel_radius_mm)
        self.rm = float(s.manifold_radius_mm)
        self.wall = float(s.wall_thickness_mm)
        self.n_ch = int(s.channel_count)
        self.twist = float(s.channel_twist_rad_per_mm)
        # builder: steps = (int)MathF.Ceiling((z1-z0)/(1.5f*Voxel)) in float32
        self.steps = max(1, int(np.ceil(_F32(self.z1 - self.z0) / (_F32(1.5) * _F32(self.v)))))
        self.dz = (self.z1 - self.z0) / self.steps
        self.ring_z = (self.z0, self.z1)
        self.ring_r = tuple(float(inner_radius(s, mz)) + self.wall / 2 for mz in self.ring_z)
        self.ring_segs = tuple(
            max(1, int(np.ceil(_F32(2 * math.pi) * _F32(r) / (_F32(1.5) * _F32(self.v))))) for r in self.ring_r)
        self.port_ends = tuple(float(inner_radius(s, mz)) + self.wall + PORT_OVERSHOOT_MM for mz in self.ring_z)
        self.max_slope = float(s.max_slope)
        self._lip = math.sqrt(1.0 + self.max_slope ** 2)
        extent = max(self.L, max(s.chamber_radius_mm, s.exit_radius_mm) + self.wall + PORT_OVERSHOOT_MM)
        self.float_slack_mm = 1e-6 * extent + 1e-9  # float32 coordinates in the builder
        self.surface_tolerance_mm = self.v + self.float_slack_mm
        self._ch_rlo = s.throat_radius_mm + self.wall / 2
        self._ch_rhi = max(s.chamber_radius_mm, s.exit_radius_mm) + self.wall / 2
        self._ch_step_angle = abs(self.twist) * self.dz + 0.0  # per-step angular advance
        self._ch_cos = math.cos(min(self._ch_step_angle, math.pi) / 2)

    # ------------------------------------------------------------------ exact construction access
    @property
    def segment_count(self) -> int:
        return self.n_ch * self.steps + sum(self.ring_segs) + 2

    @property
    def default_max_distance_mm(self) -> float:
        return self.spec.min_feature_mm

    def channel_nodes(self, c: int) -> np.ndarray:
        """(steps+1, 3) helix nodes of channel c (builder Pt(c, z_k))."""
        self.budget.check('reference_batch_budget', self.steps + 1)
        self.budget.spend('reference_budget', self.steps + 1)
        self.budget.memory((self.steps + 1) * 256)
        k = np.arange(self.steps + 1)
        return self._nodes(np.full(k.shape, c), k)

    def _nodes(self, c: np.ndarray, k: np.ndarray) -> np.ndarray:
        count = math.prod(np.broadcast_shapes(c.shape, k.shape))
        self.budget.memory(count * 256)
        z = self.z0 + (self.z1 - self.z0) * k / self.steps
        th = c * (2 * np.pi / self.n_ch) + z * self.twist
        r = np.broadcast_to(_ri(self.spec, z) + self.wall / 2, th.shape)
        return np.stack([r * np.cos(th), r * np.sin(th), np.broadcast_to(z, th.shape)], axis=-1)

    def iter_coolant_segments(self, batch: int = 20000) -> Iterator[tuple[str, np.ndarray, np.ndarray, float]]:
        """Yield (family, A(n,3), B(n,3), radius) for every capsule of the union, in deterministic order."""
        if batch < 1:
            raise ValueError('batch must be positive')
        batch = min(batch, self.budget.limits['reference_batch_budget'])
        for c in range(self.n_ch):
            for s in range(0, self.steps, batch):
                e = min(s + batch, self.steps)
                self.budget.spend('reference_budget', e - s)
                self.budget.memory((e - s + 1) * 512)
                nd = self._nodes(np.asarray(c), np.arange(s, e + 1))
                yield 'channel', nd[:-1], nd[1:], self.rc
        for j, mz in enumerate(self.ring_z):
            r, segs = self.ring_r[j], self.ring_segs[j]
            for s in range(0, segs, batch):
                e = min(s + batch, segs)
                self.budget.spend('reference_budget', e - s)
                self.budget.memory((e - s + 1) * 512)
                t = np.arange(s, e + 1) * (2 * np.pi / segs)
                nd = np.stack([r * np.cos(t), r * np.sin(t), np.full(t.shape, mz)], axis=-1)
                yield 'ring', nd[:-1], nd[1:], self.rm
        for j, mz in enumerate(self.ring_z):
            yield ('port', np.array([[self.ring_r[j], 0.0, mz]]), np.array([[self.port_ends[j], 0.0, mz]]), self.rm)

    # ------------------------------------------------------------------ shell
    def shell_sdf_lower(self, points) -> np.ndarray:
        """Signed value, negative inside the shell body. Sign exact; magnitude is a lower bound on Euclidean distance."""
        p = _pts(points, self.budget)
        rho = np.hypot(p[:, 0], p[:, 1])
        zc = np.clip(p[:, 2], 0.01, self.L - 0.01)
        ri = _ri(self.spec, zc)
        radial = np.maximum(ri - rho, rho - (ri + self.wall)) / self._lip
        axial = np.maximum(-p[:, 2], p[:, 2] - self.L)
        return np.maximum(radial, axial)

    # ------------------------------------------------------------------ coolant
    def coolant_sdf_parts(self, points, max_distance_mm: float | None = None) -> dict[str, np.ndarray]:
        """Signed distance (negative inside void) to each coolant family; clamped to max_distance_mm."""
        with self.budget.hold(max(1, len(points)) * 2048):
            return self._coolant_sdf_parts(points, max_distance_mm)

    def _coolant_sdf_parts(self, points, max_distance_mm):
        md = self.default_max_distance_mm if max_distance_mm is None else float(max_distance_mm)
        if not md > 0:
            raise ValueError('max_distance_mm must be > 0')
        p = _pts(points, self.budget)
        n = len(p)
        self.budget.memory(n * 2048)
        out = {}
        for name, rad, fn in (('channels', self.rc, self._channel_dist), ('rings', self.rm, self._ring_dist),
                              ('ports', self.rm, self._port_dist)):
            reach = md + rad
            d = np.full(n, reach)
            fn(p, reach, d)
            out[name] = np.minimum(d - rad, md)
        return out

    def coolant_sdf(self, points, max_distance_mm: float | None = None) -> np.ndarray:
        parts = self.coolant_sdf_parts(points, max_distance_mm)
        return np.minimum(np.minimum(parts['channels'], parts['rings']), parts['ports'])

    def _channel_dist(self, p, reach, d):
        rho = np.hypot(p[:, 0], p[:, 1])
        z = p[:, 2]
        rlo = self._ch_rlo * self._ch_cos
        act = np.nonzero((z >= self.z0 - reach) & (z <= self.z1 + reach)
                         & (rho >= rlo - reach) & (rho <= self._ch_rhi + reach))[0]
        if not len(act):
            return
        delta = 2 * np.pi / self.n_ch
        reff = np.maximum(np.minimum(rho[act], rlo), 1e-9)
        am = 2 * np.arcsin(np.minimum(1.0, reach / (2 * reff)))
        J = np.ceil((am + abs(self.twist) * reach) / delta).astype(np.int64)
        K = np.minimum(2 * J + 1, self.n_ch)
        W = int(np.ceil(2 * reach / self.dz)) + 2
        for sel in _plan_chunks(K * (W + 1), self.budget):
            idx = act[sel]
            pc = p[idx]
            Kc = int(K[sel].max())
            if Kc >= self.n_ch:
                cs = np.broadcast_to(np.arange(self.n_ch), (len(idx), self.n_ch))
            else:
                phi = np.arctan2(pc[:, 1], pc[:, 0])
                base = np.rint((phi - pc[:, 2] * self.twist) / delta).astype(np.int64)
                cs = (base[:, None] + np.arange(-(Kc // 2), Kc // 2 + 1)) % self.n_ch
            klo = np.floor((pc[:, 2] - reach - self.z0) / self.dz).astype(np.int64)
            ks = klo[:, None] + np.arange(W + 1)  # nodes
            nodes = self._nodes(cs[:, :, None], ks[:, None, :])  # (n,K,W+1,3)
            dist = _seg_dist(pc[:, None, None, :], nodes[:, :, :-1], nodes[:, :, 1:])
            valid = (ks[:, :-1] >= 0) & (ks[:, :-1] < self.steps)
            dist = np.where(valid[:, None, :], dist, np.inf)
            d[idx] = np.minimum(d[idx], dist.reshape(len(idx), -1).min(axis=1))

    def _ring_dist(self, p, reach, d):
        rho = np.hypot(p[:, 0], p[:, 1])
        for j, mz in enumerate(self.ring_z):
            r, segs = self.ring_r[j], self.ring_segs[j]
            delta = 2 * np.pi / segs
            rlo = r * math.cos(delta / 2)
            act = np.nonzero((np.abs(p[:, 2] - mz) <= reach) & (rho >= rlo - reach) & (rho <= r + reach))[0]
            if not len(act):
                continue
            reff = np.maximum(np.minimum(rho[act], rlo), 1e-9)
            am = 2 * np.arcsin(np.minimum(1.0, reach / (2 * reff)))
            W = np.minimum(np.ceil(2 * am / delta).astype(np.int64) + 2, segs)
            for sel in _plan_chunks(W + 1, self.budget):
                idx = act[sel]
                pc = p[idx]
                Wc = int(W[sel].max())
                phi = np.mod(np.arctan2(pc[:, 1], pc[:, 0]), 2 * np.pi)
                if Wc >= segs:
                    ks = np.broadcast_to(np.arange(segs + 1), (len(idx), segs + 1))
                else:
                    klo = np.floor((phi - am[sel]) / delta).astype(np.int64)
                    ks = klo[:, None] + np.arange(Wc + 1)
                t = ks * delta
                nodes = np.stack([r * np.cos(t), r * np.sin(t), np.full(t.shape, mz)], axis=-1)
                dist = _seg_dist(pc[:, None, :], nodes[:, :-1], nodes[:, 1:]).min(axis=1)
                d[idx] = np.minimum(d[idx], dist)

    def _port_dist(self, p, reach, d):
        for j, mz in enumerate(self.ring_z):
            a = np.array([self.ring_r[j], 0.0, mz])
            b = np.array([self.port_ends[j], 0.0, mz])
            d[:] = np.minimum(d, _seg_dist(p, a, b))

    # ------------------------------------------------------------------ combined
    def solid_sdf(self, points, max_distance_mm: float | None = None) -> np.ndarray:
        """Signed value of (shell minus coolant); negative inside material. Lower bound on distance to its boundary."""
        return np.maximum(self.shell_sdf_lower(points), -self.coolant_sdf(points, max_distance_mm))

    def wall_clearance(self, points, max_distance_mm: float | None = None) -> np.ndarray:
        """Lower bound of distance from a point to the nearest boundary of the material (positive = inside material)."""
        return -self.solid_sdf(points, max_distance_mm)

    def classify(self, points, tolerance_mm: float | None = None) -> np.ndarray:
        """OUTSIDE / SOLID / COOLANT, or UNCERTAIN within tolerance of any modelled surface."""
        tol = self.surface_tolerance_mm if tolerance_mm is None else float(tolerance_mm)
        p = _pts(points, self.budget)
        sh = self.shell_sdf_lower(p)
        cs = self.coolant_sdf(p, max(tol, self.default_max_distance_mm))
        out = np.where(sh < 0, np.where(cs < 0, COOLANT, SOLID), OUTSIDE).astype(np.int8)
        # ambiguous = material-vs-empty could flip within tolerance (void-vs-void, e.g. a port mouth, is not)
        near = ((np.abs(sh) <= tol) & (cs > -tol)) | ((np.abs(cs) <= tol) & (sh < tol))
        return np.where(near, UNCERTAIN, out).astype(np.int8)

    # ------------------------------------------------------------------ coverage samples
    def coverage_gap_mm(self) -> float:
        """Default centerline sample gap: one voxel (so samples resolve what a voxel grid can resolve)."""
        return self.v

    def centerline_samples(self, max_gap_mm: float | None = None, batch: int = 20000
                           ) -> Iterator[tuple[str, np.ndarray, float]]:
        """Yield (family, points(n,3), radius). Every segment is subdivided into ceil(len/gap) pieces, so the
        samples cover the whole centerline with spacing <= max_gap (error bound: max_gap/2 along the line)."""
        gap = self.coverage_gap_mm() if max_gap_mm is None else float(max_gap_mm)
        if not gap > 0:
            raise ValueError('max_gap_mm must be > 0')
        # Per-segment grids do not change with segment batch size.
        for fam, a, b, rad in self.iter_coolant_segments(batch):
            self.budget.memory(len(a) * 512)
            ln = np.linalg.norm(b - a, axis=1)
            for i in range(len(a)):
                m = max(1, int(np.ceil(float(ln[i]) / gap)))
                self.budget.check('sample_batch_budget', m + 1)
                self.budget.spend('sample_budget', m + 1)
                self.budget.memory(len(a) * 512 + (m + 1) * 192)
                t = (np.arange(m + 1) / m)[:, None]
                yield fam, a[i] * (1 - t) + b[i] * t, rad

    def coverage_error_bound_mm(self, max_gap_mm: float | None = None) -> float:
        """Max distance from any centerline point to its nearest sample, for the gap used."""
        gap = self.coverage_gap_mm() if max_gap_mm is None else float(max_gap_mm)
        return gap / 2

    def min_wall_lower_bound_mm(self) -> float:
        """Spec-derived lower bound on cold/hot wall normal thickness (same rule as the spec validator)."""
        s = self.spec
        return (s.wall_thickness_mm / 2 - s.channel_radius_mm) * math.cos(math.atan(s.max_slope))


def _pts(points, budget=None) -> np.ndarray:
    if budget is not None:
        budget.memory(max(1, len(points)) * 2048)
    p = np.asarray(points, dtype=np.float64)
    if p.ndim == 1:
        p = p[None, :]
    if p.ndim != 2 or p.shape[1] != 3:
        raise ValueError('points must have shape (n, 3)')
    return p
