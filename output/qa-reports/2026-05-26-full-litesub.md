| id | domain | difficulty | status | lvm | features | time_ms | regression_for | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A_box_with_corner_holes | A | easy | pass | 5.0 | 4/4 | 12890 |  |  |
| A_chamfered_cube | A | easy | pass | 5.0 | 3/3 | 10180 |  |  |
| A_circular_flange_pattern | A | medium | pass | 5.0 | 5/5 | 14950 |  |  |
| A_csv_apartment_building | A | medium | pass | 5.0 | 5/5 | 82080 | C | User-feedback CSV (4/30/2026): planner returned a single 5x10x100 cq.box. Worktree C's planner decomposition floor must require >= 3 parts for architectural ... |
| A_csv_bolt_threads_timeout | A | medium | pass | 4.7 | 5/5 | 16200 | A | User-feedback CSV (4/30/2026): 'Could not reach backend ... read timeout=180'. Rated 1. Tagged A; once Worktree A makes this reach Domain B, the threads shou... |
| A_csv_bus_driver_figurine | A | medium | pass | 4.0 | 5/5 | 61780 | A | User-feedback CSV (4/30/2026): 'A soulful bus driver figurine ... result just wouldn't load. Connection error.' Rated 2. Worktree A fix. |
| A_csv_cube_no_load | A | easy | pass | 5.0 | 1/1 | 7160 | A | User-feedback CSV (4/30/2026): pipeline never returned for this prompt. Worktree A (job queue + worker scaling) should make this complete < 30s reliably. |
| A_csv_dog_cupcake | A | medium | pass | 4.7 | 4/4 | 42060 | A | User-feedback CSV (4/30/2026): user-prompt 'a dog ... cupcake ... scary pitbull wearing a bow' ran 15 minutes and failed. Captured here as an A regression be... |
| A_csv_mouse_shell_timeout | A | medium | pass | 5.0 | 4/4 | 41290 | A | User-feedback CSV (4/30/2026): 'Could not reach backend ... read timeout=180'. Rated 1. A separate organic-shell topology case is captured by C_csv_spoon_fun... |
| A_csv_simple_circle | A | easy | pass | 5.0 | 3/3 | 8980 | A | User-feedback CSV (4/30/2026): multiple users reported 'a circle' / 'a blue circle' / '3D Circle' prompts buffering and never returning, OR returning 'Could ... |
| A_csv_simple_square | A | easy | pass | 4.0 | 2/2 | 11560 | A | User-feedback CSV (4/30/2026): 'Build a square' / 'Draw a square' buffered then died with read-timeout. Worktree A fix. |
| A_csv_smooth_sphere_3in | A | easy | pass | 5.0 | 3/3 | 11640 | A | User-feedback CSV (4/30/2026): 'It refused to build it, just would not generate.' Rated 2. Captured as A since the failure was a refusal/timeout; once the pi... |
| A_l_bracket | A | easy | pass | 5.0 | 6/6 | 37540 |  |  |
| A_simple_plate | A | easy | pass | 4.0 | 1/2 | 12030 |  |  |
| A_slotted_bracket | A | medium | pass | 5.0 | 4/4 | 19150 |  |  |
| A_stepped_block | A | medium | pass | 5.0 | 3/3 | 13810 |  |  |
| B_bearing_housing | B | medium | pass | 5.0 | 4/4 | 37450 |  |  |
| B_csv_iphone_case | B | hard | pass | 5.0 | 8/8 | 41350 | B | User-feedback CSV (4/30/2026): TypeError: Workplane.rect() got an unexpected keyword argument 'rx'. Worktree B's cq_lint must reject this kwarg pre-execution... |
| B_csv_passenger_car_shell | B | hard | pass | 4.7 | 5/5 | 73760 | B | User-feedback CSV (4/30/2026): prompt 'Generate a 3D model of 6 and 7' produced a multi-part train decomposition; passenger car body failed with OCP.StdFail.... |
| B_eccentric_cam | B | hard | pass | 5.0 | 6/6 | 54610 |  |  |
| B_hex_bolt_m10 | B | easy | pass | 4.0 | 4/5 | 12640 |  |  |
| B_keyed_shaft | B | easy | pass | 5.0 | 6/6 | 37740 |  |  |
| B_spur_gear | B | easy | pass | 4.7 | 5/5 | 13350 |  |  |
| B_threaded_rod | B | easy | pass | 5.0 | 4/4 | 26910 |  |  |
| B_v_belt_pulley | B | medium | pass | 5.0 | 6/6 | 44640 |  |  |
| C_blended_chair_leg | C | hard | fail |  |  | 104520 |  | OCP.StdFail.StdFail_NotDone: BRep_API: command not done |
| C_csv_baseball_stitches | C | hard | pass | 4.3 | 3/4 | 126010 | E,D | User-feedback CSV (4/28/2026): system produced a long threaded tube instead of a stitched sphere because the Classifier force-fit into Domain B (mechanical/t... |
| C_csv_baseball_wires_item | C | hard | pass | 3.3 | 3/4 | 88960 | B,E | User-feedback CSV (4/28/2026): IndexError on cq.Workplane('XY').spline(...).wires().item(0). Worktree B's cq_lint should warn on .item(N) without bounds chec... |
| C_csv_cat_20m | C | medium | pass | 4.7 | 4/4 | 46020 | C | User-feedback CSV (4/27/2026): planner produced 19 parts at 10000mm each, several OCCT failures including BSplCLib::Interpolate on the head. Worktree C's REA... |
| C_csv_cat_head_spline | C | hard | pass | 5.0 | 5/5 | 56800 | B | User-feedback CSV (4/27/2026): Standard_OutOfRange: BSplCLib::Interpolate on a 24-point periodic spline. Worktree B's cq_lint must reject periodic splines wi... |
| C_csv_cat_leg_cut | C | medium | pass | 4.7 | 4/4 | 38400 | B | User-feedback CSV (4/28/2026): ValueError: Cannot find a solid on the stack or in the parent chain after .cut(). Worktree B should warn the LLM about losing ... |
| C_csv_gremlin_chair | C | hard | pass | 3.7 | 2/4 | 72910 | D | User-feedback CSV (4/30/2026): user said 'it unfortunately looks not very close to the gremlin'. Worktree D's vision verifier should catch wrong silhouette /... |
| C_csv_pacman | C | medium | pass | 5.0 | 4/4 | 27960 | D | User-feedback CSV (4/30/2026): user said depth perception was off, 'like it looked at a 2d picture'. Worktree D's multi-view render must catch the missing de... |
| C_csv_spoon_funnel | C | hard | pass | 4.7 | 5/5 | 94610 | D | User-feedback CSV (4/29/2026): user said it 'looked more as a long funnel'. Worktree D's rendered-image LVM must catch the missing concave bowl and trigger a... |
| C_curved_swan_neck | C | hard | pass | 5.0 | 6/6 | 39450 |  |  |
| C_drop_pendant | C | easy | pass | 4.8 | 5/5 | 51710 |  |  |
| C_ergonomic_handgrip | C | medium | pass | 5.0 | 5/5 | 23840 |  |  |
| C_lofted_perfume_bottle | C | easy | pass | 5.0 | 5/5 | 17510 |  |  |
| C_organic_drawer_pull | C | hard | pass | 5.0 | 6/6 | 55490 |  |  |
| C_round_to_square_transition | C | medium | pass | 5.0 | 6/6 | 57250 |  |  |
| C_table_lamp_base | C | easy | pass | 5.0 | 5/5 | 40430 |  |  |
| C_tapered_handle | C | easy | pass | 5.0 | 3/3 | 22760 |  |  |
| C_teardrop_pendant | C | medium | pass | 4.3 | 5/5 | 132550 |  |  |
| C_three_lobe_doorknob | C | medium | fail |  |  | 102500 |  | ValueError: Nothing to loft |
| C_water_bottle_grip | C | medium | fail |  |  | 80020 |  | ValueError: Nothing to loft |
| D_aero_sweep_naca0012 | D | hard | pass | 3.3 | 6/8 | 40110 | D | Primary XFOIL integration test — validates the full aero pipeline: generate wing geometry, run XFOIL, return PolarReport. Stall should be ≥14 alpha-points co... |
| D_canard_surface | D | easy | pass | 4.0 | 4/4 | 20300 | F |  |
| D_csv_f14_jet | D | hard | pass | 4.0 | 6/6 | 71260 | A,C | User-feedback CSV (4/30/2026): planning ran for 5 minutes then 'Could not connect to backend (Connection error)'. Worktree A (move planning off the synchrono... |
| D_delta_wing_naca0006 | D | medium | pass | 4.0 | 5/6 | 23920 | F |  |
| D_dihedral_wing | D | medium | pass | 3.7 | 5/5 | 33940 | F |  |
| D_dihedral_wing_panel | D | hard | pass | 3.3 | 5/6 | 22840 | D |  |
| D_fuselage_cross_section | D | medium | pass | 4.0 | 5/5 | 22610 | D | Tests that the Domain D strategy correctly handles non-wing applications of NACA profiles (fuselage body of revolution). Large span-to-chord ratio stresses t... |
| D_horizontal_stabilizer | D | medium | pass | 3.3 | 7/8 | 20400 | D |  |
| D_multipart_wing_assembly | D | hard | pass | 3.3 | 6/8 | 25010 | D | Multi-part Domain D path — tests that Planner decomposes wing into skin + ribs + spar and that the assembler applies FASTENED mates correctly. |
| D_naca0012_segment | D | easy | pass | 3.8 | 4/4 | 18700 | D |  |
| D_naca2412_with_ribs | D | hard | pass | 3.3 | 5/6 | 23100 | D |  |
| D_naca4412_segment | D | easy | pass | 4.0 | 5/5 | 19370 | D |  |
| D_propeller_2blade | D | medium | pass | 4.3 | 5/6 | 17290 | F |  |
| D_propeller_3blade | D | hard | pass | 4.7 | 6/6 | 28810 | F |  |
| D_rotor_blade | D | hard | pass | 3.7 | 4/5 | 14030 | F |  |
| D_swept_dihedral_combo | D | hard | pass | 5.0 | 6/6 | 48620 | F |  |
| D_swept_stabilizer | D | medium | pass | 4.0 | 5/5 | 42430 | F |  |
| D_swept_wing_35deg | D | medium | pass | 3.9 | 6/6 | 39670 | F |  |
| D_swept_winglet | D | medium | pass | 3.7 | 6/6 | 18110 | D |  |
| D_tapered_fin | D | medium | pass | 4.0 | 6/6 | 25290 | D |  |
| D_wind_turbine_blade | D | hard | pass | 4.0 | 3/5 | 16890 | F |  |
| E_enclosure_panel | E | medium | pass | 5.0 | 6/6 | 30910 |  |  |
| E_l_bracket_sheet | E | easy | pass | 4.3 | 5/6 | 51220 |  |  |
| E_pcb_tray | E | medium | fail |  |  | 263850 |  | AttributeError: 'BoundBox' object has no attribute 'xMax'. Did you mean: 'xmax'? |
| F_afterburner_duct | F | easy | pass | 3.0 | 3/5 | 16160 | F |  |
| F_chamber_with_dome | F | medium | pass | 3.3 | 3/5 | 9970 | F |  |
| F_combustion_chamber | F | medium | pass | 5.0 | 6/6 | 43080 | F |  |
| F_nacelle_cowl_large | F | hard | pass | 2.7 | 4/6 | 13410 | F |  |
| F_nacelle_inlet | F | medium | pass | 4.0 | 6/6 | 10600 | F |  |
| F_nozzle_basic | F | easy | pass | 3.7 | 6/6 | 13070 | F |  |
| F_nozzle_steep_angle | F | medium | pass | 4.0 | 4/5 | 11110 | F |  |
| F_nozzle_with_chamber | F | hard | pass | 4.0 | 4/5 | 98090 | F |  |
| G_fuselage_simple | G | medium | pass | 4.3 | 6/6 | 20570 | G |  |
| G_fuselage_tapered | G | hard | pass | 4.0 | 5/6 | 31750 | G |  |
| G_nose_cone_haack | G | medium | pass | 4.0 | 4/5 | 18380 | G |  |
| G_nose_cone_ogive | G | easy | pass | 4.0 | 6/6 | 19570 | G |  |
| G_pod_body | G | medium | pass | 4.0 | 5/5 | 22720 | G |  |
