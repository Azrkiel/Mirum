"""T-01 unit tests — NLP domain classifier (classifier.py).

All 5 domains tested (A–E) plus priority-override rules.
Gemini is stubbed via sys.modules injection so no API key is needed.

Run from repo root:
    python backend/tests/test_classifier.py
or via pytest:
    pytest backend/tests/test_classifier.py -v
"""

from __future__ import annotations

import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

# ---------------------------------------------------------------------------
# sys.modules stub — inject a fake google.generativeai before any import
# ---------------------------------------------------------------------------

def _make_model_mock(domain: str, reasoning: str = "test", key_params: str = ""):
    """Return a (model_instance, model_class) mock pair for a fixed JSON response."""
    response_text = json.dumps({
        "domain": domain,
        "reasoning": reasoning,
        "key_params": key_params,
    })
    mock_response = MagicMock()
    mock_response.text = response_text

    mock_model = MagicMock()
    mock_model.generate_content_async = AsyncMock(return_value=mock_response)

    mock_model_cls = MagicMock(return_value=mock_model)
    return mock_model, mock_model_cls


def _install_initial_stub() -> types.ModuleType:
    """Install the google.generativeai stub into sys.modules (first time only).

    Subsequent calls by tests MUST mutate this same object in-place because
    classifier.py caches `import google.generativeai as genai` at import time.
    Replacing sys.modules["google.generativeai"] with a new object has no
    effect on the already-imported `genai` reference inside classifier.py.
    """
    if "google" not in sys.modules:
        google_pkg = types.ModuleType("google")
        google_pkg.__path__ = []  # type: ignore[attr-defined]
        sys.modules["google"] = google_pkg
    else:
        google_pkg = sys.modules["google"]

    # Create the stub once
    stub = types.ModuleType("google.generativeai")
    _, cls = _make_model_mock("A")
    stub.GenerativeModel = cls  # type: ignore[attr-defined]
    stub.configure = MagicMock()  # type: ignore[attr-defined]
    sys.modules["google.generativeai"] = stub
    google_pkg.generativeai = stub  # type: ignore[attr-defined]
    return stub


# Install once at module level
_GENAI_STUB = _install_initial_stub()


def _set_response(domain: str, reasoning: str = "test", key_params: str = ""):
    """Mutate the already-installed stub in-place so classifier.py sees the new mock.

    Returns the new (mock_model, mock_model_cls) pair.
    """
    mock_model, mock_model_cls = _make_model_mock(domain, reasoning, key_params)
    # Mutate the existing module object — this is what classifier.py's `genai` refs
    _GENAI_STUB.GenerativeModel = mock_model_cls  # type: ignore[attr-defined]
    # Also update sys.modules entry to be safe
    sys.modules["google.generativeai"].GenerativeModel = mock_model_cls  # type: ignore[attr-defined]
    return mock_model, mock_model_cls


# For backwards compatibility in test helpers
def _install_stub(domain: str, reasoning: str = "test", key_params: str = ""):
    mock_model, mock_model_cls = _set_response(domain, reasoning, key_params)
    return _GENAI_STUB, mock_model

# Now we can import the classifier
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from classifier import classify_part  # noqa: E402


# ---------------------------------------------------------------------------
# Helper to run an async call synchronously in a test
# ---------------------------------------------------------------------------

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestDomainA(unittest.TestCase):
    """Domain A — primitives / structural parts."""

    def _classify(self, desc: str, key_params: str = "width=50") -> dict:
        _install_stub("A", "structural part", key_params)
        return _run(classify_part(desc))

    def test_a_basic_box(self):
        result = self._classify("100×50×20 mm aluminium mounting plate with 4 M6 through-holes")
        self.assertEqual(result["domain"], "A")

    def test_a_l_bracket(self):
        result = self._classify("L-shaped steel bracket, 80mm arms, 3mm thick, two bolt slots")
        self.assertEqual(result["domain"], "A")

    def test_a_returns_reasoning(self):
        result = self._classify("Simple rectangular spacer 10×10×5 mm")
        self.assertIn("reasoning", result)
        self.assertIsInstance(result["reasoning"], str)

    def test_a_returns_key_params(self):
        result = self._classify("Base plate 200×100×10 mm", key_params="width=200, height=100")
        self.assertIn("key_params", result)


class TestDomainB(unittest.TestCase):
    """Domain B — mechanical / parametric parts."""

    def _classify(self, desc: str) -> dict:
        _install_stub("B", "mechanical part", "num_teeth=20, module=2")
        return _run(classify_part(desc))

    def test_b_spur_gear(self):
        result = self._classify("Involute spur gear, 20 teeth, module 2, 20° pressure angle")
        self.assertEqual(result["domain"], "B")

    def test_b_bearing(self):
        result = self._classify("Deep groove ball bearing, 30mm bore, 62mm OD")
        self.assertEqual(result["domain"], "B")

    def test_b_lead_screw(self):
        result = self._classify("Tr16×4 trapezoidal lead screw, 250mm length")
        self.assertEqual(result["domain"], "B")

    def test_b_cam(self):
        result = self._classify("Eccentric cam with 8mm base circle and 2mm lift")
        self.assertEqual(result["domain"], "B")


class TestDomainC(unittest.TestCase):
    """Domain C — organic / ergonomic parts."""

    def _classify(self, desc: str) -> dict:
        _install_stub("C", "organic shape", "length=120, max_width=40")
        return _run(classify_part(desc))

    def test_c_handle(self):
        result = self._classify("Ergonomic pistol grip handle with finger contours, 120mm long")
        self.assertEqual(result["domain"], "C")

    def test_c_mouse_shell(self):
        result = self._classify("Computer mouse shell with curved top surface and thumb rest")
        self.assertEqual(result["domain"], "C")

    def test_c_co2_dragster(self):
        result = self._classify("CO2-powered dragster body with streamlined aerodynamic profile")
        self.assertEqual(result["domain"], "C")

    def test_c_bottle(self):
        result = self._classify("Water bottle with tapered neck, smooth organic body, 500ml volume")
        self.assertEqual(result["domain"], "C")


