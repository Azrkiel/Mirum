"""Bounded joint contract for the mate-joint assembler.

Pure math (stdlib only): imported by the sandbox-allowlisted ``cad_utils``
placement helper and by the trusted joint verifier, so it must stay free of
I/O, process, or network imports. numpy is imported lazily, and only by the
freedom analysis that runs in trusted code.

Frames
------
A joint connects a planar anchor face on the source part (frame A) to a planar
anchor face on the target part (frame B). Each frame is the face's area
centroid ``p`` and outward unit normal ``n``. The source part is the
reference body: the base part, or the part placed before it in the joint tree.

Nominal assembly relation (all supported joints)
------------------------------------------------
* ``n_B = -n_A`` (faces oppose each other),
* ``p_B = p_A + clearance * n_A`` (coaxial; ``clearance`` is the axial face
  gap along ``n_A`` in mm; never a radial or diametral fit),
* relative rotation convention: ``R_B = R_A * Q`` with ``Q`` the minimal
  rotation taking ``n_B`` (target-local) onto ``-n_A`` (source-local). Parts
  therefore keep their modelled orientation except for the rotation needed
  to oppose the faces. When the two local normals are equal, ``Q`` is a
  half-turn about a deterministic axis (``flip_axis``).

Intended relative freedom (what the joint leaves free about frame A)
--------------------------------------------------------------------
=========== ===================================================== =========
Joint       Free relative motion                                  Residuals
=========== ===================================================== =========
FASTENED    none                                                  tilt, spin, axial, radial
REVOLUTE    rotation about n_A through p_A                       tilt, axial, radial
CYLINDRICAL rotation about and translation along n_A             tilt, radial
PLANAR      both in-plane translations, rotation about n_A       tilt, axial
BALL        three rotations about p_A (clearance must be 0)      point
=========== ===================================================== =========

Unsupported (fail closed): SLIDER (two opposed planar faces do not define
the slide direction), and the coupling types GEAR, SCREW, RACK_PINION, CAM
(metadata only; no geometric relation is enforced).

The delivered GLB/STEP is one static pose. Joint freedom is kinematic intent
and is recorded separately; it is checked here by residual rank analysis, not
by the exchange format.
"""
from __future__ import annotations

import math

SUPPORTED_JOINTS: dict[str, dict] = {
    "FASTENED": {"residuals": ("tilt", "spin", "axial", "radial"), "free": ()},
    "REVOLUTE": {"residuals": ("tilt", "axial", "radial"), "free": ("rotate_axis",)},
    "CYLINDRICAL": {"residuals": ("tilt", "radial"), "free": ("rotate_axis", "translate_axis")},
    "PLANAR": {"residuals": ("tilt", "axial"),
               "free": ("rotate_axis", "translate_u", "translate_v")},
    "BALL": {"residuals": ("point",), "free": ("rotate_x", "rotate_y", "rotate_z")},
}

UNSUPPORTED_JOINTS: dict[str, str] = {
    "SLIDER": "two opposed planar anchor faces do not define the slide direction",
    "GEAR": "coupling metadata only; no geometric relation is enforced",
    "SCREW": "coupling metadata only; no geometric relation is enforced",
    "RACK_PINION": "coupling metadata only; no geometric relation is enforced",
    "CAM": "coupling metadata only; no geometric relation is enforced",
}

FREE_MOTION_LABELS = {
    "rotate_axis": "rotation about the joint axis (anchor normal) through the anchor centre",
    "translate_axis": "translation along the joint axis",
    "translate_u": "translation in the contact plane (u)",
    "translate_v": "translation in the contact plane (v)",
    "rotate_x": "rotation about world X through the joint point",
    "rotate_y": "rotation about world Y through the joint point",
    "rotate_z": "rotation about world Z through the joint point",
}


def require_supported(joint: str) -> dict:
    """Return the contract for ``joint`` or raise ValueError (fail closed)."""
    if joint in SUPPORTED_JOINTS:
        return SUPPORTED_JOINTS[joint]
    reason = UNSUPPORTED_JOINTS.get(joint, "unknown joint type")
    raise ValueError(f"Unsupported joint type {joint!r}: {reason}.")


def tolerances(length_scale: float) -> dict[str, float]:
    """Scale-aware acceptance budgets.

    ``length_scale`` is the larger bounding-box diagonal of the two bodies.
    Placement is closed-form, so errors are floating-point/STEP round-off
    (~1e-11 relative); the budgets leave several decades of margin while
    staying far below any physical fit.
    """
    scale = max(float(length_scale), 1.0)
    return {"linear_mm": 1e-6 * scale, "angular_rad": 1e-7, "rank_rel": 1e-6}


# ---------------------------------------------------------------------------
# Small 3-vector / 3x3 helpers (tuples, row-major matrices)
# ---------------------------------------------------------------------------

