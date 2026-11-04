"""Isolated Task C prototype registry; production search remains unavailable."""
from . import tailless_swept, canard, tandem

BUILDERS = {m.Parameters().family: m for m in (tailless_swept, canard, tandem)}


def build(family, parameters=None):
    if family not in BUILDERS:
        raise ValueError('No Task C builder for selected family')
    return BUILDERS[family].build(parameters)


def capabilities():
    return {name: dict(geometry='screening_prototype',
        builder_revision=module.build().builder_revision, production_search=False,
        shared_geometry_contract='mirum-aero-1',
        shared_geometry_state=('unsupported_custom_reflex' if name == 'tailless_swept'
                               else 'neutral_control_screening_adapter'),
        shared_load_handoff='incomplete_rigid_member_resultants',
        development_geometry_contract='mirum-family-coordinates-1',
        development_geometry_scope='rigid_lifting_oml_with_explicit_controls',
        native_development_workflow='search_compare_export_with_reconstruction',
        independent_numerical_performance=False, physical_construction=False,
        missing=['A: family-scoped native qualification and reflex/control support',
                 'B: family-scoped construction and load-path qualification'])
        for name, module in BUILDERS.items()}


def archive(aircraft):
    from copy import deepcopy
    return dict(family=aircraft.family, builder_revision=aircraft.builder_revision,
                parameters=deepcopy(aircraft.parameters), aircraft_revision=aircraft.revision)


def reconstruct(record):
    if set(record) != {'family', 'builder_revision', 'parameters', 'aircraft_revision'}:
        raise ValueError('Malformed family geometry archive')
    aircraft = build(record['family'], record['parameters'])
    if record != archive(aircraft):
        raise ValueError('Family archive revision mismatch')
    return aircraft


def export_screening(record, directory):
    """Export lifting OML only, with explicit missing body/control/structure scope.

    No production design bundle is created. The equipment/body inventory is a
    screening mass model; these are not delivered parts or manufactured solids.
    """
    from pathlib import Path
    from cem.core import canonical_json
    from cem.evidence import file_digest
    from gliders.model.export import write_stl
    aircraft = reconstruct(record)
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)
    # Refuse to overwrite an existing review artifact.
    if any(path.iterdir()):
        raise ValueError('Screening export requires an empty directory')
    parts = []
    for wing in aircraft.airplane().wings:
        points, faces = wing.mesh_body(method='tri')
        target = path/(wing.name+'.stl')
        write_stl(str(target), points, faces)
        parts.append(dict(file=target.name, sha256=file_digest(target)))
    manifest = dict(archive=record, units='mm', scope='lifting_surface_oml_only',
        missing_geometry=['body', 'equipment', 'control_hinges', 'structure'],
        manufacturing_ready=False, independent_numerical_performance=False,
        mass_summary=aircraft.mass_summary(), parts=parts)
    (path/'screening.json').write_text(canonical_json(manifest))
    return manifest
