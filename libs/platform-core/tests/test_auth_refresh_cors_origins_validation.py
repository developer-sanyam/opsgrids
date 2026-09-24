"""MINOR 2 of the 2026-09-24 SSO review: `auth_refresh_cors_origins` is credentialed CORS for a
cookie-authenticated route, so a shape mistake here is a widened attack surface, not a typo that
404s. This pins the validator that refuses the unsafe shapes at startup, before any of them
could ever reach `_PathScopedCorsMiddleware`.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from platform_core.config import ServiceSettings


class _Settings(ServiceSettings):
    service_name: str = "probe"
    db_schema: str = "public"


def _cfg(origins: str) -> _Settings:
    return _Settings(auth_refresh_cors_origins=origins)


class TestValidShapesAreAccepted:
    def test_a_bare_https_origin(self) -> None:
        assert _cfg("https://people.opsgrids.com").auth_refresh_cors_origins == [
            "https://people.opsgrids.com"
        ]

    def test_several_origins(self) -> None:
        cfg = _cfg("https://people.opsgrids.com,https://revenue.opsgrids.com")
        assert cfg.auth_refresh_cors_origins == [
            "https://people.opsgrids.com",
            "https://revenue.opsgrids.com",
        ]

    def test_an_explicit_root_path_is_still_bare(self) -> None:
        assert _cfg("https://people.opsgrids.com/").auth_refresh_cors_origins == [
            "https://people.opsgrids.com/"
        ]

    def test_empty_is_the_default_and_valid(self) -> None:
        assert _Settings().auth_refresh_cors_origins == []


class TestUnsafeShapesAreRefused:
    def test_a_wildcard_is_refused(self) -> None:
        """Starlette's own CORSMiddleware echoes back any caller's Origin, with credentials,
        once a wildcard is configured — there is no safe reading of `*` here."""
        with pytest.raises(ValidationError, match="must not contain"):
            _cfg("*")

    def test_plain_http_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="https://"):
            _cfg("http://people.opsgrids.com")

    def test_a_path_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="bare origin"):
            _cfg("https://people.opsgrids.com/some/path")

    def test_a_query_string_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="bare origin"):
            _cfg("https://people.opsgrids.com/?x=1")

    def test_userinfo_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="bare origin"):
            _cfg("https://user:pass@people.opsgrids.com")

    def test_a_storefront_subdomain_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="storefront host"):
            _cfg("https://acme.stores.opsgrids.com")

    def test_the_bare_storefront_domain_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="storefront host"):
            _cfg("https://stores.opsgrids.com")

    def test_one_bad_entry_among_good_ones_still_refuses_the_whole_list(self) -> None:
        with pytest.raises(ValidationError, match="storefront host"):
            _cfg("https://people.opsgrids.com,https://evil.stores.opsgrids.com")

    def test_no_host_at_all_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            _cfg("https://")
