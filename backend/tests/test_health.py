from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_returns_contract_shape():
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"cameras", "db", "twilio", "llm"}
    assert body["cameras"] == []
