# Glider Designer

> Standalone tool on the `glider-designer` branch. It does not use or modify
> the Mirum backend pipeline. Run everything from inside this folder.
> `results/` holds a complete sample run (default `config.py`, seed 42).

Generates interchangeable **wings, fuselages and tails**, mates every
combination, throws out anything that would not fly hands-off, and ranks the
rest. The best designs come out as STL files, 1:1 cutting templates, a
three-view drawing and a build sheet that tells you where to put the payload
and ballast and how to set the stabiliser.

It is built for **unpowered gliders with no moving control surfaces**, so a
design only passes if it is stable on its own in pitch, roll and yaw.

## Quick start

```bash
pip install -r requirements.txt
python run.py                      # about 10 min on a 2-core laptop with the defaults
python -m pytest -q                # check the maths (about 5 s)
```

Faster first try: `python run.py --wings 6 --fuselages 4 --tails 5 --top 5`

Useful options:

| Option | What it does |
|---|---|
| `--objective duration` | rank by time aloft (lowest sink rate) instead of distance (best L/D) |
| `--max-combos 300` | randomly test only 300 combinations |
| `--seed 7` | a different random set of parts |
| `--workers 4` | use more CPU cores |
| `--export-only` | re-make drawings/STLs from a finished run without re-analysing |

Everything else (mission, payload, materials, part ranges, pass/fail limits)
is in **`config.py`**, with comments.

## What comes out (`results/`)

| File | Contents |
|---|---|
| `summary.txt` | how many passed, **why the others failed**, which tail types / wing styles / parts did best, top 10 |
| `top_designs_gallery.png` | the best designs side by side, seen from above, same scale |
| `ranked_valid_designs.csv` | every flyable design, best first |
| `all_designs.csv` | every combination, with the reasons each failure was rejected |
| `parts_*.csv` | the generated wings, fuselages and tails |
| `top_designs/rankNN_W..-F..-T../` | per design: `README.md` build sheet, `three_view.png`, `assembly.stl` + `part_*.stl` (mm), `template_*.svg` 1:1 cutting templates, `design.json` |

Templates are often bigger than a sheet of paper: print them "poster/tiled" at
100 % and check the 100 mm scale bar, or send them straight to a laser cutter.

## How it works

1. **Generate parts** (`components.py`). Each part is parametric and built in
   its own frame around a shared **mounting interface**: the fuselage exposes
   a wing saddle over its pod and a tail mount at the end of its boom. That is
   what lets any wing go on any fuselage with any tail.
   - Wings: tapered, elliptical or polyhedral; span, chord, taper, sweep,
     dihedral, washout, incidence, NACA 4-digit airfoil
   - Fuselages: pod-and-boom, torpedo or slab-sided; length, diameter, nose,
     pod length, boom size
   - Tails: conventional, T or V; areas, aspect ratios, sweep, V angle
2. **Build checks** (`analysis.geometry_checks`): tail volume coefficients,
   tail arm, aspect ratio (structure), tip chord, parts overlapping, T-tail
   mountable, payload fits in the pod, total mass.
3. **Balance** (`assembly.balance`, `analysis.balance_with_wing_slide`). Like a
   real model with a rubber-band wing mount: slide the wing along the saddle,
   slide the payload in its bay, and only then add nose ballast, so that the
   CG sits at the target **static margin** (12 % of the mean chord by default).
4. **Trim** (`analysis.trim`). Pick the angle of attack that gives the best
   L/D (or lowest sink), then solve the **stabiliser incidence (decalage)** so
   the pitching moment about the CG is zero there. That is the "set and forget"
   setting you build in.
5. **Stability**: static margin, pitch moment slope around trim, Cl_beta
   (dihedral effect), Cn_beta (weathercock), then the **eigenvalues of the
   linearised lateral equations of motion** for the roll, spiral and Dutch roll
   modes.
6. **Performance**: L/D, sink rate, glide speed, Reynolds number, distance and
   time from the launch height, and landing energy (for the egg).
7. **Rank and export** the survivors (`run.py`, `export.py`). By default the
   exported top designs use each wing at most twice, for variety.

### Aerodynamics

All aerodynamics come from [AeroSandbox](https://github.com/peterdsharpe/AeroSandbox):

- **Performance and trim:** AeroBuildup with NeuralFoil airfoil data, which
  handles the low Reynolds numbers (30k-100k) of small gliders.
- **Neutral point and lateral/rate derivatives:** vortex-lattice method on
  the wings and tails, plus fuselage increments from AeroBuildup. The
  vortex-lattice method is used because NeuralFoil's low-Reynolds lift curves
  are wiggly, and tiny finite-difference steps on them give noisy stability
  numbers.

## Accuracy and limits (read before trusting a ranking)

- This is a **screening tool**. Its job is to sort plausible designs from
  impossible ones and to point you at promising regions. The differences
  between the top few designs (for example L/D 13.2 vs 13.0) are within model
  error. Build several, including some lower-ranked ones, and see whether the
  ranking holds up in real flights.
- **Mass model:** areal densities for wing, tail and fuselage skin
  (`config.py` → `materials`). Weigh your real parts and update these numbers.
  The balance point matters more than anything else.
- **Low-Reynolds aerodynamics are uncertain.** The check "loses pitch
  stability just below trim angle" catches airfoils whose moment curve kinks
  at low angle in NeuralFoil's model. It may be conservative.
- The lateral dynamics ignore the product of inertia Ixz and use simple mass
  estimates. Treat the results as pass/fail screening, not as precise
  frequencies.
- **Not modelled:**
  - structural strength and stiffness beyond the aspect-ratio and
    tip-chord limits
  - launch loads, gusts, ground effect and wing flex
- **Before building, check the winners** in XFLR5 or AVL (AeroSandbox can
  export to both: `airplane.export_XFLR5_xml(...)`, `airplane.export_AVL(...)`).
  Then trim-glide test over grass.

## Changing what "good" means

- Different payload or none: `mission.payload_*` (set the mass to 0 for an
  empty glider).
- Gentler landings for the egg: lower `limits.max_glide_speed_m_s`.
- Different materials: `materials`.
- More exotic shapes: widen `ranges`. The checks will reject the ones that
  don't work, and `summary.txt` says why.
- New part families: add a `style` in `components.py`. Keep the mounting
  interface and every check still applies.
