"""Subagent result-contract regression tests — run_critic_loop() return triple.

``agents.run_critic_loop()`` returns ``(code, step_path, geometry_cert)``.
Each consumer must accept all three values and surface the validated ``code``
and ``step_path`` as its stage output.

These tests run the REAL ``run_critic_loop()`` and stub only its seams:

  model  ``classify_part``, ``run_machinist``, ``run_rea``, ``run_planner``
  CAD    ``execute_cad_script`` (sandbox), ``_save_part_step`` (STEP export),
         ``verification.geometry.certify_geometry`` (OCC)
  I/O    ``log_attempt``, ``add_to_index``, ``_score_and_log``,
         ``write_subagent_span`` (telemetry, retrieval index, LVM scoring)

Offline and deterministic: no network, no subprocess, no telemetry or index
writes.  Run inside the backend container, where agents.py's dependencies are
installed:

    pytest backend/tests/test_subagent_result_contract.py -v
"""

from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest import mock

# Make backend/ importable without installing the package
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import agents  # noqa: E402 — real run_critic_loop; only its seams are stubbed
import compiler  # noqa: E402
from subagents import telemetry_mixin  # noqa: E402
from subagents.critic import CriticSubagent  # noqa: E402
from subagents.machinist import MachinistSubagent  # noqa: E402
from subagents.messages import SubagentContext  # noqa: E402
from subagents.orchestrator import Orchestrator, PipelineState  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

PART_CODE = "result = cq.Workplane('XY').box(10, 10, 10)"
BAD_CODE = "result = cq.Workplane('XY').boxx(10, 10, 10)"
ASSEMBLY_SCRIPT = "result = cq.Assembly()\n"
RETRY_CONTEXT = "Attempt 1 failed: AttributeError: 'Workplane' object has no attribute 'boxx'"
SANDBOX_OK = {"status": "success"}


class _MockPart:
    """Stand-in for PartDefinition: the critic loop reads only part_id and description."""

    def __init__(self, part_id: str, description: str = "a box") -> None:
        self.part_id = part_id
        self.description = description


class _MockManifest:
    """Stand-in for AssemblyManifest: the stages read only parts and model_dump()."""

    def __init__(self, parts: list[_MockPart]) -> None:
        self.parts = parts

    def model_dump(self) -> dict:
        return {"parts": [p.part_id for p in self.parts]}


def _fake_step_path(part_id: str, script: str) -> str:
    return f"/fake/steps/{part_id}.step"


async def _drain(stage) -> list[dict]:
    """Collect one stage's SSE frames (``data: {...}`` plus a blank line) as dicts."""
    return [json.loads(frame.removeprefix("data: ").strip()) async for frame in stage]


def _pairs(events: list[dict]) -> list[tuple[str, str]]:
    return [(e["step"], e["status"]) for e in events]


def _errors(events: list[dict]) -> list[dict]:
    return [e for e in events if e["status"] == "error"]


# ---------------------------------------------------------------------------
# Seams
# ---------------------------------------------------------------------------

