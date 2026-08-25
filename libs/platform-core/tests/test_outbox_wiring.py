"""Every service's outbox wiring, audited as source — the three-legged failure, pinned.

The product relay stalled silently because three independent legs each looked optional:
its config named no `db_outbox_role` (so the outbox engine fell back to the RLS-bound app
engine), its relay drained through `get_sessionmaker()` (same fallback, deliberately), and
its `migrations/env.py` never passed `outbox_role=` to `apply_schema_grants` (so even a
correctly-wired relay would have hit permission-denied — employee's did). Nothing errored;
the outbox just filled.

These tests read the files rather than the database so they hold for **every** service in
one process — the per-service suites cannot see each other — and so the scaffolder
(`tools/new_service.py`) cannot regress: a new service is born with all three legs or this
fails in CI naming the file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
SERVICES = sorted(
    p for p in (REPO / "services").iterdir() if (p / "app").is_dir() and p.name != "__pycache__"
)


def _services_with(filename: str) -> list[Path]:
    return [s for s in SERVICES if (s / filename).exists()]


def test_the_survey_is_looking_at_something() -> None:
    assert len(SERVICES) > 20, "service discovery is broken; nothing below means anything"


@pytest.mark.parametrize(
    "service", _services_with("app/relay.py"), ids=lambda service: service.name
)
class TestRelayLegs:
    def test_config_names_the_outbox_role(self, service: Path) -> None:
        config = (service / "app/config.py").read_text()
        assert "db_outbox_role" in config, (
            f"{service.name}: no db_outbox_role — get_outbox_sessionmaker falls back to the "
            "RLS-bound app engine and the relay stalls silently (ADR-0002, 'Relay stall')"
        )

    def test_relay_drains_through_the_outbox_sessionmaker(self, service: Path) -> None:
        relay = (service / "app/relay.py").read_text()
        assert "init_outbox_engine" in relay and "get_outbox_sessionmaker" in relay, (
            f"{service.name}: the relay must drain through the outbox engine, never "
            "get_sessionmaker() — as the app role it selects nothing and loops forever"
        )
        assert "assert_outbox_role" in relay, (
            f"{service.name}: the relay must refuse to start on an RLS-bound role outside "
            "local development"
        )

    def test_migrations_grant_the_outbox_role(self, service: Path) -> None:
        env = service / "migrations/env.py"
        if not env.exists():
            pytest.skip("no migrations")
        text = env.read_text()
        assert "outbox_role=cfg.db_outbox_role" in text, (
            f"{service.name}: apply_schema_grants without outbox_role= means atmora_outbox "
            "holds no grant on this schema's outbox — the relay dies with permission denied"
        )


@pytest.mark.parametrize(
    "service", _services_with("app/consumers.py"), ids=lambda service: service.name
)
def test_consumers_assert_the_app_role(service: Path) -> None:
    consumers = (service / "app/consumers.py").read_text()
    assert "assert_app_role" in consumers, (
        f"{service.name}: a consumer started on a BYPASSRLS DSN binds the per-message GUC "
        "against policies that no longer bind it — RLS silently inert (ADR-0002)"
    )


class TestTheScaffolderIsBornCorrect:
    """`tools/new_service.py` writes the templates; a wrong template out-breeds any fix."""

    def test_the_generator_carries_all_three_legs(self) -> None:
        generator = (REPO / "tools/new_service.py").read_text()
        assert "outbox_role=cfg.db_outbox_role" in generator
        assert "get_outbox_sessionmaker" in generator
        assert "assert_outbox_role" in generator
        assert "assert_app_role" in generator
        assert 'db_outbox_role: str | None = "atmora_outbox"' in generator
