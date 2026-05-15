"""T4-01 unit tests — SubagentMessage, SubagentContext, SubagentBase.

Pure stdlib, no pydantic/cadquery.  Run from repo root:
    python backend/tests/test_subagents_base.py
or via pytest:
    pytest backend/tests/test_subagents_base.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path

# Make backend/ importable without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from subagents.messages import (
    SubagentContext,
    SubagentMessage,
    _sanitize_floats,
    new_trace_id,
)
from subagents.base import SubagentBase


# ---------------------------------------------------------------------------
# Concrete test subagent
# ---------------------------------------------------------------------------

class _EchoSubagent(SubagentBase):
    """Minimal subagent that echoes the prompt back as its payload."""

    @property
    def agent_type(self):
        return "mock"

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        return self._ok(ctx, {"echo": ctx.prompt})


class _BoomSubagent(SubagentBase):
    """Subagent that always raises — used to test error wrapping."""

    @property
    def agent_type(self):
        return "mock"

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        raise RuntimeError("deliberate boom")


class _SkipSubagent(SubagentBase):
    """Subagent that always skips."""

    @property
    def agent_type(self):
        return "mock"

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        return self._skipped(ctx, "nothing to do")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestSubagentMessage(unittest.TestCase):

    def test_ok_factory(self):
        msg = SubagentMessage.ok(
            trace_id="abc", parent_id=None, agent_type="planner",
            payload={"manifest": {"parts": []}},
        )
        self.assertEqual(msg.status, "ok")
        self.assertTrue(msg.is_ok)
        self.assertFalse(msg.is_error)
        self.assertFalse(msg.is_skipped)
        self.assertEqual(msg.payload["manifest"]["parts"], [])

    def test_skipped_factory(self):
        msg = SubagentMessage.skipped(
            trace_id="abc", parent_id="root", agent_type="aero",
            reason="not domain D",
        )
        self.assertEqual(msg.status, "skipped")
        self.assertTrue(msg.is_skipped)
        self.assertEqual(msg.payload, {})
        self.assertIn("not domain D", msg.metadata["skip_reason"])

    def test_error_factory(self):
        exc = ValueError("bad input")
        msg = SubagentMessage.error(
            trace_id="abc", parent_id=None, agent_type="machinist", exc=exc,
        )
        self.assertEqual(msg.status, "error")
        self.assertTrue(msg.is_error)
        self.assertIn("ValueError", msg.metadata["error"])

    def test_json_round_trip(self):
        msg = SubagentMessage.ok(
            trace_id="t1", parent_id="t0", agent_type="critic",
            payload={"accepted": True, "attempts": 2},
            tokens_in=100, tokens_out=200, latency_ms=123.4,
        )
        restored = SubagentMessage.from_json(msg.to_json())
        self.assertEqual(restored.trace_id, "t1")
        self.assertEqual(restored.agent_type, "critic")
        self.assertEqual(restored.payload["attempts"], 2)
        self.assertAlmostEqual(restored.metadata["latency_ms"], 123.4, places=1)

    def test_nan_sanitised_in_to_dict(self):
        import math
        msg = SubagentMessage.ok(
            trace_id="t1", parent_id=None, agent_type="aero",
            payload={"Cl": [0.0, float("nan"), 1.2], "stall": float("inf")},
        )
        d = msg.to_dict()
        self.assertIsNone(d["payload"]["Cl"][1])
        self.assertIsNone(d["payload"]["stall"])
        # Must be valid JSON
        json.dumps(d)

    def test_repr_is_readable(self):
        msg = SubagentMessage.ok(
            trace_id="abcdef12-0000-0000-0000-000000000000",
            parent_id=None, agent_type="planner", payload={},
        )
        r = repr(msg)
        self.assertIn("planner", r)
        self.assertIn("abcdef12", r)


class TestSubagentContext(unittest.TestCase):

    def test_new_trace_id_generated(self):
        ctx = SubagentContext()
        self.assertTrue(len(ctx.trace_id) == 36)  # UUID4

    def test_child_inherits_trace_id(self):
        ctx = SubagentContext(trace_id="root-trace", prompt="original")
        child = ctx.child(prompt="modified", extra={"part_id": "p1"})
        self.assertEqual(child.trace_id, "root-trace")
        self.assertEqual(child.prompt, "modified")
        self.assertEqual(child.extra["part_id"], "p1")
        # Parent unchanged
        self.assertEqual(ctx.prompt, "original")

    def test_last_payload_from_returns_most_recent(self):
        ctx = SubagentContext(trace_id="t")
        ctx.history.append(SubagentMessage.ok(
            trace_id="t", parent_id=None, agent_type="planner",
            payload={"version": 1},
        ))
        ctx.history.append(SubagentMessage.ok(
            trace_id="t", parent_id=None, agent_type="planner",
            payload={"version": 2},
        ))
        p = ctx.last_payload_from("planner")
        self.assertEqual(p["version"], 2)

    def test_last_payload_from_skips_errors(self):
        ctx = SubagentContext(trace_id="t")
        ctx.history.append(SubagentMessage.error(
            trace_id="t", parent_id=None, agent_type="planner",
            exc=RuntimeError("boom"),
        ))
        ctx.history.append(SubagentMessage.ok(
            trace_id="t", parent_id=None, agent_type="planner",
            payload={"manifest": "valid"},
        ))
        p = ctx.last_payload_from("planner")
        self.assertEqual(p["manifest"], "valid")

    def test_last_payload_from_missing_returns_empty(self):
        ctx = SubagentContext()
        self.assertEqual(ctx.last_payload_from("critic"), {})


class TestSubagentBase(unittest.IsolatedAsyncioTestCase):

    async def test_echo_subagent_returns_ok(self):
        ctx = SubagentContext(prompt="hello")
        msg = await _EchoSubagent().run(ctx)
        self.assertTrue(msg.is_ok)
        self.assertEqual(msg.payload["echo"], "hello")
        self.assertEqual(msg.agent_type, "mock")

    async def test_boom_subagent_returns_error_never_raises(self):
        ctx = SubagentContext()
        msg = await _BoomSubagent().run(ctx)
        # Must NOT raise — error is wrapped in SubagentMessage
        self.assertTrue(msg.is_error)
        self.assertIn("deliberate boom", msg.metadata["error"])

    async def test_skip_subagent_returns_skipped(self):
        ctx = SubagentContext()
        msg = await _SkipSubagent().run(ctx)
        self.assertTrue(msg.is_skipped)

    async def test_latency_ms_always_set(self):
        ctx = SubagentContext()
        msg = await _EchoSubagent().run(ctx)
        self.assertGreaterEqual(msg.metadata["latency_ms"], 0)

    async def test_model_default_is_none_string(self):
        agent = _EchoSubagent()
        self.assertEqual(agent.model, "none")

    async def test_trace_id_propagated(self):
        ctx = SubagentContext(trace_id="fixed-trace-id")
        msg = await _EchoSubagent().run(ctx)
        self.assertEqual(msg.trace_id, "fixed-trace-id")


class TestSanitizeFloats(unittest.TestCase):

    def test_nan_becomes_none(self):
        import math
        self.assertIsNone(_sanitize_floats(float("nan")))

    def test_inf_becomes_none(self):
        self.assertIsNone(_sanitize_floats(float("inf")))
        self.assertIsNone(_sanitize_floats(float("-inf")))

    def test_normal_float_unchanged(self):
        self.assertAlmostEqual(_sanitize_floats(3.14), 3.14)

    def test_nested_dict(self):
        import math
        result = _sanitize_floats({"a": float("nan"), "b": {"c": 1.0}})
        self.assertIsNone(result["a"])
        self.assertAlmostEqual(result["b"]["c"], 1.0)

    def test_list_with_nan(self):
        result = _sanitize_floats([1.0, float("nan"), 3.0])
        self.assertEqual(result[0], 1.0)
        self.assertIsNone(result[1])
        self.assertEqual(result[2], 3.0)

    def test_non_float_passthrough(self):
        self.assertEqual(_sanitize_floats("hello"), "hello")
        self.assertEqual(_sanitize_floats(42), 42)
        self.assertIsNone(_sanitize_floats(None))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("T4-01 — SubagentBase + SubagentMessage + SubagentContext tests")
    print("=" * 60)
    unittest.main(verbosity=2)
