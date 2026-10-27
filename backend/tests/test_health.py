from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_contract_shape():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"cameras", "db", "twilio", "llm", "calls_mode", "calls_error"}
    assert sorted(cam["id"] for cam in body["cameras"]) == ["cam-phone", "cam-webcam"]
    assert all(set(cam) == {"id", "online", "fps"} for cam in body["cameras"])
    assert body["db"] in {"ok", "error"}


def test_health_says_whether_calls_are_real_and_the_last_failure(monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from app.main import app
    from app.services.calls import calls

    monkeypatch.setattr(calls.settings, "calls_mode", "simulate")
    monkeypatch.setattr(calls, "last_error", (time.monotonic(), "21215: Geo permission for +91 is off"))
    body = TestClient(app).get("/health").json()
    assert body["calls_mode"] == "simulate"
    assert body["calls_error"].startswith("21215")
    monkeypatch.setattr(calls, "last_error", (time.monotonic() - 700, "old"))
    assert TestClient(app).get("/health").json()["calls_error"] is None  # only the last 10 minutes
