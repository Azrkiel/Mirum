"""Provider-free CEM/glider host: uvicorn cem.app:app --app-dir backend."""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from cem.api import attach_cem_routes, jobs
from gliders.api import attach_glider_routes, jobs as glider_jobs


@asynccontextmanager
async def lifespan(app):
    try:
        yield
    finally:
        await jobs.close()
        await glider_jobs.close()


app = FastAPI(title='Mirum physics-based glider CEM', lifespan=lifespan)
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
attach_cem_routes(app, limiter)
attach_glider_routes(app, limiter)
