"""Deploy-phase tests: single-invocation SSE stream + stateless export."""
from __future__ import annotations

import json

from starlette.testclient import TestClient

from backend.app.config import settings
from backend.app.main import app

DEMO_CACHE = settings.demo_cache_dir


def _client() -> TestClient:
    return TestClient(app)


def test_stream_requires_resume_when_not_demo():
    with _client() as client:
        resp = client.post("/api/runs/stream", data={"demo_mode": "false"})
    assert resp.status_code == 422


def test_stream_endpoint_replays_demo_and_returns_result(monkeypatch):
    monkeypatch.setattr(settings, "demo_event_delay", 0.0)
    assert (DEMO_CACHE / "events.json").exists() and (DEMO_CACHE / "result.json").exists()

    run_created = None
    events = 0
    stream_end = None
    with _client() as client:
        with client.stream(
            "POST", "/api/runs/stream", data={"demo_mode": "true"}
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            for line in resp.iter_lines():
                if not line.startswith("data:"):
                    continue  # heartbeat comments are ignored
                payload = json.loads(line[5:])
                if payload.get("type") == "run_created":
                    run_created = payload
                elif payload.get("type") == "stream_end":
                    stream_end = payload
                    break
                else:
                    events += 1

    assert run_created is not None and run_created["run_id"].startswith("demo-")
    assert events >= 10  # replayed agent events, not a single blob
    assert stream_end is not None
    assert stream_end["status"] == "done"
    result = stream_end["result"]
    assert result.get("profile", {}).get("name")
    assert result.get("ranked"), "demo result must carry the top 5"
    assert result.get("candidates"), "demo result must carry discovered candidates"


def test_export_stateless_uses_state_from_body():
    state = {"profile": {"name": "Rahul Sharma", "degree": "B.Tech CSE"},
             "ranked": [], "candidates": []}
    with _client() as client:
        resp = client.post(
            "/api/runs/unknown-run/export", json={"state": state}
        )
    assert resp.status_code == 200
    assert "text/markdown" in resp.headers["content-type"]
    assert "ScholarSwarm Report" in resp.text
    assert "Rahul Sharma" in resp.text


def test_export_unknown_run_without_state_404s():
    with _client() as client:
        resp = client.post("/api/runs/unknown-run/export", json={})
    assert resp.status_code == 404
