"""Tests that the ModelRegistry actually drives live Gemini calls (P2.1/P2.2).

Before this wiring every call ran at the API's default sampling (~1.0
temperature) and the /admin/model-config hot-swap endpoint changed nothing
at runtime — the registry was a parallel architecture wired to no one.

Captures the model_name and generation_config handed to the SDK by
monkeypatching genai.GenerativeModel, so no API key or network is needed.

    python -m pytest backend/tests/test_model_registry_wiring.py
"""

import asyncio
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
os.environ.setdefault("GEMINI_API_KEY_HEAVY", "test-key")

import agents  # noqa: E402
from schemas import PartDefinition  # noqa: E402
from subagents.model_registry import ModelRegistry  # noqa: E402


class _Capture:
    """Records what the SDK was asked to do."""

    def __init__(self):
        self.calls: list[dict] = []


@pytest.fixture
def captured(monkeypatch):
    cap = _Capture()
    outer = cap

    class FakeModel:
        def __init__(self, model_name=None, system_instruction=None, **kw):
            self.model_name = model_name

        async def generate_content_async(self, prompt, generation_config=None, **kw):
            outer.calls.append({
                "model_name": self.model_name,
                "generation_config": generation_config or {},
            })

            class _Resp:
                text = '{"ambiguities": []}'
                usage_metadata = None
            return _Resp()

    monkeypatch.setattr(agents.genai, "GenerativeModel", FakeModel)
    return cap


_PART = PartDefinition(part_id="p", description="a 10mm cube", anchor_tags=[">Z"])


def _run_machinist(**kw):
    return asyncio.run(agents.run_machinist(_PART, domain="A", **kw))


# ---------------------------------------------------------------------------
# Temperature actually reaches the SDK
# ---------------------------------------------------------------------------

def test_machinist_uses_registry_temperature_not_api_default(captured, monkeypatch):
    monkeypatch.setattr(agents, "retrieve_few_shots", lambda *a, **k: [])
    _run_machinist()
    cfg = captured.calls[0]["generation_config"]
    assert cfg["temperature"] == 0.2, (
        "machinist must run at the registry temperature; the API default "
        "(~1.0) is far too loose for code generation"
    )
    assert cfg["max_output_tokens"] == 8192


def test_clarifier_uses_its_own_temperature(captured):
    asyncio.run(agents.run_clarifier("make me a bracket"))
    assert captured.calls[0]["generation_config"]["temperature"] == 0.4


def test_structured_output_survives_the_merge(captured):
    """Sampling settings must not clobber response_schema / mime type."""
    asyncio.run(agents.run_clarifier("make me a bracket"))
    cfg = captured.calls[0]["generation_config"]
    assert cfg["response_mime_type"] == "application/json"
    assert "ambiguities" in cfg["response_schema"]["properties"]
    assert cfg["temperature"] == 0.4


def test_every_wired_agent_sends_a_temperature(captured, monkeypatch):
    """No live call should fall back to the API default."""
    monkeypatch.setattr(agents, "retrieve_few_shots", lambda *a, **k: [])
    _run_machinist()
    asyncio.run(agents.run_clarifier("x"))
    assert captured.calls, "no calls captured"
    for call in captured.calls:
        assert "temperature" in call["generation_config"], call["model_name"]


# ---------------------------------------------------------------------------
# P2.2 — Pro escalation on the final retry only
# ---------------------------------------------------------------------------

def test_first_attempt_uses_heavy(captured, monkeypatch):
    monkeypatch.setattr(agents, "retrieve_few_shots", lambda *a, **k: [])
    _run_machinist(is_final_retry=False)
    assert captured.calls[0]["model_name"] == agents.MODEL


def test_final_retry_escalates_to_pro(captured, monkeypatch):
    monkeypatch.setattr(agents, "retrieve_for_failure", lambda **k: [])
    _run_machinist(is_final_retry=True, error_context="boom",
                   retry_failure_class="fillet")
    call = captured.calls[0]
    assert call["model_name"] == agents.MODEL_PRO
    # Pro escalation should also bring the tighter critic temperature.
    assert call["generation_config"]["temperature"] == 0.1


def _stub_loop(monkeypatch, compile_results):
    """Stub every boundary of run_critic_loop except model selection."""
    flags = []
    results = iter(compile_results)

    async def spy(part_def, domain="A", error_context="", retry_failure_class=None,
                  retry_techniques=None, is_final_retry=False):
        flags.append(is_final_retry)
        return "import cadquery as cq" + chr(10) + "result = cq.Workplane().box(1,1,1)"

    async def fake_exec(_code):
        r = next(results)
        return {"status": "success", "output": ""} if r is True else {
            "status": "error", "traceback": r}

    async def fake_classify(_d):
        return {"domain": "A"}

    async def fake_score(*_a, **_k):
        return None

    monkeypatch.setattr(agents, "run_machinist", spy)
    monkeypatch.setattr(agents, "execute_cad_script", fake_exec)
    monkeypatch.setattr(agents, "classify_part", fake_classify)
    monkeypatch.setattr(agents, "log_attempt", lambda **k: None)
    monkeypatch.setattr(agents, "add_to_index", lambda **k: None)
    monkeypatch.setattr(agents, "_score_and_log", fake_score)
    return flags


def test_single_attempt_budget_does_not_pay_pro_prices(monkeypatch):
    """max_retries=1 means attempt 1 is 'final' - but there is no failure
    history to reason about, so escalating would just be a price increase."""
    flags = _stub_loop(monkeypatch, [True])
    asyncio.run(agents.run_critic_loop(_PART, max_retries=1, save_step=False))
    assert flags == [False]


def test_only_the_last_attempt_escalates_to_pro(monkeypatch):
    flags = _stub_loop(monkeypatch, ["boom", "boom", True])
    asyncio.run(agents.run_critic_loop(_PART, max_retries=3, save_step=False))
    assert flags == [False, False, True], flags


# ---------------------------------------------------------------------------
# Hot-swap now has teeth
# ---------------------------------------------------------------------------

def test_admin_hot_swap_changes_the_next_call(captured, monkeypatch):
    """The /admin/model-config endpoint edits this registry; before the
    wiring, a swap changed nothing about what the pipeline actually sent."""
    monkeypatch.setattr(agents, "retrieve_few_shots", lambda *a, **k: [])
    registry = ModelRegistry.instance()
    original = registry.get("machinist")
    try:
        registry.update("machinist", temperature=0.9, model_id="swapped-model")
        _run_machinist()
        call = captured.calls[0]
        assert call["model_name"] == "swapped-model"
        assert call["generation_config"]["temperature"] == 0.9
    finally:
        registry.update("machinist", temperature=original.temperature,
                        model_id=original.model_id)


def test_registry_miss_falls_back_to_the_caller_default():
    """A miss must never send an empty model name to the API."""
    model_id, gen = agents._agent_model("no_such_agent", "fallback-model")
    assert model_id == "fallback-model"
    assert gen == {}


def test_classifier_is_deterministic():
    _, gen = agents._agent_model("classifier", "x")
    assert gen["temperature"] == 0.0, (
        "domain routing picks the strategy prompt; it must not be sampled"
    )
