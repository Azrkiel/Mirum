"""
T4-09: Subagent-level telemetry + trace IDs.
=============================================

Two exports:
  TelemetryMixin  — mixed into SubagentBase to add per-span JSONL recording
  write_subagent_span(record) — low-level JSONL writer used by ModelRegistry
                                and anything that wants to emit a raw span

SubagentSpan JSONL schema (one JSON object per line):
  {
    "event_type":   str,     # "span" | "model_swap" | ...
    "trace_id":     str,     # pipeline-level UUID4
    "parent_id":    str,     # spawning agent's trace_id, or ""
    "agent_type":   str,     # "planner" | "machinist" | ...
    "model":        str,     # model_id used, "" for no-LLM agents
    "latency_ms":   float,
    "tokens_in":    int,
    "tokens_out":   int,
    "status":       str,     # "ok" | "error" | "skipped"
    "timestamp":    float,   # Unix epoch (time.time())
  }

Output file: /tmp/mirum/telemetry/subagent_spans.jsonl
(separate from critic_loop.jsonl for independent jq querying)

jq one-liner for P50/P95 latency per agent:
  jq -s 'group_by(.agent_type)[] |
         {agent: .[0].agent_type,
          p50: (sort_by(.latency_ms) | .[floor(length*0.50)].latency_ms),
          p95: (sort_by(.latency_ms) | .[floor(length*0.95)].latency_ms)}' \
     /tmp/mirum/telemetry/subagent_spans.jsonl
"""

from __future__ import annotations

import json
import os
import pathlib
import threading
import time
from typing import Any

# ---------------------------------------------------------------------------
# JSONL sink
# ---------------------------------------------------------------------------

_SPAN_LOG_PATH = pathlib.Path(
    os.environ.get(
        "MIRUM_SUBAGENT_TELEMETRY",
        "/tmp/mirum/telemetry/subagent_spans.jsonl",
    )
)
_SINK_LOCK = threading.Lock()


def write_subagent_span(record: dict[str, Any]) -> None:
    """Append *record* as a single JSON line to the span log.

    Never raises — telemetry is best-effort.  If the directory doesn't
    exist (e.g. in unit tests) or the disk is full, the error is silently
    swallowed.
    """
    try:
        _SPAN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, default=str) + "\n"
        with _SINK_LOCK:
            with _SPAN_LOG_PATH.open("a", encoding="utf-8") as fh:
                fh.write(line)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# TelemetryMixin
# ---------------------------------------------------------------------------

class TelemetryMixin:
    """Mixin for SubagentBase — adds span recording.

    Usage (already wired into SubagentBase.run() via super() chain):

        class PlannerSubagent(TelemetryMixin, SubagentBase):
            ...

    The mixin exposes two methods meant to bracket a span:
      _span_start()  → call before the LLM call
      _span_end(msg) → call after, passing the final SubagentMessage

    SubagentBase.run() already captures timing and calls _emit_span()
    at the right moment — subclasses normally don't need to call these
    directly.
    """

    def _emit_span(
        self,
        *,
        trace_id: str,
        parent_id: str,
        agent_type: str,
        model: str,
        latency_ms: float,
        tokens_in: int,
        tokens_out: int,
        status: str,
    ) -> None:
        """Emit one SubagentSpan JSONL record."""
        write_subagent_span({
            "event_type": "span",
            "trace_id": trace_id,
            "parent_id": parent_id,
            "agent_type": agent_type,
            "model": model,
            "latency_ms": round(latency_ms, 2),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "status": status,
            "timestamp": time.time(),
        })
