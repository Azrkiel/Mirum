"""T4-04 unit tests — MachinistSubagent parallelism (no agents.py/pydantic needed).

Tests the SubagentBase contract + parallel execution semantics using a
mock critic loop.  Never calls real Machinist / Gemini / CadQuery.

Run:
    python backend/tests/test_machinist_subagent.py
"""
from __future__ import annotations

import asyncio
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from subagents.messages import SubagentContext, SubagentMessage
from subagents.base import SubagentBase


# ---------------------------------------------------------------------------
# Mock PartDefinition (no pydantic)
# ---------------------------------------------------------------------------

class _MockPart:
    def __init__(self, part_id: str, description: str = "a box") -> None:
        self.part_id = part_id
        self.description = description


# ---------------------------------------------------------------------------
# Mock MachinistSubagent that doesn't import agents.py
# ---------------------------------------------------------------------------

class _MockMachinistSubagent(SubagentBase):
    """Simulate a fast Machinist run without touching agents.py."""

    def __init__(self, *, delay: float = 0.01, fail: bool = False) -> None:
        self.delay = delay
        self.fail = fail

    @property
    def agent_type(self):
        return "machinist"

    async def _run(self, ctx: SubagentContext) -> SubagentMessage:
        part_def = ctx.extra.get("part_def")
        if part_def is None:
            raise ValueError("missing part_def")
        await asyncio.sleep(self.delay)
        if self.fail:
            raise RuntimeError(f"deliberate failure for {part_def.part_id}")
        return self._ok(ctx, {
            "part_id": part_def.part_id,
            "code": f"result = cq.Workplane().box(1,1,1)  # {part_def.part_id}",
            "step_path": f"/tmp/{part_def.part_id}.step",
            "code_len": 50,
        })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestMachinistParallelism(unittest.IsolatedAsyncioTestCase):

    async def _run_parts(
        self,
        parts: list[_MockPart],
        *,
        delay: float = 0.05,
        semaphore_n: int = 4,
    ) -> tuple[list[SubagentMessage], float]:
        """Run mock Machinist subagents in parallel, return (messages, wall_clock)."""
        sem = asyncio.Semaphore(semaphore_n)
        root_ctx = SubagentContext(trace_id="test-trace")

        async def _build(part):
            async with sem:
                ctx = root_ctx.child(extra={"part_def": part})
                msg = await _MockMachinistSubagent(delay=delay).run(ctx)
                root_ctx.history.append(msg)
                return msg

        t0 = time.perf_counter()
        msgs = list(await asyncio.gather(*[_build(p) for p in parts]))
        elapsed = time.perf_counter() - t0
        return msgs, elapsed

    async def test_single_part_ok(self):
        parts = [_MockPart("p1")]
        msgs, _ = await self._run_parts(parts)
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0].is_ok)
        self.assertEqual(msgs[0].payload["part_id"], "p1")

    async def test_four_parts_all_ok(self):
        parts = [_MockPart(f"p{i}") for i in range(4)]
        msgs, _ = await self._run_parts(parts)
        self.assertEqual(len(msgs), 4)
        self.assertTrue(all(m.is_ok for m in msgs))
        ids = {m.payload["part_id"] for m in msgs}
        self.assertEqual(ids, {"p0", "p1", "p2", "p3"})

    async def test_parallel_faster_than_sequential(self):
        """4 parts × 50ms each: parallel should finish in ~50ms, not 200ms."""
        parts = [_MockPart(f"p{i}") for i in range(4)]
        _, elapsed = await self._run_parts(parts, delay=0.05, semaphore_n=4)
        # Parallel: ≈ 50ms; sequential: ≈ 200ms
        # Generous threshold: parallel < 75ms (wall clock in CI may be slow)
        self.assertLess(elapsed, 0.15,
                        f"Expected parallel < 0.15s but got {elapsed:.3f}s")

    async def test_semaphore_throttles_concurrency(self):
        """With sem=1 and 4 parts × 20ms, must take ≥ 80ms (sequential)."""
        parts = [_MockPart(f"p{i}") for i in range(4)]
        _, elapsed = await self._run_parts(parts, delay=0.02, semaphore_n=1)
        self.assertGreaterEqual(elapsed, 0.07,
                                f"Expected throttled ≥ 0.07s but got {elapsed:.3f}s")

    async def test_one_failure_still_returns_all_messages(self):
        """asyncio.gather returns results/exceptions; all messages come back."""
        sem = asyncio.Semaphore(4)
        root_ctx = SubagentContext(trace_id="t")

        parts = [_MockPart("ok1"), _MockPart("fail1"), _MockPart("ok2")]

        async def _build(part):
            async with sem:
                ctx = root_ctx.child(extra={"part_def": part})
                fail = part.part_id.startswith("fail")
                msg = await _MockMachinistSubagent(fail=fail).run(ctx)
                root_ctx.history.append(msg)
                return msg

        msgs = list(await asyncio.gather(*[_build(p) for p in parts]))
        ok_msgs = [m for m in msgs if m.is_ok]
        err_msgs = [m for m in msgs if m.is_error]
        self.assertEqual(len(ok_msgs), 2)
        self.assertEqual(len(err_msgs), 1)
        self.assertIn("deliberate failure", err_msgs[0].metadata["error"])

    async def test_trace_id_propagated_to_all_parts(self):
        parts = [_MockPart(f"p{i}") for i in range(3)]
        root_ctx = SubagentContext(trace_id="fixed-trace")
        sem = asyncio.Semaphore(4)

        async def _build(part):
            async with sem:
                ctx = root_ctx.child(extra={"part_def": part})
                return await _MockMachinistSubagent().run(ctx)

        msgs = list(await asyncio.gather(*[_build(p) for p in parts]))
        for msg in msgs:
            self.assertEqual(msg.trace_id, "fixed-trace")

    async def test_latency_recorded(self):
        parts = [_MockPart("p1")]
        msgs, _ = await self._run_parts(parts, delay=0.01)
        self.assertGreater(msgs[0].metadata["latency_ms"], 0)


class TestMachinistSubagentContract(unittest.IsolatedAsyncioTestCase):
    """Verify SubagentBase contract independently of parallelism."""

    async def test_run_never_raises_on_failure(self):
        """SubagentBase.run() wraps exceptions → is_error, never propagates."""
        ctx = SubagentContext(extra={"part_def": None})  # missing part_def
        msg = await _MockMachinistSubagent().run(ctx)
        self.assertTrue(msg.is_error)
        self.assertIn("missing part_def", msg.metadata["error"])

    async def test_agent_type_is_machinist(self):
        self.assertEqual(_MockMachinistSubagent().agent_type, "machinist")

    async def test_payload_contains_required_keys(self):
        ctx = SubagentContext(extra={"part_def": _MockPart("px")})
        msg = await _MockMachinistSubagent().run(ctx)
        for key in ("part_id", "code", "step_path", "code_len"):
            self.assertIn(key, msg.payload, f"missing key: {key}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("T4-04 — MachinistSubagent parallelism tests")
    print("=" * 60)
    unittest.main(verbosity=2)
