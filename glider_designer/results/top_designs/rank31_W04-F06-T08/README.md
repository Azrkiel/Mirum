# Rank 31: W04-F06-T08

## Performance (predicted, still air)
- Glide ratio L/D: **12.4**  (distance from 10 m: 124 m)
- Sink rate: 0.46 m/s  (time aloft: 21.6 s)
- Glide / landing speed: 5.8 m/s, landing energy 2.42 J
- Trim: alpha 3.5 deg, CL 0.64, Reynolds 43716

## Build and balance
- Total mass: 146 g  (wing 33 g, htail 1 g, vtail 1 g, fuselage 40 g, hardware 10 g, payload 60 g)
- Nose ballast: none needed
- **Payload centre: 113 mm from the nose tip** (60 g)
- **Balance point (CG): 179 mm from the nose tip** (neutral point 193 mm, static margin 12% MAC)
- **Stabiliser incidence: -2.88 deg** relative to the fuselage (wing at +2.77 deg, decalage 5.66 deg)
- **Wing leading edge at 141 mm from the nose tip**, on top of the fuselage

## Stability
- Static margin 12% MAC, Cm_alpha -0.59/rad
- Cl_beta -0.134 (dihedral effect), Cn_beta 0.057 (weathercock)
- Dutch roll damping ratio 0.49; spiral stable
- Tail volumes: horizontal 0.33, vertical 0.042

## Parts
- Wing W04: polyhedral, span 968 mm, root chord 115 mm, taper 0.95, sweep 0.6 deg, dihedral 5.6 deg, washout 1.9 deg, NACA 2406, area 1096 cm^2, AR 8.7
- Fuselage F06: slab, length 775 mm, max diameter 68 mm
- Tail T08: conventional, stab span 167 mm x chord 44 mm, fin height 90 mm x chord 106 mm, NACA 0006

## Files
- `assembly.stl`, `part_*.stl`: meshes in millimetres (x aft, y right, z up)
- `template_*.svg`: 1:1 cutting templates (print at 100%, check the 100 mm bar)
- `three_view.png`: drawing with CG (red dot) and neutral point (blue x)

Trim-glide test: launch gently level over grass. Dives -> add a little positive stab incidence (or remove ballast); stalls/porpoises -> add nose ballast. Turns -> check for warps first.