class _CriticLoopTestCase(unittest.IsolatedAsyncioTestCase):
    """Runs the real run_critic_loop() with its model, CAD and I/O seams stubbed."""

    def setUp(self) -> None:
        # Model seams (Gemini). Each Machinist attempt returns PART_CODE unless a test overrides it.
        self._patch(agents, "classify_part", new=mock.AsyncMock(return_value={"domain": "A"}))
        self.run_machinist = self._patch(
            agents, "run_machinist", new=mock.AsyncMock(return_value=PART_CODE)
        )

        # CAD seams. The sandbox reports success; STEP export returns a fake path.
        self.execute_cad_script = self._patch(
            agents, "execute_cad_script", new=mock.AsyncMock(return_value=SANDBOX_OK)
        )
        self.save_part_step = self._patch(
            agents, "_save_part_step", new=mock.AsyncMock(side_effect=_fake_step_path)
        )
        self._stub_geometry_module()

        # I/O seams. Telemetry, the retrieval index and LVM scoring are never written.
        self.log_attempt = self._patch(agents, "log_attempt")
        self._patch(agents, "add_to_index")
        self._patch(agents, "_score_and_log", new=mock.AsyncMock())
        self.write_span = self._patch(telemetry_mixin, "write_subagent_span")

    def _patch(self, owner, name: str, **kwargs):
        patcher = mock.patch.object(owner, name, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    def _stub_geometry_module(self) -> None:
        # run_critic_loop imports verification.geometry inside a try block. The stub
        # keeps the OCC-backed certificate off the test path.
        stub = types.ModuleType("verification.geometry")
        stub.certify_geometry = lambda step_path: {"passed": True, "step_path": step_path}
        previous = sys.modules.get("verification.geometry")
        sys.modules["verification.geometry"] = stub

        def _restore() -> None:
            if previous is None:
                sys.modules.pop("verification.geometry", None)
            else:
                sys.modules["verification.geometry"] = previous

        self.addCleanup(_restore)


class _OrchestratorTestCase(_CriticLoopTestCase):
    """Adds the planner and final-export sandbox seams used by the Orchestrator stages."""

    def setUp(self) -> None:
        super().setUp()
        self._patch(agents, "run_rea", new=mock.AsyncMock(return_value=None))
        self.run_planner = self._patch(agents, "run_planner", new=mock.AsyncMock())
        self.export_sandbox = self._patch(
            compiler, "execute_cad_script", new=mock.AsyncMock(return_value=SANDBOX_OK)
        )
        self.work_dir = Path(tempfile.gettempdir())

    def _orchestrator(self) -> Orchestrator:
        orch = Orchestrator(SubagentContext(prompt="test prompt"), work_dir=self.work_dir)
        # run() sets the output GLB path before its stages; these tests drive the stages directly.
        orch._output_filename = Path(self.work_dir, "mirum-test-output.glb").as_posix()
        return orch

    async def _plan(self, orch: Orchestrator, parts: list[_MockPart]) -> None:
        """Run the planner stage so the orchestrator holds ``parts`` as its manifest."""
        self.run_planner.return_value = _MockManifest(parts)
        events = await _drain(orch._stage_plan())
        self.assertIn(("planner", "done"), _pairs(events), events)


# ---------------------------------------------------------------------------
# Tests — MachinistSubagent
# ---------------------------------------------------------------------------

class TestMachinistSubagentResultContract(_CriticLoopTestCase):

    async def test_success_returns_ok_message_with_code_and_step_path(self):
        """A passing Critic Loop surfaces as an ok message with its code and STEP path."""
        ctx = SubagentContext(extra={"part_def": _MockPart("p1")})
        msg = await MachinistSubagent(max_retries=1, save_step=True).run(ctx)

        self.assertTrue(msg.is_ok, msg.metadata.get("error"))
        self.assertEqual(msg.payload["part_id"], "p1")
        self.assertEqual(msg.payload["code"], PART_CODE)
        self.assertEqual(msg.payload["step_path"], "/fake/steps/p1.step")
        self.assertEqual(msg.payload["code_len"], len(PART_CODE))
        self.assertEqual(self.write_span.call_args.args[0]["status"], "ok")

    async def test_save_step_false_returns_empty_step_path_without_step_export(self):
        """save_step=False skips the STEP export and reports an empty step_path."""
        ctx = SubagentContext(extra={"part_def": _MockPart("p1")})
        msg = await MachinistSubagent(max_retries=1, save_step=False).run(ctx)

        self.assertTrue(msg.is_ok, msg.metadata.get("error"))
        self.assertEqual(msg.payload["code"], PART_CODE)
        self.assertEqual(msg.payload["step_path"], "")
        self.save_part_step.assert_not_awaited()

    async def test_retry_after_failed_attempt_returns_code_from_passing_attempt(self):
        """A retried part reports the code that passed and feeds the failure back to the retry."""
        self.run_machinist.side_effect = [BAD_CODE, PART_CODE]
        self.execute_cad_script.side_effect = [
            {"status": "error",
             "traceback": "AttributeError: 'Workplane' object has no attribute 'boxx'"},
            SANDBOX_OK,
        ]
        self._patch(agents, "classify_failure", return_value="unknown")
        self._patch(agents, "build_retry_context", return_value=RETRY_CONTEXT)
        self._patch(agents, "get_active_rules", return_value=[])
        self._patch(agents, "extract_techniques", return_value=[])

        ctx = SubagentContext(extra={"part_def": _MockPart("p1")})
        msg = await MachinistSubagent(max_retries=2, save_step=True).run(ctx)

        self.assertTrue(msg.is_ok, msg.metadata.get("error"))
        self.assertEqual(msg.payload["code"], PART_CODE)
        self.assertEqual(
            self.run_machinist.await_args_list[1].kwargs["error_context"], RETRY_CONTEXT
        )
        self.assertEqual(
            [c.kwargs["success"] for c in self.log_attempt.call_args_list], [False, True]
        )


# ---------------------------------------------------------------------------
# Tests — CriticSubagent (same triple; critic.py:96 unpacks two)
# ---------------------------------------------------------------------------

class TestCriticSubagentResultContract(_CriticLoopTestCase):

    async def test_success_returns_ok_message_with_code_and_step_path(self):
        """CriticSubagent surfaces the same triple as an ok message."""
        ctx = SubagentContext(extra={"part_def": _MockPart("c1")})
        msg = await CriticSubagent(max_retries=1, save_step=True).run(ctx)

        self.assertTrue(msg.is_ok, msg.metadata.get("error"))
        self.assertEqual(msg.payload["part_id"], "c1")
        self.assertEqual(msg.payload["code"], PART_CODE)
        self.assertEqual(msg.payload["step_path"], "/fake/steps/c1.step")


# ---------------------------------------------------------------------------
# Tests — Orchestrator single-part and multi-part machine stages
# ---------------------------------------------------------------------------

class TestOrchestratorSingleAndMultiPartStages(_OrchestratorTestCase):

    async def test_single_part_machine_stage_exports_and_sandboxes_script(self):
        """Single-part: the validated code is exported, and the sandbox runs that export."""
        orch = self._orchestrator()
        await self._plan(orch, [_MockPart("p1")])

        events = await _drain(orch._stage_machine())

        self.assertEqual(_errors(events), [])
        self.assertIn(("machinist:p1", "done"), _pairs(events))
        self.assertIn(("export", "running"), _pairs(events))
        self.assertIsNot(orch.state, PipelineState.FAILED)
        # The script the client receives is the one the sandbox runs.
        self.assertTrue(orch._final_script.strip())
        self.assertEqual(
            [e.get("detail") for e in events if e["step"] == "script"], [orch._final_script]
        )
        self.export_sandbox.assert_awaited_once_with(orch._final_script)

    async def test_multi_part_machine_stage_completes_every_part(self):
        """Multi-part: every concurrent Machinist finishes and exports its STEP file."""
        orch = self._orchestrator()
        await self._plan(orch, [_MockPart("p1", "left bracket"), _MockPart("p2", "right bracket")])

        events = await _drain(orch._stage_machine())

        self.assertEqual(_errors(events), [])
        self.assertIn(("machinist:p1", "done"), _pairs(events))
        self.assertIn(("machinist:p2", "done"), _pairs(events))
        self.assertIsNot(orch.state, PipelineState.FAILED)
        self.assertEqual(
            sorted(c.args[0] for c in self.save_part_step.await_args_list), ["p1", "p2"]
        )

    async def test_multi_part_machine_stage_hands_step_files_to_assembler(self):
        """The STEP paths from the machine stage are the step_files the assembler receives."""
        orch = self._orchestrator()
        await self._plan(orch, [_MockPart("p1", "left bracket"), _MockPart("p2", "right bracket")])
        machine_events = await _drain(orch._stage_machine())
        self.assertEqual(_errors(machine_events), [])

        assemble = self._patch(
            agents, "run_assembly_critic_loop", new=mock.AsyncMock(return_value=ASSEMBLY_SCRIPT)
        )
        assembler_events = await _drain(orch._stage_assemble())

        self.assertEqual(_errors(assembler_events), [])
        self.assertIn(("assembler", "done"), _pairs(assembler_events))
        manifest, step_files = assemble.await_args.args
        self.assertIs(manifest, orch._manifest)
        self.assertEqual(
            step_files, {"p1": "/fake/steps/p1.step", "p2": "/fake/steps/p2.step"}
        )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print("=" * 60)
    print("Subagent result contract — run_critic_loop() return triple")
    print("=" * 60)
    unittest.main(verbosity=2)
