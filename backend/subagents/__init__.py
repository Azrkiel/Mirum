"""
backend/subagents — Mirum subagent system (T4-01 through T4-10).

Public API
----------
From this package callers import:

    from subagents.messages import SubagentMessage, SubagentContext, new_trace_id
    from subagents.base import SubagentBase

Individual subagent modules are imported directly:

    from subagents.orchestrator import Orchestrator          # T4-02
    from subagents.planner import PlannerSubagent            # T4-03
    from subagents.machinist import MachinistSubagent        # T4-04
    from subagents.critic import CriticSubagent              # T4-05
    from subagents.assembler import AssemblerSubagent        # T4-06
    from subagents.rea import REASubagent                    # T4-07
    from subagents.clarifier import ClarifierSubagent        # T4-07
    from subagents.aero import AeroSubagent                  # T4-08
    from subagents.telemetry_mixin import TelemetryMixin     # T4-09
    from subagents.model_registry import ModelRegistry       # T4-10

Status (2026-05-14)
-------------------
  ✅ T4-01  SubagentBase + SubagentMessage + SubagentContext  [2026-05-14]
  ✅ T4-02  Orchestrator                                      [2026-05-14]
  ✅ T4-03  PlannerSubagent                                   [2026-05-14]
  ✅ T4-04  MachinistSubagent (parallel-safe)                 [2026-05-14]
  ✅ T4-05  CriticSubagent + AssemblyCriticSubagent           [2026-05-14]
  ✅ T4-06  AssemblerSubagent (single/translation/constraint) [2026-05-14]
  ✅ T4-07  REASubagent + ClarifierSubagent                   [2026-05-14]
  ✅ T4-08  AeroSubagent (Domain D, parallel with assembler)  [2026-05-14]
  ✅ T4-09  TelemetryMixin + write_subagent_span              [2026-05-14]
  ✅ T4-10  ModelRegistry + /admin/model-config endpoint      [2026-05-14]
"""

from subagents.messages import (  # noqa: F401
    AgentType,
    SubagentContext,
    SubagentMessage,
    new_trace_id,
)
from subagents.base import SubagentBase  # noqa: F401
