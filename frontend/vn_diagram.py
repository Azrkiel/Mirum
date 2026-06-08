"""V-n (flight-envelope) diagram builder for the Phase-6 Structural card.

Kept separate from app.py (which executes Streamlit at import) so the figure
math is unit-testable headless. Pure: takes a StructuralReport dict, returns a
matplotlib Figure.
"""

from __future__ import annotations


def vn_diagram_figure(report: dict):
    """Build a V-n diagram Figure from a StructuralReport dict.

    The maneuvering envelope is the stall parabola n=(V/Vs)^2 up to the corner
    speed V_A, flat at the limit load factor out to V_NE; gust lines overlay as
    straight lines from the 1-g point. Returns a matplotlib Figure (Agg-safe).
    """
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    vs = float(report.get("v_stall_ms") or 0.0)
    vc = float(report.get("v_corner_ms") or 0.0)
    vne = float(report.get("v_ne_ms") or 0.0)
    n_pos = float(report.get("n_maneuver_pos") or 0.0)
    n_neg = float(report.get("n_maneuver_neg") or 0.0)
    ng_pos = float(report.get("n_gust_pos") or n_pos)
    ng_neg = float(report.get("n_gust_neg") or n_neg)

    fig, ax = plt.subplots(figsize=(5.0, 3.6))
    if vs <= 0 or vne <= 0:
        ax.text(0.5, 0.5, "insufficient envelope data", ha="center", va="center")
        ax.set_axis_off()
        return fig

    n = 40
    pos_v = [vc * i / n for i in range(n + 1)] + [vne]
    pos_n = [(v / vs) ** 2 for v in pos_v[:-1]] + [n_pos]
    vc_neg = vs * (abs(n_neg) ** 0.5) if n_neg else vc
    neg_v = [vc_neg * i / n for i in range(n + 1)] + [vne]
    neg_n = [-((v / vs) ** 2) for v in neg_v[:-1]] + [n_neg]

    ax.fill(pos_v + neg_v[::-1], pos_n + neg_n[::-1],
            alpha=0.15, color="tab:blue", label="maneuver envelope")
    ax.plot(pos_v, pos_n, color="tab:blue", lw=2.0)
    ax.plot(neg_v, neg_n, color="tab:blue", lw=2.0)
    ax.plot([vne, vne], [n_neg, n_pos], color="tab:blue", lw=2.0)  # V_NE wall

    ax.plot([0, vne], [1.0, ng_pos], "--", color="tab:red", lw=1.0, label="gust")
    ax.plot([0, vne], [1.0, ng_neg], "--", color="tab:red", lw=1.0)

    ax.axhline(0, color="black", lw=0.6)
    y_top = ax.get_ylim()[1]
    for v, lbl in [(vs, "V_s"), (vc, "V_A"), (vne, "V_NE")]:
        if v > 0:
            ax.axvline(v, color="gray", ls=":", lw=0.8)
            ax.text(v, y_top, f" {lbl}", fontsize=8, va="top", color="gray")

    ax.set_xlabel("Airspeed V (m/s)")
    ax.set_ylabel("Load factor n")
    ax.set_title("Flight Envelope (V-n)")
    ax.legend(loc="lower right", fontsize=7)
    ax.grid(True, alpha=0.25)
    fig.tight_layout()
    return fig
