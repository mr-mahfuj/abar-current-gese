from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from .guardrails import validate_and_fix
from .llm_interpreter import interpret_notes
from .optimizer import solve
from .replay import validate_plan
from .schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse

load_dotenv()


class RateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, requests_per_minute: int = 60):
        super().__init__(app)
        self.limit = max(1, requests_per_minute)
        self.requests: dict[str, deque[float]] = defaultdict(deque)

    async def dispatch(self, request: Request, call_next):
        if request.url.path != "/optimize-energy":
            return await call_next(request)
        now = time.monotonic()
        bucket = self.requests[request.client.host if request.client else "unknown"]
        while bucket and now - bucket[0] >= 60:
            bucket.popleft()
        if len(bucket) >= self.limit:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers={"Retry-After": "60"})
        bucket.append(now)
        return await call_next(request)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title="GridWise LLM", version="1.0.0", lifespan=lifespan)
app.add_middleware(RateLimitMiddleware, requests_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "60")))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "invalid request", "errors": exc.errors()})


@app.exception_handler(Exception)
async def internal_exception_handler(_request: Request, _exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "internal optimization error"})


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/optimize-energy", response_model=OptimizeResponse)
async def optimize_energy(request: OptimizeRequest) -> OptimizeResponse:
    raw_directives = interpret_notes(request.operator_notes, request.battery.capacity_kwh)
    directives = validate_and_fix(raw_directives, len(request.operator_notes), request.battery.capacity_kwh)
    typed_directives = [DirectiveInterpretation.model_validate(directive) for directive in directives]
    plan, compiled = solve(request.hours, request.battery, typed_directives)
    total_grid, total_cost, peak_grid = validate_plan(plan, request.hours, request.battery, compiled)
    return OptimizeResponse(
        scenario_id=request.scenario_id,
        directive_interpretation=typed_directives,
        hourly_plan=plan,
        total_grid_kwh=total_grid,
        total_cost_bdt=total_cost,
        peak_grid_kwh=peak_grid,
        plan_summary="Schedule minimizes grid cost while satisfying solar, battery, and operator directives.",
    )
