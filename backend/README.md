# GridWise LLM Backend

Containerized FastAPI service for the BUP CSE Fest 2026 GridWise challenge.

## Architecture

`operator_notes` flow through Gemini 2.0 Flash with a forced JSON response schema, deterministic guardrails, a PuLP/CBC linear program, and a pure replay validator before the response is returned. Gemini failures are retried once and then use a conservative local fallback so the optimizer still returns a valid base schedule.

The LP minimizes `sum(grid_kwh * tariff_bdt_per_kwh)` while enforcing solar availability, battery transitions, reserve/capacity, charge/discharge rates, end-of-day neutrality, and all validated directives.

## Local setup

Python 3.11 is the supported runtime:

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Set `GEMINI_API_KEY` in `.env` for the hosted interpreter. `GEMINI_MODEL` defaults to `gemini-2.0-flash`; `RATE_LIMIT_PER_MINUTE` defaults to 60 per client IP. Do not commit `.env` or credentials.

## API

```bash
curl http://localhost:8000/health
# {"status":"ok"}
curl -X POST http://localhost:8000/optimize-energy \
  -H 'content-type: application/json' \
  --data @../prob-statement/sample-request.json
```

Malformed or structurally invalid JSON returns 400. The service returns 429 when a client exceeds the configured rate limit and does not expose provider errors or stack traces.

## Tests

```bash
pytest -q
```

The public sample test checks every sample's directive semantics and validates that each response has a 24-hour plan. The test suite also covers health and malformed input handling.

## Docker

```bash
docker build -t gridwise:local .
docker run --rm -p 8000:8000 --env-file .env gridwise:local
```

The image binds to `0.0.0.0:8000`, contains no credentials, and is suitable for Render, GHCR, or another public container platform.

## Limitations

The local fallback is intended for provider outage and local reproducibility. For the highest paraphrase accuracy during judging, configure a valid Google AI Studio key and sufficient quota.