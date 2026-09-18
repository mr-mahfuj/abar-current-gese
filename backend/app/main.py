from __future__ import annotations

import os
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from typing import Any

from dotenv import load_dotenv
from fastapi import Body, FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.base import BaseHTTPMiddleware

from .guardrails import validate_and_fix
from .llm_interpreter import interpret_notes
from .optimizer import solve
from .replay import validate_plan
from .schemas import DirectiveInterpretation, OptimizeRequest, OptimizeResponse

load_dotenv()


def _round_plan(plan):
    return [
        entry.model_copy(
            update={
                "grid_kwh": round(entry.grid_kwh, 2),
                "solar_used_kwh": round(entry.solar_used_kwh, 2),
                "battery_kwh": round(entry.battery_kwh, 2),
                "battery_energy_after_kwh": round(entry.battery_energy_after_kwh, 2),
            }
        )
        for entry in plan
    ]


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


app = FastAPI(
    title="GridWise LLM Energy Optimizer",
    description=(
        "LLM-assisted campus energy scheduling API. Notes are interpreted by "
        "Gemini, validated deterministically, compiled into PuLP constraints, "
        "optimized, and replay-validated before response."
    ),
    version="1.0.0",
    lifespan=lifespan,
)
app.add_middleware(RateLimitMiddleware, requests_per_minute=int(os.getenv("RATE_LIMIT_PER_MINUTE", "60")))


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"detail": "invalid request", "errors": exc.errors()})


@app.exception_handler(Exception)
async def internal_exception_handler(_request: Request, _exc: Exception):
    return JSONResponse(status_code=500, content={"detail": "internal optimization error"})


@app.get(
    "/health",
    summary="Check service readiness",
    description="Returns ok when the HTTP service is ready to receive optimization requests.",
    response_description="Service readiness status.",
    tags=["system"],
)
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/optimize-energy",
    response_model=OptimizeResponse,
    summary="Interpret directives and optimize a 24-hour energy schedule",
    description=(
        "Submit one scenario with exactly 24 ordered hourly entries and 1-3 operator notes. "
        "The canonical request is the scenario object itself. A public sample wrapper containing "
        "input and expected_output is also accepted; expected_output is ignored."
    ),
    response_description="Validated directive interpretations and a replay-validated schedule.",
    tags=["optimization"],
    openapi_extra={
        "requestBody": {
            "content": {
                "application/json": {
                    "schema": {"$ref": "#/components/schemas/OptimizeRequest"},
                    "examples": {
                        "canonical": {
                            "summary": "Canonical scenario request",
                            "value": {
                                "scenario_id": "SAMPLE-01",
                                "operator_notes": ["Keep at least 50 kWh in the battery from 6 PM until 9 PM."],
                                "hours": "Paste 24 entries with hour values 0 through 23.",
                                "battery": {
                                    "capacity_kwh": 200,
                                    "initial_energy_kwh": 120,
                                    "minimum_energy_kwh": 40,
                                    "max_charge_kwh_per_hour": 50,
                                    "max_discharge_kwh_per_hour": 50,
                                },
                            },
                        },
                        "sample-pack-wrapper": {
                            "summary": "Public sample case wrapper",
                            "value": {
                                "input": "Paste a cases[n].input object here.",
                                "expected_output": "Optional; ignored by the API.",
                            },
                        },
                    },
                }
            }
        }
    },
)
async def optimize_energy(body: OptimizeRequest | dict[str, Any] = Body(...)) -> OptimizeResponse | JSONResponse:
    # Accept both the canonical request and the public sample-pack object copied with expected_output.
    request: OptimizeRequest | None = None
    if isinstance(body, OptimizeRequest):
        request = body
        payload = None
    else:
        payload = body.get("input", body)
    if not isinstance(payload, dict):
        if request is None:
            return JSONResponse(status_code=400, content={"detail": "invalid request"})
    else:
        payload = dict(payload)
        payload.pop("expected_output", None)
        try:
            request = OptimizeRequest.model_validate(payload)
        except ValidationError as exc:
            return JSONResponse(
                status_code=400,
                content={"detail": "invalid request", "errors": exc.errors()},
            )

    raw_directives = interpret_notes(request.operator_notes, request.battery.capacity_kwh)
    directives = validate_and_fix(raw_directives, len(request.operator_notes), request.battery.capacity_kwh)
    typed_directives = [DirectiveInterpretation.model_validate(directive) for directive in directives]
    plan, compiled = solve(request.hours, request.battery, typed_directives)
    plan = _round_plan(plan)
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
