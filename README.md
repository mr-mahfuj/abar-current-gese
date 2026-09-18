# GridWise LLM

FastAPI backend for the BUP CSE Fest 2026 GridWise energy optimization challenge.

## Pipeline

`operator_notes` -> Gemini `gemini-2.5-flash` structured JSON -> deterministic guardrails -> PuLP/CBC optimizer -> replay validator -> JSON response.

The optimizer minimizes total grid cost while enforcing solar availability, battery limits, directive constraints, and end-of-day battery neutrality.

## Run locally

Python 3.11 is supported and recommended:

```bash
cd backend
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
# Set GEMINI_API_KEY in .env for Gemini-backed interpretation.
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

Configuration:

```env
GEMINI_API_KEY=your_google_ai_studio_key
GEMINI_MODEL=gemini-2.5-flash
RATE_LIMIT_PER_MINUTE=60
```

Without `GEMINI_API_KEY`, the service uses its controlled local fallback for development. Never commit `.env` or API keys.

## Swagger and API docs

With the server running:

- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- OpenAPI JSON: http://localhost:8000/openapi.json

Swagger documents `GET /health`, `POST /optimize-energy`, the `OptimizeRequest` input schema, and the `OptimizeResponse` output schema.

## Test the API

Health check:

```bash
curl -i http://localhost:8000/health
```

Expected response:

```json
{"status":"ok"}
```

Create a request from the first public case:

```bash
python - <<'PY'
import json
from pathlib import Path

pack = json.loads(Path("../prob-statement/BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json").read_text())
Path("/tmp/gridwise-request.json").write_text(json.dumps(pack["cases"][0]["input"]))
PY
```

Call the optimizer:

```bash
curl -i -X POST http://localhost:8000/optimize-energy \
  -H "Accept: application/json" \
  -H "Content-Type: application/json" \
  --data @/tmp/gridwise-request.json
```

The endpoint also accepts a complete public sample object containing `input` and `expected_output`; `expected_output` is ignored. Always send one JSON object, not two adjacent JSON objects.

Run the automated tests:

```bash
cd backend
PYTHONPATH=. pytest -q
```

The tests cover all public directive interpretations, wrapped sample requests, health, and malformed input. Invalid structure returns HTTP 400; excessive requests return HTTP 429.

## Docker

```bash
cd backend
docker build -t gridwise:local .
docker run --rm -p 8000:8000 --env-file .env gridwise:local
```

The container listens on `PORT` when provided and defaults to port 8000. It contains no credentials.

## Deployment

See [backend/deployment.md](backend/deployment.md) for Render, Railway, Docker, Gemini key, GHCR fallback image, and hackathon submission instructions.