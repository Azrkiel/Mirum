"""Provider-free capabilities and AI draft refusal on the release API."""
from __future__ import annotations

import pytest

from harness.api import Api

pytestmark = pytest.mark.usefixtures("evidence")


def test_capabilities_report_ai_disabled_and_unavailable(api: Api) -> None:
    response = api.get("/engineering/v1/capabilities")
    # backend/engineering/api_v1.py:205 uses the default GET status.
    assert response.status_code == 200, response.text
    capabilities = response.json()
    assert capabilities["ai_enabled"] is False
    assert capabilities["ai_available"] is False


def test_ai_draft_is_refused_before_preparation(api: Api) -> None:
    response = api.create_draft({
        "capability": "rectangular_cantilever",
        "mode": "ai",
        "prompt": "Prepare a rectangular cantilever for review",
    })
    # backend/engineering/api_v1.py:257-259 refuses AI before creating a draft.
    assert response.status_code == 503, response.text
    assert response.json() == {"detail": "AI draft preparation is not enabled"}
