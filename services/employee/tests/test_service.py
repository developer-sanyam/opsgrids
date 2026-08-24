"""The service boots and answers.

Thin on purpose — it exists so a freshly generated service is green from the first
commit, and so a broken factory wiring fails here rather than in a deploy.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_this_service() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "employee"}


def test_info_is_mounted_under_the_versioned_prefix() -> None:
    # The info route sits under the `/employees` prefix that routes.json points at :8120.
    with TestClient(app) as client:
        response = client.get("/api/v1/employees/info")
    assert response.status_code == 200
    assert response.json()["schema_name"] == "employee"


def test_unknown_route_returns_problem_json() -> None:
    # A path matching no route at all — `/employees/{id}` would instead 422 on a bad UUID.
    # Every failure in the platform has the same shape, including 404s.
    with TestClient(app) as client:
        response = client.get("/api/v1/no-such-resource")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "request_id" in response.json()
