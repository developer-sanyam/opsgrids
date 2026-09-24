"""Redis: caching, rate limiting, and the permission-version check.

One rule governs everything here, and it is not symmetric:

**The cache degrades; the limiter and the version check do not.** A cache miss caused by a
Redis outage falls through to the source of truth and the request succeeds. A *rate limit*
that fails open lets an outage become an amplification vector, and a *permission version*
that fails open lets a revoked grant keep working. Those two fail closed, loudly.
"""

from __future__ import annotations

import time
from typing import Any

from redis.asyncio import Redis

from platform_core.errors import RateLimitedProblem, UpstreamUnavailableProblem
from platform_core.logging import get_logger

log = get_logger("cache")

_redis: Redis | None = None


def init_redis(url: str) -> Redis:
    """Create the process-wide client. Idempotent."""
    global _redis
    if _redis is None:
        _redis = Redis.from_url(url, decode_responses=True)
    return _redis


def get_redis() -> Redis:
    if _redis is None:
        raise RuntimeError("init_redis() has not been called — is this inside the lifespan?")
    return _redis


async def close_redis() -> None:
    global _redis
    if _redis is not None:
        await _redis.aclose()
    _redis = None


# --------------------------------------------------------------------- soft cache


async def cache_get(key: str) -> str | None:
    """Read, or act as though it were a miss.

    Swallowing the error is the point: the caller's fallback is the source of truth, so a
    Redis outage should slow the platform down rather than take it down.
    """
    try:
        value = await get_redis().get(key)
        return str(value) if value is not None else None
    except Exception as exc:
        log.warning("cache.get.failed", key=key, error=str(exc)[:120])
        return None


async def cache_set(key: str, value: str, *, ttl: int = 300) -> None:
    try:
        await get_redis().set(key, value, ex=ttl)
    except Exception as exc:
        log.warning("cache.set.failed", key=key, error=str(exc)[:120])


async def cache_delete(*keys: str) -> None:
    if not keys:
        return
    try:
        await get_redis().delete(*keys)
    except Exception as exc:
        log.warning("cache.delete.failed", error=str(exc)[:120])


# --------------------------------------------------------------------- one-shot reservations


async def reserve_once(key: str, *, ttl: int) -> bool:
    """`SET key 1 NX EX ttl`: `True` when this call is the first to claim `key`, `False` when
    someone already has. The primitive behind "valid once" — auth's operator routes use it to
    refuse a replayed admin-gateway hop nonce.

    **Fails closed**, unlike `cache_get`/`cache_set`: those are caches whose fallback is the
    source of truth, so swallowing an outage is right. Here the reservation *is* the check, and
    treating "Redis is down" as "not seen before" would let a replay through exactly when the
    store that would have caught it is unavailable.
    """
    try:
        claimed = await get_redis().set(key, "1", ex=ttl, nx=True)
    except Exception as exc:
        log.error("reserve_once.unavailable", error=str(exc)[:120])
        raise UpstreamUnavailableProblem(
            "Cannot verify this request right now. Please retry shortly."
        ) from exc
    return bool(claimed)


# --------------------------------------------------------------------- rate limiting


async def enforce_rate_limit(
    key: str,
    *,
    limit: int,
    window_seconds: int = 60,
    detail: str = "Too many requests. Please slow down.",
) -> None:
    """Fixed-window counter. Raises 429 when the window is spent.

    `detail` is what the 429 says. A bucket that is not the caller's own — the gateway's
    global ceiling on anonymous traffic — should not read as though the caller did something,
    so it says what actually happened instead.

    A fixed window lets a caller spend two windows' worth across a boundary; a sliding
    window costs more state for a bound nobody is relying on this layer to enforce
    precisely. The edge WAF is the first line — this is the last.

    **Fails closed.** If Redis is unreachable the limit cannot be evaluated, and treating
    "unknown" as "allowed" turns an outage into an amplifier.
    """
    bucket = f"rl:{key}:{int(time.time()) // window_seconds}"
    try:
        redis = get_redis()
        count = await redis.incr(bucket)
        if count == 1:
            # Two windows of slack so a clock skew cannot orphan the key.
            await redis.expire(bucket, window_seconds * 2)
    except Exception as exc:
        log.error("ratelimit.unavailable", error=str(exc)[:120])
        raise UpstreamUnavailableProblem(
            "Cannot verify request limits right now. Please retry shortly."
        ) from exc

    if count > limit:
        problem = RateLimitedProblem(detail)
        problem.headers = {"Retry-After": str(window_seconds)}
        raise problem


# --------------------------------------------------------------------- permission version


def permission_version_key(organization_id: str) -> str:
    return f"pv:{organization_id}"


async def current_permission_version(organization_id: str) -> int | None:
    """The organization's current permission version, or None if never set.

    None means "no grant has changed since this Redis was last emptied", which is not the
    same as zero — a rebuilt cache must not invalidate every token in circulation.
    """
    raw = await cache_get(permission_version_key(organization_id))
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:  # pragma: no cover - defensive
        return None


async def bump_permission_version(organization_id: str) -> int:
    """Called whenever a grant changes. Every token minted before this is now stale."""
    try:
        return int(await get_redis().incr(permission_version_key(organization_id)))
    except Exception as exc:
        # Losing a bump means a revoked grant keeps working until the token expires,
        # which is exactly the window `pv` exists to close. Loud, not silent.
        log.error("permission_version.bump_failed", org=organization_id, error=str(exc)[:120])
        raise


async def resolve_cached(key: str, loader: Any, *, ttl: int = 60) -> Any:
    """Read-through cache for values that are expensive and safe to be briefly stale.

    Used by the gateway for permission resolution. The TTL is short on purpose and the
    key carries the permission version, so a grant change invalidates by construction
    rather than by waiting.
    """
    cached = await cache_get(key)
    if cached is not None:
        import json

        return json.loads(cached)

    value = await loader()
    import json

    await cache_set(key, json.dumps(value), ttl=ttl)
    return value
