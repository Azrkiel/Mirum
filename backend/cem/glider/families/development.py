"""Bounded fixture-injected search/comparison; never called by production jobs."""
import random
from . import BUILDERS, build, archive, reconstruct
from .admission import assess


def validate_record(record):
    if set(record) != {'geometry', 'evidence', 'assessment'}:
        raise ValueError('Malformed family development record')
    aircraft = reconstruct(record['geometry'])
    if assess(aircraft, record['evidence']) != record['assessment']:
        raise ValueError('Stale or altered family development assessment')
    return aircraft


def search(family, solver, *, budget=8, seed=42):
    if type(budget) is not int or not 1 <= budget <= 120:
        raise ValueError('Family development budget must be 1..120')
    if family not in BUILDERS:
        raise ValueError('No development builder for selected family')
    rng = random.Random(seed)
    records, accepted = [], []
    for i in range(budget):
        parameters = BUILDERS[family].Parameters().model_dump(mode='json')
        # Sample the family's actual geometry parameterization. No ballast or
        # payload moves are hidden in the injected solver interface.
        key = {'tailless_swept': 'wing', 'canard': 'main_wing', 'tandem': 'rear_wing'}[family]
        if i:
            parameters[key]['incidence_deg'] = rng.uniform(-2., 6.)
            parameters[key]['washout_deg'] = rng.uniform(0., 4.)
        aircraft = build(family, parameters)
        raw = solver(aircraft)
        assessment = assess(aircraft, raw)
        record = dict(geometry=archive(aircraft), evidence=raw, assessment=assessment)
        records.append(record)
        if assessment['state'] == 'fixture_passed':
            accepted.append(record)
    return dict(scope='development_fixtures_only', evaluations=records, accepted=accepted,
                production_search=False, independent_numerical_performance=False)


def compare(left, right, solver):
    aircraft = [reconstruct(record) for record in (left, right)]
    if aircraft[0].family != aircraft[1].family:
        raise ValueError('Family development comparison requires the same family')
    if aircraft[0].parameters['inventory'] != aircraft[1].parameters['inventory']:
        raise ValueError('Comparison must retain equipment/body/material inventory')
    arms = [assess(a, solver(a)) for a in aircraft]
    return dict(state='fixture_passed' if all(a['state'] == 'fixture_passed' for a in arms)
                else 'inconclusive', arms=arms, verified_gain=False, performance_delta=None)
