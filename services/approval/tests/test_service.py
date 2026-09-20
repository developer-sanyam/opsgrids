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


def test_every_request_type_constant_is_in_known_request_types() -> None:
    """`app/services.KNOWN_REQUEST_TYPES` is what `GET /policies` iterates and what
    `update_policy` refuses to configure anything outside of — a `REQUEST_TYPE_*` constant added
    to `app/services.py` without also appending it here would be unconfigurable (no `PUT` path
    could ever reach it, a field-level 422 every time), silently absent from the settings screen,
    and stuck at whatever the defaults happen to be forever. No endpoint or migration would fail
    on that mistake, which is exactly why it needs a test rather than only the module's own
    comment about "one new module-level constant, appended"."""
    from app import services

    constants = {
        value
        for name, value in vars(services).items()
        if name.startswith("REQUEST_TYPE_") and isinstance(value, str)
    }
    assert constants, "no REQUEST_TYPE_* constant found — this test would pass vacuously"
    assert constants == set(services.KNOWN_REQUEST_TYPES)


def test_the_escalated_lineage_outcome_matches_the_inbox_ctes_own_literal() -> None:
    """The eligibility lineage's vocabulary (`app/selectors._ELIGIBILITY_OUTCOMES`) and the
    `escalated_away` branches of `_INBOX_CANDIDATES_CTE` both speak of a step whose `outcome` is
    the literal `'escalated'` — one in Python, one baked into SQL text, with nothing that would
    otherwise catch the two drifting if either were ever renamed."""
    from app import selectors

    assert "escalated" in selectors._ELIGIBILITY_OUTCOMES
    assert "hs.outcome = 'escalated'" in selectors._INBOX_CANDIDATES_CTE