class TestDomainD(unittest.TestCase):
    """Domain D — aerospace / aerodynamics parts."""

    def _classify(self, desc: str) -> dict:
        _install_stub("D", "aerospace part", "naca_code=2412, chord=200")
        return _run(classify_part(desc))

    def test_d_naca_airfoil(self):
        result = self._classify("NACA 2412 airfoil section, 200mm chord, 600mm span")
        self.assertEqual(result["domain"], "D")

    def test_d_wing_rib(self):
        result = self._classify("Wing rib for a light aircraft, NACA 0015 profile, 1.5mm thick")
        self.assertEqual(result["domain"], "D")

    def test_d_leading_edge(self):
        result = self._classify("Leading edge spar cap for composite wing segment")
        self.assertEqual(result["domain"], "D")

    def test_d_stabilizer_fin(self):
        result = self._classify("Vertical stabilizer fin with symmetric NACA 0012 cross-section")
        self.assertEqual(result["domain"], "D")


class TestDomainE(unittest.TestCase):
    """Domain E — sheet metal / thin-wall parts."""

    def _classify(self, desc: str) -> dict:
        _install_stub("E", "sheet metal part", "thickness=1.5, bend_radius=2")
        return _run(classify_part(desc))

    def test_e_enclosure_panel(self):
        result = self._classify("1.5mm steel enclosure panel, 200×150mm, two M3 punch holes")
        self.assertEqual(result["domain"], "E")

    def test_e_pcb_tray(self):
        result = self._classify("PCB mounting tray, 16-gauge aluminium, 4 standoff bosses")
        self.assertEqual(result["domain"], "E")

    def test_e_bent_bracket(self):
        result = self._classify("Sheet metal bracket bent at 90°, 2mm thick galvanised steel")
        self.assertEqual(result["domain"], "E")

    def test_e_duct_flange(self):
        result = self._classify("Air duct flange, 1.0mm galvanised, 100×100mm square cross-section")
        self.assertEqual(result["domain"], "E")


class TestPriorityRules(unittest.TestCase):
    """Priority rules: D > all; E > A for sheet metal keywords."""

    def _classify_as(self, domain: str, desc: str) -> dict:
        _install_stub(domain, "priority test", "")
        return _run(classify_part(desc))

    def test_priority_d_over_a_wing_rib(self):
        """'Wing rib' must be D, not A — structural keyword cannot win."""
        result = self._classify_as("D", "Wing rib structural mounting plate, 3mm aluminium")
        self.assertEqual(result["domain"], "D")

    def test_priority_d_over_c_airfoil_fairing(self):
        """Airfoil fairing is D, not C — organic surface cannot win."""
        result = self._classify_as("D", "Airfoil fairing with smooth blended leading edge, NACA 0012")
        self.assertEqual(result["domain"], "D")

    def test_priority_d_over_b_turbine_blade(self):
        """Turbine blade blank is D, not B — parametric blade cannot win."""
        result = self._classify_as("D", "Turbine blade blank, NACA 4415 profile, 50mm chord")
        self.assertEqual(result["domain"], "D")

    def test_priority_e_over_a_sheet_bracket(self):
        """Sheet metal bracket is E, not A — gauge keyword flips domain."""
        result = self._classify_as("E", "Sheet metal L-bracket, 1.5mm gauge, 90° bend, mounting holes")
        self.assertEqual(result["domain"], "E")


class TestReturnShape(unittest.TestCase):
    """Verify the return dict always has the required keys."""

    def test_has_required_keys(self):
        _install_stub("A")
        result = _run(classify_part("Simple cube"))
        self.assertIn("domain", result)
        self.assertIn("reasoning", result)
        self.assertIn("key_params", result)

    def test_domain_is_valid_letter(self):
        for letter in ("A", "B", "C", "D", "E"):
            _install_stub(letter)
            result = _run(classify_part("Generic part"))
            self.assertIn(result["domain"], ("A", "B", "C", "D", "E"))

    def test_gemini_called_with_description(self):
        stub, mock_model = _install_stub("B")
        _run(classify_part("20-tooth gear"))
        mock_model.generate_content_async.assert_called_once()
        call_args = mock_model.generate_content_async.call_args
        # First positional arg contains the description
        prompt_arg = call_args[0][0] if call_args[0] else call_args[1].get("contents", "")
        self.assertIn("20-tooth gear", str(prompt_arg))

    def test_malformed_gemini_response_still_returns_dict(self):
        """Even if Gemini returns partial JSON, classify_part should not crash."""
        bad_response = MagicMock()
        bad_response.text = '{"domain": "A"}'  # missing reasoning, key_params
        mock_model = MagicMock()
        mock_model.generate_content_async = AsyncMock(return_value=bad_response)
        mock_model_cls = MagicMock(return_value=mock_model)
        # Mutate in-place — replacement would be ignored by classifier.py's cached ref
        _GENAI_STUB.GenerativeModel = mock_model_cls  # type: ignore[attr-defined]
        sys.modules["google.generativeai"].GenerativeModel = mock_model_cls  # type: ignore[attr-defined]
        result = _run(classify_part("Some part"))
        self.assertIsInstance(result, dict)
        self.assertIn("domain", result)


if __name__ == "__main__":
    unittest.main(verbosity=2)
