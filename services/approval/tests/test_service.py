"""The service boots and answers.

Thin on purpose — it exists so approval's factory wiring is proven at the same shape every other
service's `test_service.py` proves, and so a broken factory wiring fails here rather than in a
deploy. Does not need a database session: only the health and info surfaces, which the factory
serves without a request-scoped transaction.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_health_reports_this_service() -> None:
    with TestClient(app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "service": "approval"}


def test_info_is_mounted_under_the_versioned_prefix() -> None:
    # `/api/v1/approvals/info` — not `/approval/info` (singular): the gateway's route table
    # (`services/app-gateway/routes.json`) maps the prefix `/api/v1/approvals` (ADR-0006 §1,
    # §14), so a singular path is unreachable through the gateway even though it answers fine
    # against the service directly, which is exactly the trap that makes it worth pinning here.
    with TestClient(app) as client:
        response = client.get("/api/v1/approvals/info")
    assert response.status_code == 200
    assert response.json()["schema_name"] == "approval"


def test_unknown_route_returns_problem_json() -> None:
    # Every failure in the platform has the same shape, including 404s.
    with TestClient(app) as client:
        response = client.get("/api/v1/approvals/nope")
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("application/problem+json")
    assert "request_id" in response.json()
