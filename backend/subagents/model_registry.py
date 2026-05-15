"""
T4-10: ModelRegistry + hot-swap.
=================================

Single-source-of-truth for which Gemini model each subagent type uses.
Supports runtime hot-swap (no restart) via ``POST /admin/model-config``.

Design
------
* Singleton pattern: ``ModelRegistry.instance()`` always returns the same
  object regardless of where it is imported.
* Thread-safe updates via a simple ``threading.Lock`` — asyncio-safe too
  since GIL protects dict mutations and we hold the lock only while
  reassigning the per-agent config.
* Every swap is logged to the SubagentSpan telemetry sink (telemetry.py)
  with ``event_type="model_swap"``.

Default table (from config.py constants):

  agent_type    model              temperature  max_tokens  lite_fallback
  ----------    -----              -----------  ----------  -------------
  planner       GEMINI_HEAVY       0.3          4096        GEMINI_LITE
  machinist     GEMINI_HEAVY       0.2          8192        None
  critic        GEMINI_HEAVY       0.1          4096        None
  assembler     —  (no LLM)        n/a          n/a         None
  rea           GEMINI_LITE        0.2          2048        None
  clarifier     GEMINI_LITE        0.4          1024        None
  aero          —  (no LLM)        n/a          n/a         None
  refiner       GEMINI_HEAVY       0.3          8192        None
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import ClassVar


# ---------------------------------------------------------------------------
# ModelConfig dataclass
# ---------------------------------------------------------------------------

@dataclass
class ModelConfig:
    """Configuration for a single subagent's LLM backend.

    ``model_id = ""`` signals that the subagent makes no LLM calls
    (assembler, aero).
    """
    model_id: str              # e.g. "gemini-3-flash-preview" or "" for no-LLM agents
    temperature: float = 0.2
    max_tokens: int = 4096
    lite_fallback: str = ""    # model_id to use when budget is tight; "" = no fallback


# ---------------------------------------------------------------------------
# ModelRegistry singleton
# ---------------------------------------------------------------------------

_NO_LLM = ModelConfig(model_id="", temperature=0.0, max_tokens=0, lite_fallback="")


class ModelRegistry:
    """Per-agent model configuration registry.

    Usage::

        registry = ModelRegistry.instance()
        cfg = registry.get("planner")
        model_id = cfg.model_id

    Hot-swap (from the /admin/model-config endpoint)::

        registry.update("planner", model_id="gemini-3.1-flash-lite-preview",
                        temperature=0.4)
    """

    _instance: ClassVar[ModelRegistry | None] = None
    _lock: ClassVar[threading.Lock] = threading.Lock()

    def __init__(self) -> None:
        self._registry: dict[str, ModelConfig] = {}
        self._rw_lock = threading.Lock()
        self._init_defaults()

    def _init_defaults(self) -> None:
        """Populate the registry from config.py constants."""
        # Late import to avoid circular dependency at module load time.
        from config import GEMINI_MODEL_HEAVY, GEMINI_MODEL_LITE  # noqa: PLC0415

        self._registry = {
            "planner": ModelConfig(
                model_id=GEMINI_MODEL_HEAVY,
                temperature=0.3,
                max_tokens=4096,
                lite_fallback=GEMINI_MODEL_LITE,
            ),
            "machinist": ModelConfig(
                model_id=GEMINI_MODEL_HEAVY,
                temperature=0.2,
                max_tokens=8192,
                lite_fallback="",
            ),
            "critic": ModelConfig(
                model_id=GEMINI_MODEL_HEAVY,
                temperature=0.1,
                max_tokens=4096,
                lite_fallback="",
            ),
            "assembler": _NO_LLM,
            "rea": ModelConfig(
                model_id=GEMINI_MODEL_LITE,
                temperature=0.2,
                max_tokens=2048,
                lite_fallback="",
            ),
            "clarifier": ModelConfig(
                model_id=GEMINI_MODEL_LITE,
                temperature=0.4,
                max_tokens=1024,
                lite_fallback="",
            ),
            "aero": _NO_LLM,
            "refiner": ModelConfig(
                model_id=GEMINI_MODEL_HEAVY,
                temperature=0.3,
                max_tokens=8192,
                lite_fallback="",
            ),
        }

    # ------------------------------------------------------------------
    # Singleton access
    # ------------------------------------------------------------------

    @classmethod
    def instance(cls) -> "ModelRegistry":
        """Return the process-wide singleton, creating it on first call."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        """Reset singleton (test use only — wipes the instance)."""
        with cls._lock:
            cls._instance = None

    # ------------------------------------------------------------------
    # Read / write
    # ------------------------------------------------------------------

    def get(self, agent_type: str) -> ModelConfig:
        """Return the ModelConfig for *agent_type*.

        Falls back to ``_NO_LLM`` for unknown agent types so subagents
        never crash on registry misses.
        """
        with self._rw_lock:
            return self._registry.get(agent_type, _NO_LLM)

    def update(
        self,
        agent_type: str,
        *,
        model_id: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        lite_fallback: str | None = None,
    ) -> ModelConfig:
        """Hot-swap one agent's config.  Returns the new ModelConfig.

        Only fields explicitly passed are changed; others retain their
        current values.  Logs a swap event to telemetry.
        """
        with self._rw_lock:
            old = self._registry.get(agent_type, _NO_LLM)
            new = ModelConfig(
                model_id=model_id if model_id is not None else old.model_id,
                temperature=temperature if temperature is not None else old.temperature,
                max_tokens=max_tokens if max_tokens is not None else old.max_tokens,
                lite_fallback=lite_fallback if lite_fallback is not None else old.lite_fallback,
            )
            self._registry[agent_type] = new

        # Log swap event (best-effort — never raises)
        try:
            _log_model_swap(agent_type, old.model_id, new.model_id)
        except Exception:  # noqa: BLE001
            pass

        return new

    def snapshot(self) -> dict[str, dict]:
        """Return a JSON-serialisable snapshot of the full registry."""
        with self._rw_lock:
            return {
                agent_type: {
                    "model_id": cfg.model_id,
                    "temperature": cfg.temperature,
                    "max_tokens": cfg.max_tokens,
                    "lite_fallback": cfg.lite_fallback,
                }
                for agent_type, cfg in self._registry.items()
            }


# ---------------------------------------------------------------------------
# Internal telemetry helper
# ---------------------------------------------------------------------------

def _log_model_swap(agent_type: str, old_model: str, new_model: str) -> None:
    """Write a model_swap span to the subagent telemetry sink."""
    import time  # noqa: PLC0415
    try:
        from subagents.telemetry_mixin import write_subagent_span  # noqa: PLC0415
        write_subagent_span({
            "event_type": "model_swap",
            "agent_type": agent_type,
            "old_model": old_model,
            "new_model": new_model,
            "timestamp": time.time(),
        })
    except ImportError:
        pass  # telemetry_mixin may not exist yet; swap still succeeds
