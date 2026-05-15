"""
backend/verification — post-compile geometry and material analysis.

Modules:
  geometry   — OCC-based structural checks + GeometryCertificate TypedDict
  material   — density table, resolve_material(), mass_properties()

Typical usage (from agents.py after run_critic_loop):

    from verification.geometry import certify_geometry
    from verification.material import resolve_material, mass_properties

    cert = certify_geometry(step_path)
    mat_name, density = resolve_material(requirements.material_class)
    if cert["volume_mm3"] is not None:
        props = mass_properties(cert["volume_mm3"], density)
        cert["mass_g"] = props["mass_g"]
"""
