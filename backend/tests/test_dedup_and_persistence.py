"""Tests for duplicate-part dedup (P2.4) and telemetry persistence (P3.4).

    python -m pytest backend/tests/test_dedup_and_persistence.py
"""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-key")

from agents import group_parts_by_description  # noqa: E402
from schemas import PartDefinition  # noqa: E402

_REPO = pathlib.Path(__file__).resolve().parent.parent.parent


def _part(pid, desc):
    return PartDefinition(part_id=pid, description=desc, anchor_tags=[">Z"])


# ---------------------------------------------------------------------------
# P2.4 — dedup grouping
# ---------------------------------------------------------------------------

def test_the_four_legged_table_builds_one_leg():
    """The motivating case: the Planner is REQUIRED to emit leg_1..leg_4
    separately, which previously cost 4x classify + Machinist + export."""
    parts = [
        _part("tabletop", "A 1200x800x25mm rectangular tabletop"),
        _part("leg_1", "A 60mm square leg, 700mm tall"),
        _part("leg_2", "A 60mm square leg, 700mm tall"),
        _part("leg_3", "A 60mm square leg, 700mm tall"),
        _part("leg_4", "A 60mm square leg, 700mm tall"),
    ]
    groups = group_parts_by_description(parts)
    assert len(groups) == 2, "5 parts, 2 unique geometries"

    by_rep = {rep.part_id: pids for rep, pids in groups}
    assert by_rep["tabletop"] == ["tabletop"]
    assert by_rep["leg_1"] == ["leg_1", "leg_2", "leg_3", "leg_4"]

    # Every part_id still accounted for — nothing may be dropped, or the
    # assembler's completeness check fails the whole request.
    covered = [pid for _rep, pids in groups for pid in pids]
    assert sorted(covered) == sorted(p.part_id for p in parts)


def test_all_distinct_parts_are_not_grouped():
    parts = [_part("a", "desc A"), _part("b", "desc B"), _part("c", "desc C")]
    groups = group_parts_by_description(parts)
    assert len(groups) == 3
    assert all(len(pids) == 1 for _rep, pids in groups)


def test_grouping_preserves_planner_build_order():
    """Planner lists base/foundation first; the assembler relies on it."""
    parts = [_part("base", "the base"), _part("x1", "a widget"),
             _part("top", "the top"), _part("x2", "a widget")]
    groups = group_parts_by_description(parts)
    assert [rep.part_id for rep, _ in groups] == ["base", "x1", "top"]


def test_representative_is_the_first_occurrence():
    parts = [_part("leg_1", "same"), _part("leg_2", "same")]
    (rep, pids), = group_parts_by_description(parts)
    assert rep.part_id == "leg_1"
    assert pids == ["leg_1", "leg_2"]


def test_near_identical_descriptions_are_not_merged():
    """Dedup keys on exact description; a 1mm difference is a different part."""
    parts = [_part("a", "A 60mm square leg"), _part("b", "A 61mm square leg")]
    assert len(group_parts_by_description(parts)) == 2


def test_empty_part_list():
    assert group_parts_by_description([]) == []


# ---------------------------------------------------------------------------
# P3.4 — telemetry survives a restart
# ---------------------------------------------------------------------------

def _compose():
    import yaml
    with open(_REPO / "docker-compose.yml") as fh:
        return yaml.safe_load(fh)


def test_telemetry_dir_is_a_named_volume_not_tmpfs():
    """The retrieval corpus is rebuilt from this log at startup; on tmpfs
    every restart wiped every past successful generation."""
    compose = _compose()
    backend = compose["services"]["backend"]

    env = " ".join(backend.get("environment") or [])
    assert "MIRUM_TELEMETRY_DIR" in env
    target = "/var/lib/mirum/telemetry"
    assert target in env, "telemetry dir must point at the persisted path"
    assert not target.startswith("/tmp"), "must not live on the tmpfs"

    mounts = backend.get("volumes") or []
    assert any(m.endswith(f":{target}") for m in mounts), (
        f"no volume mounted at {target}: {mounts}"
    )

    volume_name = next(m.split(":")[0] for m in mounts if m.endswith(f":{target}"))
    assert volume_name in (compose.get("volumes") or {}), (
        f"{volume_name} mounted but not declared under top-level volumes"
    )


def test_tmpfs_is_still_only_for_scratch():
    """The /tmp tmpfs must remain — sandbox scratch depends on it."""
    backend = _compose()["services"]["backend"]
    assert any(t.startswith("/tmp") for t in (backend.get("tmpfs") or []))


def test_telemetry_default_still_works_without_compose():
    """Bare `python -m eval_harness.runner` must not require the env var."""
    import telemetry
    assert telemetry._LOG_DIR is not None
    assert telemetry._LOG_FILE.name == "critic_loop.jsonl"
