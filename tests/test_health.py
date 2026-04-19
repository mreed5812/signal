"""Tests for /api/health endpoint."""

from fastapi.testclient import TestClient


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["database"] == "connected"


def test_health_degraded(mock_db_disconnected, client: TestClient) -> None:
    # Override the connected mock with disconnected
    from unittest.mock import patch
    with patch("src.common.database.check_connectivity", return_value=False):
        resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "degraded"
