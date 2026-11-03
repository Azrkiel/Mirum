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
    result = admissible(gl, spec.request, evaluate(gl))
    report = finite_json(dict(input_revision=spec.digest(), implementation=VERSION,
        solver=dict(aerosandbox=aerosandbox.__version__, neuralfoil=neuralfoil.__version__),
        design=spec.model_dump(), effective_placement=placement(gl), geometry=geometry(gl),
        metrics=result, assumptions=ASSUMPTIONS,
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
