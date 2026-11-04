# Family whole-aircraft aerodynamics continuation

Continue the existing checkout and preserve the prior uncommitted control-response work.

1. Add native XFOIL coordinate-polar queries bound to exact installed foils,
   transition, Reynolds number, angles and binary/raw artifact identities. Keep
   incomplete rows explicit; never fill gaps or substitute a NACA label.
2. Use those polars in a separate development-only nonlinear lifting-line model
   with both physical halves, all installed members, source-resolved induced
   velocities, profile/induced drag, section pitching moments and conservation.
   Preserve production `mirum-aero-1` restrictions and family admission gates.
3. Exercise positive native attached-flow cases, missing/post-stall coverage,
   tamper/replay rejection, multi-surface coupling and existing regressions.
4. Record remaining body/dynamics/control-authority/reference requirements.
   A computed attached-flow diagnostic must not qualify nonlinear stall,
   control authority, dynamics or a complete aircraft.

Final qualification requires an independently validated family reference package,
installed body/control geometry, full inertia and a declared operating/required
control-moment envelope. No such package is currently present in the checkout;
its availability has been requested while independent software work continues.
