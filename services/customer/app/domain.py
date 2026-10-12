"""Pure functions for the customer service. No I/O, no session."""

from __future__ import annotations

import uuid

# ------------------------------------------------------------------ conversion ids (copy of lead's)

#: lead's `_CONVERSION_NAMESPACE`, frozen: changing it would re-derive every id ever handed out.
CONVERSION_NAMESPACE = uuid.UUID("6f2e6f3a-6d1e-4b6a-9c8e-2f7a5b1d4c90")


def conversion_ids(
    lead_id: uuid.UUID, generation: int = 0
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """The `(company_id, contact_id, deal_id)` a lead converts into at `generation`.

    A verbatim copy of lead's `app.domain.conversion_ids` (`import app` is ambiguous across
    services, and platform-core is not the home of a CRM algorithm). The three copies are held
    equal by `libs/contracts/vectors/conversion_ids.v1.json`, which each service's tests assert.
    The aborted-conversion handler re-derives an attempt's ids with this and never acts on the
    ids an event names. Generation `0` has no suffix; each later generation appends `:{generation}`.
    """
    suffix = "" if generation == 0 else f":{generation}"
    return (
        uuid.uuid5(CONVERSION_NAMESPACE, f"{lead_id}:company{suffix}"),
        uuid.uuid5(CONVERSION_NAMESPACE, f"{lead_id}:contact{suffix}"),
        uuid.uuid5(CONVERSION_NAMESPACE, f"{lead_id}:deal{suffix}"),
    )
