"""The theme document: `GET/PUT/DELETE /storefront/theme` (ADR-0011 §3, gap 2).

Reuses `storefront.store.settings.*` — no new permission code — per ADR-0011's own note:
anyone who can publish the store can edit its theme.
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import all_perms, seed_theme_document

pytestmark = pytest.mark.integration

THEME = "/api/v1/storefront/theme"


def _payload(**overrides: object) -> dict[str, object]:
    return {
        "theme_key": "market-fresh",
        "mode": "retail",
        "tokens": {"colorBg": "#ffffff"},
        "pages": [{"id": "home", "route": "/", "name": "Home", "sections": []}],
        "version": 1,
        **overrides,
    }


class TestReadingWithNoTheme:
    async def test_get_with_no_theme_is_a_clear_404(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            response = await client.get(THEME)
        assert response.status_code == 404
        assert "theme" in response.json()["detail"].lower()


class TestAdoptAndSave:
    async def test_put_creates_and_get_reads_it_back(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            created = await client.put(THEME, json=_payload())
            read = await client.get(THEME)

        assert created.status_code == 200
        assert created.json()["version"] == 1
        assert read.json()["theme_key"] == "market-fresh"
        assert read.json()["pages"][0]["route"] == "/"

    async def test_a_second_save_increments_the_version(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(THEME, json=_payload())
            saved_again = await client.put(THEME, json=_payload(version=1, mode="trade"))

        assert saved_again.status_code == 200
        assert saved_again.json()["version"] == 2
        assert saved_again.json()["mode"] == "trade"

    async def test_a_stale_version_is_a_named_409(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(THEME, json=_payload())
            stale = await client.put(THEME, json=_payload(version=1, mode="trade"))
            conflict = await client.put(THEME, json=_payload(version=1))

        assert stale.status_code == 200
        assert conflict.status_code == 409
        assert conflict.json()["errors"][0]["code"] == "theme_version_conflict"

    async def test_an_oversized_document_is_a_422(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        huge_pages = [
            {"id": str(i), "route": f"/{i}", "name": "x" * 2000, "sections": []} for i in range(500)
        ]
        with as_principal(all_perms(org_id)):
            response = await client.put(THEME, json=_payload(pages=huge_pages))
        assert response.status_code == 422

    async def test_removing_it_is_soft_and_a_fresh_adopt_starts_at_version_one(
        self, client, as_principal, org_id: uuid.UUID
    ) -> None:
        with as_principal(all_perms(org_id)):
            await client.put(THEME, json=_payload())
            removed = await client.delete(THEME)
            missing = await client.get(THEME)
            readopted = await client.put(THEME, json=_payload())

        assert removed.status_code == 204
        assert missing.status_code == 404
        assert readopted.json()["version"] == 1


class TestCrossTenantIsolation:
    async def test_another_organizations_theme_is_invisible(
        self, client, as_principal, admin_session, org_id: uuid.UUID, other_org: uuid.UUID
    ) -> None:
        await seed_theme_document(admin_session, other_org)

        with as_principal(all_perms(org_id)):
            response = await client.get(THEME)

        assert response.status_code == 404
