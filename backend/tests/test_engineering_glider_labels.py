"""Package report heading/outcome line follow the capability claim (pure; no services)."""
from types import SimpleNamespace

from engineering.bundles import BundlePublisher
from engineering.capabilities import DRAGSTER, GLIDER, REGEN


def report_md(descriptor, outcome='accepted'):
    brief = SimpleNamespace(model_dump=lambda mode=None: {}, brief_id='b1', brief_sha256='a' * 64, fixed_requirements_sha256='b' * 64,
                            content=SimpleNamespace(descriptor=descriptor,
                                                    capability=SimpleNamespace(model_dump=lambda mode=None: {})))
    result = SimpleNamespace(reasons=[], evaluations=[], attempts=[])
    files = BundlePublisher._generator_reports(object.__new__(BundlePublisher), 'j1', brief, result, outcome)
    return files['report.md'].decode()


def test_glider_package_reads_screened_not_checked_or_accepted():
    text = report_md(GLIDER)
    lines = text.splitlines()
    assert lines[0] == f'# Screened {GLIDER.label} result'
    assert 'Outcome: screened; not accepted for manufacture; not engineering certified' in lines
    assert 'Checked' not in text and 'Accepted' not in text


def test_geometry_only_packages_say_geometry_checks_passed():
    for d in (DRAGSTER, REGEN):
        text = report_md(d)
        assert text.splitlines()[0] == f'# Geometry-checked {d.label} result'
        assert 'Outcome: geometry checks passed' in text
        assert 'Checked design' not in text and 'Accepted' not in text


def test_rejected_outcome_keeps_the_raw_value():
    assert 'Outcome: rejected' in report_md(GLIDER, 'rejected').splitlines()