def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def scale(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def norm(a):
    return math.sqrt(dot(a, a))


def unit(a):
    length = norm(a)
    if not math.isfinite(length) or length < 1e-12:
        raise ValueError("Cannot normalise a zero or non-finite vector.")
    return scale(a, 1.0 / length)


IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def mat_vec(m, v):
    return tuple(dot(row, v) for row in m)


def mat_mul(a, b):
    cols = tuple(zip(*b))
    return tuple(tuple(dot(row, col) for col in cols) for row in a)


def transpose(m):
    return tuple(tuple(col) for col in zip(*m))


def axis_angle_matrix(axis, angle):
    """Rodrigues rotation matrix for a unit ``axis`` and ``angle`` (rad)."""
    x, y, z = unit(axis)
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


def matrix_axis_angle(m):
    """Inverse of :func:`axis_angle_matrix`: (unit axis, angle in [0, pi]).

    atan2 of the antisymmetric and trace parts stays well conditioned at both
    0 and pi (acos of the trace alone loses ~1e-8 rad near a half-turn).
    """
    w = (m[2][1] - m[1][2], m[0][2] - m[2][0], m[1][0] - m[0][1])
    s = norm(w) / 2.0
    c = (m[0][0] + m[1][1] + m[2][2] - 1.0) / 2.0
    angle = math.atan2(s, c)
    if angle < 1e-12:
        return (0.0, 0.0, 1.0), 0.0
    if s > 1e-6 or c > 0.0:
        return unit(w), angle
    # Near a half-turn the antisymmetric part vanishes: R = cI + (1-c) a a^T
    # (+ a negligible skew term), so read the axis from the symmetric part.
    sq = [max(0.0, (m[i][i] - c) / (1.0 - c)) for i in range(3)]
    k = max(range(3), key=lambda i: sq[i])
    ak = math.sqrt(sq[k])
    axis = [0.0, 0.0, 0.0]
    for j in range(3):
        axis[j] = ak if j == k else (m[k][j] + m[j][k]) / (2.0 * (1.0 - c) * ak)
    axis = unit(tuple(axis))
    if dot(axis, w) < 0:
        axis = scale(axis, -1.0)
    return axis, angle


def least_aligned_basis(n):
    """World basis vector least aligned with ``n``.

    Near-ties (within 1e-9, e.g. 0 vs 1e-17 normal noise after a STEP round
    trip) resolve to the lowest index, so placement and re-measurement pick
    the same half-turn axis.
    """
    comps = [abs(n[0]), abs(n[1]), abs(n[2])]
    smallest = min(comps)
    k = next(i for i, c in enumerate(comps) if c <= smallest + 1e-9)
    return tuple(1.0 if i == k else 0.0 for i in range(3))


def plane_basis(n):
    """Deterministic orthonormal (u, v) spanning the plane normal to ``n``."""
    u = unit(cross(n, least_aligned_basis(n)))
    return u, cross(n, u)


def align_rotation(n_from, n_to):
    """Minimal rotation taking unit ``n_from`` onto unit ``n_to``.

    Returns (matrix, flip_axis); flip_axis is None unless the vectors are
    opposite, in which case the half-turn axis is chosen deterministically.
    """
    a, b = unit(n_from), unit(n_to)
    c = dot(a, b)
    if c > 1.0 - 1e-15:
        return IDENTITY, None
    if c < -1.0 + 1e-12:
        axis = unit(cross(a, least_aligned_basis(a)))
        return axis_angle_matrix(axis, math.pi), axis
    axis = cross(a, b)
    return axis_angle_matrix(axis, math.atan2(norm(axis), c)), None


# ---------------------------------------------------------------------------
# Placement
# ---------------------------------------------------------------------------

def place_child(parent_rotation, parent_translation, parent_frame, child_frame, clearance):
    """Pose of a child body that satisfies the nominal joint relation.

    ``parent_frame`` / ``child_frame`` are (origin, normal) in each body's
    local coordinates. Returns (rotation, translation, flip_axis) with the
    child's world point x_w = R x_local + t.
    """
    p_local, n_local = parent_frame
    c_local, m_local = child_frame
    q, flip = align_rotation(m_local, scale(n_local, -1.0))
    rotation = mat_mul(parent_rotation, q)
    p_world = add(mat_vec(parent_rotation, p_local), parent_translation)
    n_world = unit(mat_vec(parent_rotation, n_local))
    target = add(p_world, scale(n_world, clearance))
    translation = sub(target, mat_vec(rotation, c_local))
    return rotation, translation, flip


# ---------------------------------------------------------------------------
# Residuals (world-frame measurements)
# ---------------------------------------------------------------------------

def joint_residuals(joint, a, b, clearance, spin_pair=None):
    """Residual vector for ``joint`` given measured world frames.

    ``a``/``b`` are (origin, unit normal) of the source/target anchor faces.
    ``spin_pair`` = (expected, actual) world unit vectors in the contact plane
    for the FASTENED spin lock: the image of a target-local reference
    direction under the conventional relative rotation, and under the
    delivered one. Linear residuals are in mm, angular ones dimensionless
    (sine of the error angle).
    """
    spec = require_supported(joint)
    pa, na = a
    pb, nb = b
    u, v = plane_basis(na)
    d = sub(pb, pa)
    out: dict[str, float] = {}
    for kind in spec["residuals"]:
        if kind == "tilt":
            s = add(na, nb)
            out["tilt_u"], out["tilt_v"] = dot(s, u), dot(s, v)
        elif kind == "spin":
            if spin_pair is None:
                raise ValueError("FASTENED spin residual needs a reference direction pair.")
            expected, actual = spin_pair
            out["spin"] = dot(cross(expected, actual), na)
        elif kind == "axial":
            out["axial"] = dot(d, na) - clearance
        elif kind == "radial":
            out["radial_u"], out["radial_v"] = dot(d, u), dot(d, v)
        elif kind == "point":
            e = sub(d, scale(na, clearance))
            out["point_x"], out["point_y"], out["point_z"] = e
    return out


def residuals_within(residuals, tol):
    """True if every linear residual <= linear_mm and angular <= angular_rad."""
    for key, value in residuals.items():
        if not math.isfinite(value):
            return False
        limit = tol["angular_rad"] if key.startswith(("tilt", "spin")) else tol["linear_mm"]
        if abs(value) > limit:
            return False
    return True


# ---------------------------------------------------------------------------
# Freedom analysis (trusted code only; uses numpy lazily)
# ---------------------------------------------------------------------------

def expected_free_twists(joint, na):
    """Basis of the intended free relative motions as 6-vectors (w, t)."""
    u, v = plane_basis(na)
    zero = (0.0, 0.0, 0.0)
    basis = {
        "rotate_axis": na + zero,
        "translate_axis": zero + na,
        "translate_u": zero + u,
        "translate_v": zero + v,
        "rotate_x": (1.0, 0.0, 0.0) + zero,
        "rotate_y": (0.0, 1.0, 0.0) + zero,
        "rotate_z": (0.0, 0.0, 1.0) + zero,
    }
    return [basis[name] for name in require_supported(joint)["free"]]


def moved_frames(b, ref_dir, pivot, twist):
    """Apply a finite twist (w, t) about ``pivot`` to target frame and direction."""
    w, t = twist[:3], twist[3:]
    angle = norm(w)
    r = axis_angle_matrix(w, angle) if angle > 0 else IDENTITY
    pb, nb = b
    moved_p = add(add(pivot, mat_vec(r, sub(pb, pivot))), t)
    return (moved_p, mat_vec(r, nb)), mat_vec(r, ref_dir)


def freedom_analysis(joint, a, b, clearance, length_scale, expected_dir=None, actual_dir=None):
    """Numerical Jacobian of the joint residuals w.r.t. a target-body twist.

    Twist coordinates are rotations (rad) about world axes through the source
    anchor centre and translations scaled by ``length_scale``, so the rank
    threshold is dimensionless. Returns the measured constrained/free counts
    and whether the null space equals the declared free motion.
    """
    import numpy as np

    tol = tolerances(length_scale)
    scale_l = max(float(length_scale), 1.0)
    pa, na = a
    ref_dir = actual_dir if actual_dir is not None else plane_basis(b[1])[0]
    expected = expected_dir if expected_dir is not None else ref_dir

    def residual_vector(twist):
        mb, md = moved_frames(b, ref_dir, pa, twist)
        res = joint_residuals(joint, a, mb, clearance, spin_pair=(expected, md))
        return np.array([res[k] / (scale_l if not k.startswith(("tilt", "spin")) else 1.0)
                         for k in sorted(res)])

    h = 1e-6
    columns = []
    for i in range(6):
        step = [0.0] * 6
        step[i] = h if i < 3 else h * scale_l
        plus, minus = residual_vector(tuple(step)), residual_vector(tuple(-s for s in step))
        columns.append((plus - minus) / (2 * h))
    jac = np.stack(columns, axis=1)
    sv = np.linalg.svd(jac, compute_uv=False)
    rank = int(np.sum(sv > tol["rank_rel"] * max(sv.max(), 1e-300))) if sv.size else 0
    free_basis = expected_free_twists(joint, na)
    allowed_ok = True
    for twist in free_basis:
        scaled = np.array(twist[:3] + tuple(x / scale_l for x in twist[3:]))
        scaled /= np.linalg.norm(scaled)
        if np.linalg.norm(jac @ scaled) > 1e-6:
            allowed_ok = False
    expected_rank = 6 - len(free_basis)
    return {
        "basis": ("declared joint contract: residual-rank check of the implemented "
                  "constraint set at the delivered frames; not a swept-motion or "
                  "collision analysis of the free motion"),
        "constrained_dof": rank,
        "expected_constrained_dof": expected_rank,
        "free_dof": 6 - rank,
        "declared_free_motion": [FREE_MOTION_LABELS[m] for m in require_supported(joint)["free"]],
        "free_motion_matches": rank == expected_rank and allowed_ok,
        "singular_values": [float(x) for x in sv],
    }
