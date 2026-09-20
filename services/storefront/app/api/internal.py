"""The one gate on certificate issuance. **Reached by Caddy on the compose network, never by a
browser, and it answers a yes/no that costs a certificate authority's rate limit to get wrong.**

ADR-0012 §5: the edge issues certificates on demand for any hostname a buyer types, and the
only control on that is this route. Caddy asks `GET …/tls-authorize?domain=<name>` before
obtaining a certificate for a name it has never seen; a 2xx means "obtain one", anything else
— a 4xx, a 5xx, a timeout — means "do not". So:

* **Refusing is the safe direction.** A refused name gets no certificate and a TLS error, which
  a merchant sees on their domain screen and fixes. An allowed name that should not have been
  costs a Let's Encrypt validation against our account's limits, for a host somebody else
  pointed at our address — and ten thousand of those is how an attacker stops every real
  merchant from getting a certificate. Nothing here allows a name it did not resolve.
* **Unknown is no, here — the one place that inverts ADR-0010 §13.** For a buyer's check an
  outage must render as "couldn't check", because "no" costs a customer. For issuance an
  outage must render as "no", because a certificate already in storage keeps serving and a
  refused *new* name merely waits. There is no `try`/`except` in this module either, for the
  opposite reason to `public.py`'s: an exception becomes a 500, and a 500 is a no.
* **Three kinds of name can be allowed, and the list is closed.** The platform's own hostnames
  (`tls_platform_hosts`, exact); a published store's `<slug>.stores.opsgrids.com`; and a
  published store's custom domain at a status that means the claim was proved — through the
  same bypass reader, the same Redis cache and the same published-store test the buyer's
  bootstrap uses, so a name the origin would refuse to serve is a name that gets no certificate.

The path is under `/internal/`, which the Caddyfile answers 404 for from the internet and which
the gateway's route table would demand a bearer token for anyway. This module still assumes
nothing about who is calling: it reads one identity row and returns no field from it.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app import domain, selectors
from app.config import settings
from app.schemas import TlsAuthorizeOut
from platform_core.db import get_directory_session
from platform_core.errors import ForbiddenProblem
from platform_core.logging import get_logger

log = get_logger("storefront.tls")

router = APIRouter(tags=["storefront (internal)"])

DirectorySession = Annotated[AsyncSession, Depends(get_directory_session)]


@router.get(
    "/storefront/internal/tls-authorize",
    response_model=TlsAuthorizeOut,
    summary="May the edge obtain a certificate for this hostname?",
    responses={
        403: {"description": "No — not a platform host and no published store answers there"},
        422: {"description": "That is not a hostname; the edge treats this as no"},
    },
)
async def tls_authorize(
    name: Annotated[str, Query(alias="domain", max_length=domain.MAX_HOST_LENGTH)],
    directory: DirectorySession,
) -> TlsAuthorizeOut:
    """Caddy's `ask` (ADR-0012 §5). 200 allows issuance for exactly this name; 403 refuses.

    The order is the cheap answer first: a platform host needs no lookup at all. Everything
    else is one read of `storefront.storefronts` on the SELECT-only bypass reader, which is the
    same read the buyer's bootstrap makes — so "would we serve this host" and "may this host
    have a certificate" are one decision made in one place.
    """
    cfg = settings()
    host = domain.normalise_request_host(name)

    if host in cfg.tls_platform_host_set:
        return TlsAuthorizeOut(host=host, reason="platform")

    store = await selectors.resolve_public_store_by_host(
        directory, host, platform_suffix=cfg.platform_host_suffix
    )
    if store is None or not store.is_published:
        # Loud in the log with the status, silent on the wire: the same discipline as the
        # buyer's bootstrap, because the caller here is Caddy and the *name* is chosen by
        # whoever pointed DNS at us. This line is where a takeover attempt, a merchant's typo
        # and an unpublished store are told apart, and it is the only place they are.
        log.warning(
            "storefront.tls.refused",
            host=host,
            status=store.status if store is not None else None,
        )
        raise ForbiddenProblem("No published store answers at this name.")

    return TlsAuthorizeOut(host=host, reason="store")
