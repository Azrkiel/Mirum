"""Explicit opt-in API: approved typed requirements, no LLM or guessed physics."""
import asyncio
import base64
import logging

from fastapi import Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .spec import CantileverSpec
from .service import approved_candidates, generate_with_feedback

logger = logging.getLogger('mirum.engineering')
_slots = asyncio.Semaphore(1)


class EngineeringRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    specification: CantileverSpec
    candidate_heights_mm: list[float] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def validate_search_space(self):
        approved_candidates(self.specification, self.candidate_heights_mm)
        return self


def attach_engineering_routes(app, limiter):
    @app.post('/engineering/generate')
    @limiter.limit('2/minute')
    async def engineering_generate(body: EngineeringRequest, request: Request):
        """Accepted = this bounded digital/beam model passes, not engineering certification."""
        try:
            async with _slots:
                result = await generate_with_feedback(body.specification, body.candidate_heights_mm)
        except Exception:
            logger.exception('Required deterministic engineering validation failed')
            return {'status': 'error', 'detail': 'Required engineering generation or validation failed'}
        if result['status'] == 'accepted':
            result['step'] = base64.b64encode(result['step']).decode('ascii')
            result['glb'] = base64.b64encode(result['glb']).decode('ascii')
        return result
