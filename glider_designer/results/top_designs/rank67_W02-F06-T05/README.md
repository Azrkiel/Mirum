# Rank 67: W02-F06-T05

## Performance (predicted, still air)
- Glide ratio L/D: **11.8**  (distance from 10 m: 118 m)
- Sink rate: 0.67 m/s  (time aloft: 15.0 s)
- Glide / landing speed: 7.9 m/s, landing energy 4.65 J
- Trim: alpha 2.5 deg, CL 0.32, Reynolds 80003

## Build and balance
- Total mass: 148 g  (wing 35 g, htail 2 g, vtail 1 g, fuselage 40 g, hardware 10 g, payload 60 g)
- Nose ballast: none needed
- **Payload centre: 156 mm from the nose tip** (60 g)
- **Balance point (CG): 207 mm from the nose tip** (neutral point 225 mm, static margin 12% MAC)
- **Stabiliser incidence: -0.78 deg** relative to the fuselage (wing at +1.90 deg, decalage 2.68 deg)
- **Wing leading edge at 141 mm from the nose tip**, on top of the fuselage

## Stability
- Static margin 12% MAC, Cm_alpha -0.53/rad
- Cl_beta -0.133 (dihedral effect), Cn_beta 0.042 (weathercock)
- Dutch roll damping ratio 0.43; spiral stable
- Tail volumes: horizontal 0.33, vertical 0.024

## Parts
- Wing W02: tapered, span 786 mm, root chord 172 mm, taper 0.71, sweep 5.7 deg, dihedral 9.3 deg, washout 1.3 deg, NACA 0006, area 1176 cm^2, AR 5.4
- Fuselage F06: slab, length 775 mm, max diameter 68 mm
- Tail T05: conventional, stab span 192 mm x chord 69 mm, fin height 90 mm x chord 54 mm, NACA 0006

## Files
- `assembly.stl`, `part_*.stl`: meshes in millimetres (x aft, y right, z up)
- `template_*.svg`: 1:1 cutting templates (print at 100%, check the 100 mm bar)
- `three_view.png`: drawing with CG (red dot) and neutral point (blue x)

Trim-glide test: launch gently level over grass. Dives -> add a little positive stab incidence (or remove ballast); stalls/porpoises -> add nose ballast. Turns -> check for warps first.
