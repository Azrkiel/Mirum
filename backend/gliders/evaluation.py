"""Single fresh assembly, the existing solver and independent admission checks."""
import json
import zipfile
from .design_spec import VERSION, placement
from .preview import geometry


def run_evaluation(spec, output):
    import aerosandbox
    import neuralfoil
    from .model.analysis import evaluate
    from .model import export
    from .search import admissible, finite_json, ASSUMPTIONS
    gl = spec.build()
    if spec.flight_conditions.mode == 'specified':
        from .flight_conditions import evaluate_conditions
        result = evaluate_conditions(gl, spec)
        assumptions = ('Specified-condition AeroSandbox/NeuralFoil force estimate, not CFD. '
                       'ISA atmosphere; true airspeed; no automatic rebalance or retrim. '
                       'Force/moment equilibrium, stability, stall margin and glide distance are not established.')
    else:
        result = admissible(gl, spec.request, evaluate(gl))
        assumptions = ASSUMPTIONS
    report = finite_json(dict(input_revision=spec.digest(), implementation=VERSION,
        solver=dict(aerosandbox=aerosandbox.__version__, neuralfoil=neuralfoil.__version__),
        design=spec.model_dump(), effective_placement=placement(gl), geometry=geometry(gl),
        metrics=result, assumptions=assumptions,
        analysis_mode=spec.flight_conditions.mode,
        balancing_policy_applied=('preserve_placement' if spec.flight_conditions.mode == 'specified' else spec.balancing_policy),
        operating_conditions=(result.get("operating_conditions") if spec.flight_conditions.mode == "specified" else
            dict(airspeed_m_s=result.get("glide_speed_m_s"), alpha_deg=result.get("alpha_trim_deg"), beta_deg=0, altitude_m=0)),
        adjustments={k: {'nominal': spec.placement.model_dump()[k], 'effective': v}
                     for k, v in placement(gl).items() if spec.placement.model_dump()[k] != v}))
    # Exports bind to this exact evaluated assembly, including infeasible shapes.
    folder = output / 'geometry'
    folder.mkdir()
    export.export_stls(gl, str(folder))
    export.export_templates(gl, str(folder))
    (folder / 'design.json').write_text(json.dumps(report, allow_nan=False))
    with zipfile.ZipFile(output / 'evaluation.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(folder.iterdir()):
            archive.write(path, path.name)
    (output / 'result.json').write_text(json.dumps(report, allow_nan=False))
    return report
