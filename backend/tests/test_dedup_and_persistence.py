"""Tests for duplicate-part dedup (P2.4) and the telemetry default (P3.4).

The PR #4 docker-compose persistence assertions were dropped: that compose change
is infrastructure and is not part of this port.

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

def test_telemetry_default_still_works_without_compose():
    """Bare `python -m eval_harness.runner` must not require the env var."""
    import telemetry
    assert telemetry._LOG_DIR is not None
    assert telemetry._LOG_FILE.name == "critic_loop.jsonl"


def test_batch_builds_each_unique_description_once_and_fans_out(monkeypatch):
    import asyncio
    import agents

    built = []

    async def fake_loop(part, **_kw):
        built.append(part.part_id)
        return f"code_for_{part.part_id}", f"/tmp/{part.part_id}.step", {"passed": True}

    monkeypatch.setattr(agents, "run_critic_loop", fake_loop)
    parts = [_part("leg_1", "a leg"), _part("leg_2", "a leg"), _part("top", "a top")]
    scripts, steps, certs = asyncio.run(agents.run_machinist_batch(parts))
    assert built == ["leg_1", "top"]
    assert scripts["leg_2"] == scripts["leg_1"] == "code_for_leg_1"
    assert steps["leg_2"] == steps["leg_1"]
    assert set(certs) == {"leg_1", "leg_2", "top"}
