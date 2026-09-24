"""Adopted from the security review's grace-window probe (fix round 1, M1 and MINOR 3).

The finding: the grace cache held the *live successor refresh token and access token, in
plaintext*, in a Redis with no password, reachable from every one of the ~34 service containers
on the compose network, and AOF-persisted to disk. Anyone who could read that one key could
rotate the session themselves. The redesign is marker-only — `auth:refresh_grace:{sha256(jti)}`
holds nothing but a presence flag — and a grace hit re-derives and re-validates the session, the
user and the membership from the spent token's own row, then mints a brand new access token and
nothing else: no refresh token, no `RefreshToken` row, no cookie.

A second finding rides along: two genuinely concurrent presentations of the same *unspent*
token (not the grace race, which needs one to have already rotated — the race where *neither*
has yet) used to be able to both read `is_spent = False` and both mint a successor, forking the
family. `rotate_refresh` now takes the row with `SELECT … FOR UPDATE`, so the second transaction
blocks until the first commits and then sees it as already spent.
"""

from __future__ import annotations

import asyncio
import json

import jwt
import pytest
from sqlalchemy import select

from app import services
from app.models import RefreshToken
from platform_core import cache
from platform_core.errors import UnauthorizedProblem
from tests.conftest import USER, FakeEntitlements
from tests.test_refresh_concurrency import _second_session
from tests.test_signin import _login

pytestmark = pytest.mark.integration


def _jti(raw: str) -> str:
    return jwt.decode(raw, options={"verify_signature": False})["jti"]


async def _rotate(session, cfg, pair, directory, raw):
    return await services.rotate_refresh(
        session,
        entitlements=FakeEntitlements(),
        cfg=cfg,
        pair=pair,
        directory=directory,
        raw_token=raw,
    )


class TestTheGraceCacheHoldsNoTokenMaterial:
    async def test_the_cache_key_is_not_the_plain_jti(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        out = await _login(session, cfg, pair, directory)
        assert out.issued is not None
        spent_jti = _jti(out.issued.refresh_token)
        await session.commit()

        await _rotate(session, cfg, pair, directory, out.issued.refresh_token)

        assert f"auth:refresh_grace:{spent_jti}" not in redis.store, (
            "the marker must be keyed by a hash of the jti, not the jti itself"
        )
        assert any(key.startswith("auth:refresh_grace:") for key in redis.store)

    async def test_the_cache_value_carries_no_token_and_no_user_identifying_payload(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        out = await _login(session, cfg, pair, directory)
        assert out.issued is not None
        spent = out.issued.refresh_token
        await session.commit()

        successor = await _rotate(session, cfg, pair, directory, spent)
        assert isinstance(successor, services.Issued)

        marker_values = [v for k, v in redis.store.items() if k.startswith("auth:refresh_grace:")]
        assert len(marker_values) == 1
        raw_value = marker_values[0]

        # Not a token, not JSON carrying one — and specifically not the successor this rotation
        # just minted, which is exactly what the old design stored here.
        assert successor.access_token not in raw_value
        assert successor.refresh_token not in raw_value
        assert str(USER) not in raw_value
        try:
            parsed = json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            assert "access_token" not in parsed
            assert "refresh_token" not in parsed

    async def test_a_grace_hit_answers_with_an_access_token_only_and_no_cookie_worthy_pair(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        out = await _login(session, cfg, pair, directory)
        assert out.issued is not None
        spent = out.issued.refresh_token
        await session.commit()

        winner = await _rotate(session, cfg, pair, directory, spent)
        assert isinstance(winner, services.Issued)

        grace = await _rotate(session, cfg, pair, directory, spent)
        assert isinstance(grace, services.AccessOnlyIssued)
        assert not hasattr(grace, "refresh_token")
        assert not hasattr(grace, "refresh_expires_at")
        assert grace.access_token != winner.access_token


class TestRedisOutageFailsClosed:
    async def test_a_replay_with_no_redis_reads_as_theft_not_grace(
        self, session, cfg, pair, directory, registered
    ) -> None:
        class Down:
            async def get(self, *args: object, **kwargs: object) -> None:
                raise ConnectionError("redis is down")

            async def set(self, *args: object, **kwargs: object) -> None:
                raise ConnectionError("redis is down")

        cache._redis = Down()  # type: ignore[assignment]
        try:
            out = await _login(session, cfg, pair, directory)
            assert out.issued is not None
            spent = out.issued.refresh_token
            await session.commit()

            await _rotate(session, cfg, pair, directory, spent)
            with pytest.raises(UnauthorizedProblem, match="security reasons"):
                await _rotate(session, cfg, pair, directory, spent)
        finally:
            cache._redis = None


class TestRevocationAlwaysBeatsGrace:
    async def test_revoke_all_for_user_blocks_a_grace_hit_that_would_otherwise_qualify(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        out = await _login(session, cfg, pair, directory)
        assert out.issued is not None
        spent = out.issued.refresh_token
        await session.commit()

        winner = await _rotate(session, cfg, pair, directory, spent)
        assert isinstance(winner, services.Issued)

        await services.revoke_all_for_user(session, USER, reason="password_changed")
        await session.commit()

        with pytest.raises(UnauthorizedProblem):
            await _rotate(session, cfg, pair, directory, spent)


class TestConcurrentFirstRotationDoesNotForkTheFamily:
    async def test_two_truly_concurrent_presentations_of_an_unspent_token_do_not_both_mint_a_token(
        self, session, cfg, pair, directory, registered, redis
    ) -> None:
        """MINOR 3: before the row lock, both connections could read `is_spent = False` at
        once and each mint a successor — two live chains from one predecessor. Now the second
        transaction's `SELECT … FOR UPDATE` blocks until the first commits, and sees the token
        already spent — landing on the grace path (both requests genuinely raced with nobody
        having rotated first, so both are inside the window) rather than forking anything."""
        out = await _login(session, cfg, pair, directory)
        assert out.issued is not None
        captured = out.issued.refresh_token
        await session.commit()

        a, ea = await _second_session()
        b, eb = await _second_session()
        try:
            ra, rb = await asyncio.gather(
                _rotate(a, cfg, pair, directory, captured),
                _rotate(b, cfg, pair, directory, captured),
            )
        finally:
            await a.close()
            await ea.dispose()
            await b.close()
            await eb.dispose()

        results = [ra, rb]
        refresh_tokens = [r for r in results if isinstance(r, services.Issued)]
        access_only = [r for r in results if isinstance(r, services.AccessOnlyIssued)]

        # Exactly one real rotation happened; the loser got the grace window's access-only
        # answer instead of a second, competing refresh token.
        assert len(refresh_tokens) == 1
        assert len(access_only) == 1

        # And the family is provably not forked: it has exactly one *live* (non-revoked,
        # not-yet-rotated-again) successor in the database.
        winner = refresh_tokens[0]
        winner_row = await session.get(RefreshToken, _jti(winner.refresh_token))
        assert winner_row is not None
        query = select(RefreshToken).where(RefreshToken.family == winner_row.family)
        rows = (await session.execute(query)).scalars().all()
        live = [row for row in rows if row.revoked_at is None and row.rotated_at is None]
        assert len(live) == 1
