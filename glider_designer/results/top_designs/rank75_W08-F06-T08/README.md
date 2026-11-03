# Rank 75: W08-F06-T08

## Performance (predicted, still air)
- Glide ratio L/D: **11.8**  (distance from 10 m: 118 m)
- Sink rate: 0.61 m/s  (time aloft: 16.3 s)
- Glide / landing speed: 7.2 m/s, landing energy 3.74 J
- Trim: alpha 4.0 deg, CL 0.43, Reynolds 60773

## Build and balance
- Total mass: 143 g  (wing 31 g, htail 1 g, vtail 1 g, fuselage 40 g, hardware 10 g, payload 60 g)
- Nose ballast: none needed
- **Payload centre: 157 mm from the nose tip** (60 g)
- **Balance point (CG): 203 mm from the nose tip** (neutral point 217 mm, static margin 11% MAC)
- **Stabiliser incidence: -0.72 deg** relative to the fuselage (wing at +1.79 deg, decalage 2.51 deg)
- **Wing leading edge at 141 mm from the nose tip**, on top of the fuselage

## Stability
- Static margin 11% MAC, Cm_alpha -0.49/rad
- Cl_beta -0.099 (dihedral effect), Cn_beta 0.081 (weathercock)
- Dutch roll damping ratio 0.52; spiral stable
- Tail volumes: horizontal 0.31, vertical 0.052

## Parts
- Wing W08: elliptical, span 814 mm, root chord 125 mm, taper 0.96, sweep 7.0 deg, dihedral 5.8 deg, washout 2.0 deg, NACA 0008, area 1017 cm^2, AR 6.6
- Fuselage F06: slab, length 775 mm, max diameter 68 mm
- Tail T08: conventional, stab span 167 mm x chord 44 mm, fin height 90 mm x chord 106 mm, NACA 0006

## Files
- `assembly.stl`, `part_*.stl`: meshes in millimetres (x aft, y right, z up)
- `template_*.svg`: 1:1 cutting templates (print at 100%, check the 100 mm bar)
- `three_view.png`: drawing with CG (red dot) and neutral point (blue x)

Trim-glide test: launch gently level over grass. Dives -> add a little positive stab incidence (or remove ballast); stalls/porpoises -> add nose ballast. Turns -> check for warps first.
