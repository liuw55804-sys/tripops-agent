import time

from fastapi.testclient import TestClient

from tripops.api.main import app


def test_health() -> None:
    with TestClient(app) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def _trip_payload() -> dict[str, object]:
    return {
        "id": "api-trip",
        "origin": "Shanghai",
        "destinations": ["Kyoto"],
        "start_date": "2030-10-01",
        "end_date": "2030-10-05",
        "budget": "12000",
        "travelers": [{"id": "u1", "display_name": "Alice"}],
        "raw_requirement": "Plan an explainable Kyoto trip",
    }


def _wait_for_terminal(client: TestClient, run_id: str) -> dict[str, object]:
    for _ in range(50):
        payload = client.get(f"/v1/runs/{run_id}").json()
        if payload["status"] in {"completed", "failed", "waiting_approval"}:
            return payload
        time.sleep(0.01)
    raise AssertionError("run did not reach a terminal state")


def test_run_lifecycle_trace_and_sse() -> None:
    with TestClient(app) as client:
        started = client.post("/v1/runs", json={"request": _trip_payload()})
        assert started.status_code == 202
        run_id = started.json()["run_id"]

        completed = _wait_for_terminal(client, run_id)
        trace = client.get(f"/v1/runs/{run_id}/trace")
        with client.stream("GET", f"/v1/runs/{run_id}/events") as stream:
            event_stream = "".join(stream.iter_text())

    assert completed["status"] == "completed"
    assert completed["phase"] == "finish"
    assert completed["plan"]["revision"] == 1
    assert len(completed["plan"]["itinerary"]) == 15
    assert len(completed["evidence"]) == 5
    assert set(completed["selected_skills"]) == {
        "itinerary-optimization",
    }
    assert completed["trace_event_count"] > 0
    assert trace.status_code == 200
    assert any(event["name"] == "supervisor" for event in trace.json()["events"])
    assert "event: run_status" in event_stream


def test_completed_run_accepts_disruption_and_replans() -> None:
    with TestClient(app) as client:
        run_id = client.post("/v1/runs", json={"request": _trip_payload()}).json()["run_id"]
        initial = _wait_for_terminal(client, run_id)
        initial_ids = {item["id"] for item in initial["plan"]["itinerary"]}
        response = client.post(
            f"/v1/runs/{run_id}/disruptions",
            json={
                "event": {
                    "id": "storm-1",
                    "event_type": "severe_weather",
                    "description": "Typhoon disrupts rail services",
                    "locations": ["Kyoto"],
                    "starts_at": "2030-10-01T08:00:00Z",
                    "ends_at": "2030-10-01T11:00:00Z",
                    "required_capabilities": ["weather_search", "transport_search"],
                }
            },
        )
        assert response.status_code == 202
        completed = _wait_for_terminal(client, run_id)

    assert completed["status"] == "completed"
    assert completed["plan"]["revision"] == 2
    revised_ids = {item["id"] for item in completed["plan"]["itinerary"]}
    assert len(initial_ids & revised_ids) == 12
    assert len(revised_ids - initial_ids) == 3


def test_unknown_run_and_invalid_approval_are_explicit() -> None:
    with TestClient(app) as client:
        missing = client.get("/v1/runs/not-found")
        run_id = client.post("/v1/runs", json={"request": _trip_payload()}).json()["run_id"]
        _wait_for_terminal(client, run_id)
        conflict = client.post(
            f"/v1/runs/{run_id}/approval",
            json={"decision": {"approved": True, "decided_by": "tester"}},
        )

    assert missing.status_code == 404
    assert conflict.status_code == 409
