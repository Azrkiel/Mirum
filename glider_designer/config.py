"""
All knobs for the glider generator live here.

Units are SI (metres, kilograms, seconds, degrees for angles) unless a name
says otherwise. Change a value, re-run `python run.py`, compare results.
"""

CONFIG = {
    "seed": 42,

    # ------------------------------------------------------------------ #
    # MISSION: what the glider has to do
    # ------------------------------------------------------------------ #
    "mission": {
        # "distance": maximise glide ratio L/D  (distance = height x L/D)
        # "duration": minimise sink rate        (time aloft = height / sink)
        "objective": "distance",
        "launch_height_m": 10.0,

        # Payload carried in the fuselage pod. Defaults to a hen's egg.
        # Set payload_mass_kg to 0.0 for an empty glider.
        "payload_mass_kg": 0.060,
        "payload_diameter_m": 0.045,
        "payload_length_m": 0.060,

        "air_density_kg_m3": 1.225,
        "gravity_m_s2": 9.81,
    },

    # ------------------------------------------------------------------ #
    # MATERIALS / MASS MODEL  (defaults: hand-built foam glider)
    # ------------------------------------------------------------------ #
    "materials": {
        "wing_areal_density_kg_m2": 0.30,      # e.g. 6 mm Depron + carbon spar + tape
        "tail_areal_density_kg_m2": 0.15,      # e.g. 3 mm Depron
        "fuselage_shell_areal_density_kg_m2": 0.50,  # pod/boom skin, per m^2 of surface
        "fixed_hardware_mass_kg": 0.010,       # glue, wing hold-down, launch peg
        "wall_thickness_m": 0.003,             # used for the payload-fit check
        "wing_cg_chord_fraction": 0.42,        # where a solid foam wing balances, per chord
    },

    # ------------------------------------------------------------------ #
    # SAMPLING: how many parts to generate. Every wing x fuselage x tail
    # combination is assembled (optionally capped by max_combinations).
    # ------------------------------------------------------------------ #
    "sampling": {
        "n_wings": 12,
        "n_fuselages": 8,
        "n_tails": 10,
        "max_combinations": None,   # e.g. 300 to randomly subsample for a quick run
    },

    # ------------------------------------------------------------------ #
    # PARAMETER RANGES for the random part generators
    # (min, max) pairs are sampled uniformly; lists are sampled as choices.
    # ------------------------------------------------------------------ #
    "ranges": {
        "wing": {
            "style": ["tapered", "elliptical", "polyhedral"],
            "span_m": (0.60, 1.40),
            "root_chord_m": (0.10, 0.22),
            "taper": (0.35, 1.00),             # tip chord / root chord
            "sweep_qc_deg": (0.0, 8.0),        # quarter-chord sweep
            "dihedral_deg": (3.0, 12.0),       # effective: tip height = (span/2) tan(dihedral)
            "washout_deg": (0.0, 3.0),         # tip twisted nose-down by this much
            "incidence_deg": (1.0, 3.0),       # wing angle relative to fuselage
            "naca_camber_pct": [0, 1, 2, 3, 4],
            "naca_camber_pos": [3, 4],         # tenths of chord
            "naca_thickness_pct": [6, 7, 8, 9, 10, 12],
        },
        "fuselage": {
            "style": ["pod_boom", "torpedo", "slab"],
            "length_m": (0.45, 0.95),
            "max_diameter_m": (0.055, 0.085),
            "nose_frac": (0.06, 0.12),          # nose cone length / fuselage length
            "pod_frac": (0.28, 0.45),           # pod end / fuselage length
            "boom_radius_m": (0.005, 0.009),
            "wing_station_in_pod": (0.30, 0.70),  # wing root LE: 0 = end of nose, 1 = end of pod
        },
        "tail": {
            "kind": ["conventional", "conventional", "T", "V"],  # repeated = more likely
            "h_span_m": (0.15, 0.40),
            "h_root_chord_m": (0.04, 0.10),
            "h_taper": (0.60, 1.00),
            "v_height_m": (0.05, 0.15),
            "v_root_chord_m": (0.05, 0.11),
            "v_taper": (0.50, 0.90),
            "v_sweep_le_deg": (10.0, 40.0),
            "vt_panel_span_m": (0.10, 0.25),    # V-tail: length of each panel
            "vt_root_chord_m": (0.05, 0.10),
            "vt_taper": (0.60, 1.00),
            "vt_dihedral_deg": (30.0, 50.0),
            "airfoil": ["0006", "0008"],
        },
    },

    # ------------------------------------------------------------------ #
    # VALIDITY LIMITS: a design is rejected if it breaks any of these
    # ------------------------------------------------------------------ #
    "limits": {
        # Pitch stability. Ballast is added to the nose to hit the target.
        "static_margin_target": 0.12,
        "static_margin_min": 0.05,
        "static_margin_max": 0.20,
        "max_ballast_fraction": 0.25,           # ballast / total mass

        # Trim: the tail incidence is solved for each design.
        "tail_incidence_range_deg": (-6.0, 3.0),
        "stall_margin": 0.85,                   # trimmed CL <= this x CLmax
        "pitch_stable_below_trim_deg": 1.0,     # Cm must keep falling from trim-1 deg to trim+3 deg
        "min_trim_CL": 0.30,

        # Tail sizing (classic tail volume coefficients)
        "h_tail_volume_range": (0.30, 0.80),
        "v_tail_volume_range": (0.020, 0.060),
        "min_tail_arm_over_mac": 2.0,           # wing AC -> tail AC distance / wing MAC

        # Lateral-directional dynamics (from linearised equations of motion)
        "spiral_min_time_to_double_s": 8.0,     # a slow spiral divergence is tolerable
        "dutch_roll_min_damping": 0.05,

        # Structure / build / physics sanity
        "max_aspect_ratio": 14.0,
        "min_tip_chord_m": 0.035,
        "min_wing_tail_gap_m": 0.04,
        "t_tail_min_fin_tip_chord_frac": 0.6,   # fin tip chord / stab root chord
        "max_wing_loading_g_dm2": 15.0,
        "max_total_mass_kg": 0.50,
        "min_reynolds": 30000,
        "max_glide_speed_m_s": 9.0,             # landing speed limit (protects the egg)
    },

    # ------------------------------------------------------------------ #
    # OUTPUT
    # ------------------------------------------------------------------ #
    "export": {
        "top_k": 10,          # how many of the best designs get STL / drawings / templates
        "gallery_size": 12,   # planforms shown in the overview gallery image
        # Variety: the exported/gallery designs use each wing at most this many
        # times, so one great wing doesn't fill the whole top 10. The full ranking
        # is always in ranked_valid_designs.csv. Set to None to disable.
        "max_designs_per_wing": 2,
    },
}
