import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


SAMPLES = Path(__file__).parents[2] / "prob-statement" / "BUP_CSE_FEST_2026_Preli_Public_Sample_Cases.json"


def test_public_samples_are_valid_and_match_directive_semantics():
    payload = json.loads(SAMPLES.read_text())
    with TestClient(app) as client:
        for case in payload["cases"]:
            response = client.post("/optimize-energy", json=case["input"])
            assert response.status_code == 200, (case["id"], response.text)
            result = response.json()
            expected = case["expected_output"]["directive_interpretation"]
            actual = result["directive_interpretation"]
            assert len(actual) == len(expected)
            for got, want in zip(actual, expected):
                assert got["note_index"] == want["note_index"]
                assert got["applies"] == want["applies"]
                assert got["directive_type"] == want["directive_type"]
                assert got["structured_adjustment"] == want["structured_adjustment"]
            assert len(result["hourly_plan"]) == 24
            assert result["total_grid_kwh"] >= 0
            assert result["total_cost_bdt"] >= 0
            assert abs(result["total_cost_bdt"] - case["expected_output"]["total_cost_bdt"]) <= 0.01
            assert abs(result["peak_grid_kwh"] - case["expected_output"]["peak_grid_kwh"]) <= 0.01


def test_malformed_request_returns_400():
    with TestClient(app) as client:
        response = client.post("/optimize-energy", json={"scenario_id": "bad"})
    assert response.status_code == 400


def test_public_case_wrapper_is_accepted():
    payload = json.loads(SAMPLES.read_text())
    case = payload["cases"][0]
    with TestClient(app) as client:
        response = client.post(
            "/optimize-energy",
            json={"input": case["input"], "expected_output": case["expected_output"]},
        )
    assert response.status_code == 200
    assert response.json()["scenario_id"] == case["input"]["scenario_id"]


def test_health():
    with TestClient(app) as client:
        assert client.get("/health").json() == {"status": "ok"}