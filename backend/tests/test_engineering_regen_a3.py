"""Pin regen's geometry-only capability and report contract."""
from pathlib import Path

from engineering.api_v1 import generator_entry
from engineering.capabilities import REGEN


def test_regen_descriptor_offers_no_step_artifacts():
    assert REGEN.primary_artifact == 'generator-cleaned.stl'
    assert REGEN.payloads == (
        'generator-cleaned.stl',
        'generator-report.json',
        'generator-spec.canonical.json',
    )
    artifact_names = [name.lower() for name in (*REGEN.payloads, REGEN.primary_artifact)]
    assert all('step' not in name and not name.endswith(('.step', '.stp')) for name in artifact_names)


def test_regen_unsupported_features_are_listed_by_the_api():
    unsupported = {
        'step_export',
        'gas_dynamics',
        'thermal_analysis',
        'structural_analysis',
        'manufacturing_analysis',
    }
    assert unsupported <= set(REGEN.unsupported_features)
    assert unsupported <= set(generator_entry(REGEN)['unsupported_features'])


def test_regen_report_marks_step_export_unavailable():
    mesh_source = Path(__file__).resolve().parents[1] / 'engineering' / 'regen_mesh.py'
    assert "'step_export': 'not_available'," in mesh_source.read_text(encoding='utf-8')
