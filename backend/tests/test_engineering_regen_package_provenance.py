"""E01 A1: the regen package records native identities and the resource policy, never server paths."""
import json
from types import SimpleNamespace

from engineering.bundles import PACKAGE_PROVENANCE_KEYS, BundlePublisher
from engineering.capabilities import DRAGSTER, GLIDER, REGEN

PROVENANCE = dict(runtime_so_sha256='1' * 64, builder_sha256='2' * 64, builder_path='/opt/regen/app/builder',
                  runtime_so_path='/opt/regen/app/libpicogk.so', gcc_lib_path='/opt/regen/lib',
                  image_id='sha256:' + '3' * 64, run_bytes=256 * 1024 ** 2,
                  resource_policy_version='engineering_resources_v1')


def report_json(descriptor, evaluations):
    brief = SimpleNamespace(model_dump=lambda mode=None: {}, brief_id='b1', brief_sha256='a' * 64,
                            fixed_requirements_sha256='b' * 64,
                            content=SimpleNamespace(descriptor=descriptor,
                                                    capability=SimpleNamespace(model_dump=lambda mode=None: {})))
    result = SimpleNamespace(reasons=[], evaluations=evaluations, attempts=[])
    files = BundlePublisher._generator_reports(object.__new__(BundlePublisher), 'j1', brief, result, 'accepted')
    return json.loads(files['report.json']), files['report.json']


def evaluation(**extra):
    return dict(candidate=SimpleNamespace(candidate_sha256='c' * 64), report_sha256='d' * 64,
                report={'schema': 'x'}, **extra)


def test_regen_package_records_hashes_image_and_resource_policy():
    report, _ = report_json(REGEN, [evaluation(provenance=PROVENANCE)])
    recorded = report['evaluations'][0]['native_provenance']
    assert recorded == {k: PROVENANCE[k] for k in PACKAGE_PROVENANCE_KEYS}
    assert recorded['run_bytes'] == REGEN.resources.run_bytes


def test_no_server_path_reaches_the_package():
    assert not any(k.endswith('_path') for k in PACKAGE_PROVENANCE_KEYS)
    _, raw = report_json(REGEN, [evaluation(provenance=PROVENANCE)])
    for value in ('/opt/regen/app/builder', '/opt/regen/app/libpicogk.so', '/opt/regen/lib', '_path'):
        assert value.encode() not in raw


def test_evaluations_without_provenance_are_unchanged():
    for descriptor in (DRAGSTER, GLIDER, REGEN):
        report, _ = report_json(descriptor, [evaluation()])
        assert 'native_provenance' not in report['evaluations'][0]
        assert set(report['evaluations'][0]) == {'candidate_sha256', 'report_sha256', 'generator_report'}


def test_unknown_provenance_keys_are_dropped():
    report, _ = report_json(REGEN, [evaluation(provenance=dict(PROVENANCE, extra='x', env={'K': 'v'}))])
    assert set(report['evaluations'][0]['native_provenance']) == set(PACKAGE_PROVENANCE_KEYS)
