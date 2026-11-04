# Rank 2: W12-F06-T07

## Performance (predicted, still air)
- Glide ratio L/D: **13.3**  (distance from 10 m: 133 m)
- Sink rate: 0.44 m/s  (time aloft: 22.5 s)
- Glide / landing speed: 5.9 m/s, landing energy 2.78 J
- Trim: alpha 4.0 deg, CL 0.49, Reynolds 44585

## Build and balance
- Total mass: 158 g  (wing 45 g, htail 2 g, vtail 1 g, fuselage 40 g, hardware 10 g, payload 60 g)
- Nose ballast: none needed
- **Payload centre: 158 mm from the nose tip** (60 g)
- **Balance point (CG): 203 mm from the nose tip** (neutral point 217 mm, static margin 12% MAC)
- **Stabiliser incidence: -0.90 deg** relative to the fuselage (wing at +1.60 deg, decalage 2.50 deg)
- **Wing leading edge at 141 mm from the nose tip**, on top of the fuselage

## Stability
- Static margin 12% MAC, Cm_alpha -0.63/rad
- Cl_beta -0.264 (dihedral effect), Cn_beta 0.007 (weathercock)
- Dutch roll damping ratio 0.35; spiral stable
- Tail volumes: horizontal 0.45, vertical 0.022

## Parts
- Wing W12: polyhedral, span 1301 mm, root chord 132 mm, taper 0.68, sweep 1.4 deg, dihedral 11.2 deg, washout 2.6 deg, NACA 1406, area 1487 cm^2, AR 12.3
- Fuselage F06: slab, length 775 mm, max diameter 68 mm
- Tail T07: conventional, stab span 337 mm x chord 43 mm, fin height 100 mm x chord 101 mm, NACA 0006

## Files
- `assembly.stl`, `part_*.stl`: meshes in millimetres (x aft, y right, z up)
- `template_*.svg`: 1:1 cutting templates (print at 100%, check the 100 mm bar)
- `three_view.png`: drawing with CG (red dot) and neutral point (blue x)

Trim-glide test: launch gently level over grass. Dives -> add a little positive stab incidence (or remove ballast); stalls/porpoises -> add nose ballast. Turns -> check for warps first.
