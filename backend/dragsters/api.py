"""SSE adapter for the fixed deterministic workflow, independent of providers."""
import asyncio
import json

from .service import generate


def event(payload):
    return 'data: ' + json.dumps(payload, allow_nan=False) + '\n\n'


async def stream(spec, request, safe_error, runner=None):
    """StreamingResponse cancellation owns generation until native cleanup ends."""
    attempt_id = request.state.dragster_attempt_id
    yield event(dict(step='native', status='running', attempt_id=attempt_id,
                     message='Building and checking the wedge in a bounded native worker'))
    task = asyncio.create_task(generate(spec, runner=runner))
    delivered = False
    try:
        while not task.done():
            done, _ = await asyncio.wait({task}, timeout=0.25)
            if await request.is_disconnected():
                task.cancel()
                return
            if not done:
                yield ': native worker running\n\n'
        result = task.result()
        if await request.is_disconnected():
            # No accepted event was delivered; remove this unclaimed attempt.
            from .service import artifacts
            artifacts.discard(result['run_id'])
            return
        yield event(dict(step='complete', status='done', attempt_id=attempt_id, **result))
        delivered = True
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        yield event(dict(step='complete', status='error', attempt_id=attempt_id, message=safe_error(exc)))
    finally:
        # Shield repeated disconnect cancellation while the runner reaps children.
        if not task.done():
            task.cancel()
        cleanup = asyncio.gather(task, return_exceptions=True)
        interrupted = False
        while not cleanup.done():
            try:
                await asyncio.shield(cleanup)
            except asyncio.CancelledError:
                interrupted = True
        if not delivered and not task.cancelled() and task.exception() is None:
            from .service import artifacts
            artifacts.discard(task.result()["run_id"])
        if interrupted:
            raise asyncio.CancelledError
