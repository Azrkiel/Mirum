"""Explicit opt-in API: approved typed requirements, no LLM or guessed physics."""
import asyncio
import base64
import logging

from fastapi import APIRouter, Request, HTTPException
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .spec import CantileverSpec
from .service import approved_candidates, generate_with_feedback
from .admission import native_slot, AdmissionBusy
from .api_v1 import limit_body

logger = logging.getLogger('mirum.engineering')
_slots = asyncio.Semaphore(1)


class BodyLimitedRoute(APIRoute):
    """Same pre-parse body cap as /engineering/v1 (MIRUM_ENGINEERING_MAX_BODY_BYTES)."""
    def get_route_handler(self):
        handler = super().get_route_handler()
        async def guarded(request):
            await limit_body(request)
            return await handler(request)
        return guarded


class EngineeringRequest(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    specification: CantileverSpec
    candidate_heights_mm: list[float] = Field(default_factory=list, max_length=4)

    @model_validator(mode='after')
    def validate_search_space(self):
        approved_candidates(self.specification, self.candidate_heights_mm)
        return self


def attach_engineering_routes(app, limiter):
    from .regen_web import attach_regen_routes
    attach_regen_routes(app, limiter=limiter)

    router = APIRouter(route_class=BodyLimitedRoute)

    @router.post('/engineering/generate')
    @limiter.limit('2/minute')
    async def engineering_generate(body: EngineeringRequest, request: Request):
        """Accepted = this bounded digital/beam model passes, not engineering certification."""
        try:
            # No queue: a busy slot is refused at once instead of waiting on the semaphore.
            if _slots.locked():
                raise HTTPException(429, 'Engineering capacity busy', headers={'Retry-After': '5'})
            async with _slots:
                owner = object()
                # Shared fairness with the reviewed-job path (present when the v1 runtime is up).
                fairness = getattr(getattr(request.app.state, 'engineering', None), 'fairness', None)
                try:
                    client = str(getattr(limiter, '_key_func', lambda r: 'unknown')(request))
                except Exception:
                    client = 'unknown'
                try:
                    if fairness: fairness.check(client)       # hourly quota and turn rule
                    native_slot.claim(owner)
                except AdmissionBusy as busy:
                    if fairness and type(busy) is AdmissionBusy: fairness.refused(client)
                    raise HTTPException(429, 'Engineering capacity busy', headers={'Retry-After': str(busy.retry_after)}) from None
                if fairness: fairness.admitted(client)         # legacy runs spend the same hourly quota
                try:
                    result = await generate_with_feedback(body.specification, body.candidate_heights_mm)
                finally:
                    native_slot.release(owner)
        except HTTPException:
            raise
        except Exception:
            logger.exception('Required deterministic engineering validation failed')
            return {'status': 'error', 'detail': 'Required engineering generation or validation failed'}
        if result['status'] == 'accepted':
            result['step'] = base64.b64encode(result['step']).decode('ascii')
            result['glb'] = base64.b64encode(result['glb']).decode('ascii')
        return result

    app.include_router(router)
