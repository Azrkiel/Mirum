# Rank 66: W03-F01-T08

## Performance (predicted, still air)
- Glide ratio L/D: **11.9**  (distance from 10 m: 119 m)
- Sink rate: 0.48 m/s  (time aloft: 20.9 s)
- Glide / landing speed: 5.7 m/s, landing energy 2.24 J
- Trim: alpha 5.0 deg, CL 0.73, Reynolds 39735

## Build and balance
- Total mass: 139 g  (wing 28 g, htail 1 g, vtail 1 g, fuselage 38 g, hardware 10 g, payload 60 g)
- Nose ballast: none needed
- **Payload centre: 189 mm from the nose tip** (60 g)
- **Balance point (CG): 224 mm from the nose tip** (neutral point 236 mm, static margin 12% MAC)
- **Stabiliser incidence: -3.78 deg** relative to the fuselage (wing at +1.72 deg, decalage 5.50 deg)
- **Wing leading edge at 163 mm from the nose tip**, on top of the fuselage

## Stability
- Static margin 12% MAC, Cm_alpha -0.61/rad
- Cl_beta -0.105 (dihedral effect), Cn_beta 0.066 (weathercock)
- Dutch roll damping ratio 0.51; spiral stable
- Tail volumes: horizontal 0.34, vertical 0.040

## Parts
- Wing W03: polyhedral, span 938 mm, root chord 133 mm, taper 0.49, sweep 6.1 deg, dihedral 3.9 deg, washout 1.1 deg, NACA 2307, area 936 cm^2, AR 9.5
- Fuselage F01: slab, length 704 mm, max diameter 61 mm
- Tail T08: conventional, stab span 167 mm x chord 44 mm, fin height 90 mm x chord 106 mm, NACA 0006

## Files
- `assembly.stl`, `part_*.stl`: meshes in millimetres (x aft, y right, z up)
- `template_*.svg`: 1:1 cutting templates (print at 100%, check the 100 mm bar)
- `three_view.png`: drawing with CG (red dot) and neutral point (blue x)

Trim-glide test: launch gently level over grass. Dives -> add a little positive stab incidence (or remove ballast); stalls/porpoises -> add nose ballast. Turns -> check for warps first.
