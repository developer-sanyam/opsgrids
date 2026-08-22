"""One DSN, four roles: the username is the only thing that changes between them.

`dsn_as` exists for local development, where a single `.env` serves every service and the
bootstrap gives every role the same password. Production hands each process its own DSN and
leaves the role fields unset, so the rewrite must be a no-op there — a property worth pinning,
because a rewrite that touched anything but the username would quietly point a production
service at the wrong host.
"""

from __future__ import annotations

from platform_core.config import ServiceSettings

DSN = "postgresql+asyncpg://atmora:secret@db.internal:5432/atmora"


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


class TestDsnAs:
    def test_an_unset_role_leaves_the_dsn_untouched(self) -> None:
        """The production path: the DSN already carries the right credentials."""
        cfg = _Settings(database_url=DSN)
        assert cfg.app_dsn == DSN
        assert cfg.directory_dsn == DSN
        assert cfg.outbox_dsn == DSN
        assert cfg.admin_dsn == DSN

    def test_only_the_username_changes(self) -> None:
        cfg = _Settings(database_url=DSN, db_app_role="atmora_account")
        assert cfg.app_dsn == "postgresql+asyncpg://atmora_account:secret@db.internal:5432/atmora"

    def test_each_role_gets_its_own_dsn(self) -> None:
        """Four roles, four connections, one file — the split the policies depend on."""
        cfg = _Settings(
            database_url=DSN,
            db_app_role="atmora_account",
            db_admin_role="atmora_admin",
            db_directory_role="atmora_directory",
            db_outbox_role="atmora_outbox",
        )
        assert cfg.app_dsn.startswith("postgresql+asyncpg://atmora_account:")
        assert cfg.admin_dsn.startswith("postgresql+asyncpg://atmora_admin:")
        assert cfg.directory_dsn.startswith("postgresql+asyncpg://atmora_directory:")
        assert cfg.outbox_dsn.startswith("postgresql+asyncpg://atmora_outbox:")
        assert cfg.outbox_dsn == cfg.dsn_as("atmora_outbox")

    def test_the_outbox_role_is_unset_by_default(self) -> None:
        """A service that does not opt in gets the app engine — the logged-about fallback."""
        assert _Settings(database_url=DSN).db_outbox_role is None
