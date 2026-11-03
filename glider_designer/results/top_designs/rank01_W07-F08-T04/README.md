# Rank 1: W07-F08-T04

## Performance (predicted, still air)
- Glide ratio L/D: **13.3**  (distance from 10 m: 133 m)
- Sink rate: 0.34 m/s  (time aloft: 29.1 s)
- Glide / landing speed: 4.6 m/s, landing energy 1.98 J
- Trim: alpha 5.5 deg, CL 0.62, Reynolds 51480

## Build and balance
- Total mass: 189 g  (wing 70 g, htail 3 g, vtail 2 g, fuselage 44 g, hardware 10 g, payload 60 g)
- Nose ballast: none needed
- **Payload centre: 156 mm from the nose tip** (60 g)
- **Balance point (CG): 251 mm from the nose tip** (neutral point 269 mm, static margin 11% MAC)
- **Stabiliser incidence: -2.33 deg** relative to the fuselage (wing at +1.46 deg, decalage 3.79 deg)
- **Wing leading edge at 165 mm from the nose tip**, on top of the fuselage

## Stability
- Static margin 11% MAC, Cm_alpha -0.51/rad
- Cl_beta -0.181 (dihedral effect), Cn_beta 0.008 (weathercock)
- Dutch roll damping ratio 0.40; spiral stable
- Tail volumes: horizontal 0.32, vertical 0.021

## Parts
- Wing W07: elliptical, span 1392 mm, root chord 177 mm, taper 0.71, sweep 5.5 deg, dihedral 10.6 deg, washout 2.3 deg, NACA 1409, area 2342 cm^2, AR 8.6
- Fuselage F08: pod_boom, length 880 mm, max diameter 57 mm
- Tail T04: T, stab span 212 mm x chord 95 mm, fin height 136 mm x chord 83 mm, NACA 0008

## Files
- `assembly.stl`, `part_*.stl`: meshes in millimetres (x aft, y right, z up)
- `template_*.svg`: 1:1 cutting templates (print at 100%, check the 100 mm bar)
- `three_view.png`: drawing with CG (red dot) and neutral point (blue x)

Trim-glide test: launch gently level over grass. Dives -> add a little positive stab incidence (or remove ballast); stalls/porpoises -> add nose ballast. Turns -> check for warps first.
