"""Request and response contracts for storefront.

This module is the OpenAPI surface: a change here is a change to the published contract, and
the frontend generates its client from it.

Two rules hold throughout, and both have a reason beyond tidiness.

**Every request model sets `extra="forbid"`.** A field a merchant sends and this service
ignores is a setting they believe they changed. On the *public* model it matters more than
that: the anonymous body is the one input on this service that arrives from a stranger, and
silently dropping what it does not recognise is how an undocumented field becomes a contract.

**The answer carries data, never prose.** A minute count or a resolved ISO date, computed from
the store's own timezone — the client owns the wording, and a buyer in Singapore looking at a
Mumbai store gets the Mumbai answer (ADR-0000 "Also settled").
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from app import domain

# The lengths are the column's, imported rather than repeated: a limit that disagrees with the
# column is a 500 waiting for the first merchant who reaches it.
from app.models import (
    CUSTOM_DOMAIN_LENGTH,
    LOGO_TEXT_LENGTH,
    STORE_NAME_LENGTH,
    TAGLINE_LENGTH,
)


def _collapsed(value: Any) -> Any:
    """Trim and collapse a brand string **before** its length is checked.

    Otherwise a name of exactly the limit, pasted with a trailing space, is refused with "at
    most 80 characters" while the merchant is looking at 78 letters. `domain.clean_brand_text`
    applies the same rule again on the way to the database — this one exists so the *published
    limit* and the *stored limit* are the same number rather than two that differ by whatever
    whitespace was in the paste buffer.
    """
    return domain.collapse_whitespace(value) if isinstance(value, str) else value


#: Brand text as it arrives: trimmed, collapsed, then bounded by the column's own limit. The
#: bound is in the OpenAPI document (`maxLength`) so a client can enforce it in the form rather
#: than discovering it on submit.
BrandText = Annotated[str, BeforeValidator(_collapsed)]

#: Every mode the column's check constraint allows — including `SLOTTED`, which slice 1 will not
#: store. **The type is deliberately permissive and `domain.assert_mode_is_available` is what
#: refuses**, because a merchant who picks the mode the UI shows them deserves "slot-based
#: delivery is not available yet, and here is what arrives with it" rather than pydantic's
#: "Input should be 'PROMISED' or 'UNSERVED'". Slice 2 unlocks it by deleting a validation rule;
#: nothing here and nothing in the schema changes.
AnswerMode = Literal["SLOTTED", "PROMISED", "UNSERVED"]
PromiseKind = Literal["DURATION", "DATE_OFFSET"]
#: Why the answer is what it is. `no_mapping` is **not** the same fact as `excluded`: one says
#: "we have not got round to it", the other says "we decided not to", and the demand report is
#: worth far more when a merchant can tell them apart (ADR-0010 §3).
UnservedReason = Literal["no_mapping", "excluded", "zone_inactive"]

Slug = Annotated[str, Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
PostalCodeIn = Annotated[str, Field(min_length=1, max_length=24)]
ZoneCode = Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9._-]+$")]


class ServiceInfo(BaseModel):
    service: str
    schema_name: str


# --------------------------------------------------------------------- the answer


class PromiseOut(BaseModel):
    """When it arrives. Exactly one of `minutes` / `date` is populated.

    `date` is already resolved **on the server, in the store's timezone**, cutoff applied. The
    client renders it; it never computes it, and it never sends its own clock.
    """

    kind: PromiseKind
    #: DURATION: "in about 30 minutes".
    minutes: int | None = None
    #: DATE_OFFSET: "by Thursday 25 September".
    date: dt.date | None = None


class FeesOut(BaseModel):
    """**Display values. These are never the numbers charged.**

    Integer minor units of `currency` (ADR-0000 §8). Slice 1 has no cart, no pricing engine and
    nothing that enforces them; when the cart lands, the pricing engine recomputes the total
    from its own state and one of the two sources of truth stops being authoritative — and it is
    this one (ADR-0010 §8). Show them on the card; never total them into anything.
    """

    currency: str
    delivery_fee_minor: int
    free_delivery_above_minor: int | None = None
    min_order_minor: int = 0
    #: Always `true` in slice 1. Present so the front end can render the caveat from the
    #: payload rather than from a constant it would have to remember to remove.
    display_only: bool = True


class ServiceabilityOut(BaseModel):
    """The answer to "do you deliver to me?".

    **There is no `UNKNOWN` here, and that is the contract.** The server never returns an
    outcome it did not compute: a check that fails is a non-2xx problem+json, and "couldn't
    check" is the client's rendering of that. Telling a buyer in a served area that the shop
    does not reach them is a lost order and a support ticket, and it is exactly what a naive
    `catch → not serviceable` produces (ADR-0010 §13).
    """

    #: Echoed back normalised, so the client can key its `localStorage` on what the server
    #: actually matched rather than on what the buyer typed.
    postal_code: str
    serviceable: bool
    mode: AnswerMode
    #: Null when `serviceable`. One of `no_mapping` | `excluded` | `zone_inactive` otherwise.
    reason: UnservedReason | None = None
    promise: PromiseOut | None = None
    fees: FeesOut | None = None
    #: The merchant's own note for this zone, e.g. "Orders after 6pm ship next day".
    buyer_note: str | None = None
    #: When the server answered. The client re-fetches on every page load regardless — the
    #: buyer's postal code is stored in the browser, the **answer is not** (ADR-0010 §5).
    checked_at: dt.datetime


class MerchantServiceabilityOut(ServiceabilityOut):
    """The same answer, from the same selector, plus which zone matched.

    The tester is the only way a merchant verifies that a 19,000-row import did what they meant,
    and `zone_code` is the whole reason it is not just the public endpoint with a token: a
    confident wrong answer from a mis-mapped postal code is invisible without it (ADR-0010 §13).
    """

    zone_id: uuid.UUID | None = None
    zone_code: str | None = None
    zone_name: str | None = None


class PublicServiceabilityIn(BaseModel):
    """The anonymous request body.

    **The store slug travels in the body, not in the path, and that is a standing constraint on
    the whole future buyer API** — not a quirk of this route. `GatewayPolicy.public_paths` is an
    exact-path frozenset (`full_path in policy.public_paths`), and `full_path` is built from the
    catch-all `{path:path}` and excludes the query string. So a public path with a variable in
    it — `/public/store/{slug}` — is not expressible at all, and every anonymous storefront
    endpoint must carry its variables in the query string or the body (ADR-0010 §4).
    """

    model_config = ConfigDict(extra="forbid")

    store: Slug
    postal_code: PostalCodeIn


class DeliveryCheckIn(BaseModel):
    """The merchant's tester. No `store` — the tenant comes from the token, not from a slug."""

    model_config = ConfigDict(extra="forbid")

    postal_code: PostalCodeIn


# --------------------------------------------------------------------- the store


class StoreOut(BaseModel):
    """The store's identity — **who the store is, not how it looks**.

    The brand (`name`, `tagline`, `logo_text`) is here rather than in the theme document,
    because a store's name is its identity and not a property of the theme somebody picked
    (ADR-0011 §2). The theme document — tokens, pages, sections — is a separate resource at
    `/api/v1/storefront/theme`, reserved by ADR-0011 §3 and **not served by this service yet**.

    Every string here is **merchant-authored text that will be rendered into a public page**.
    It is stored trimmed, bounded and free of control characters; it is **not** HTML-escaped,
    and escaping it here would be wrong. Output encoding belongs to whatever renders the page.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    #: Immutable once set (ADR-0011 §7). Changing it on a PUT is a 409 `slug_immutable`.
    slug: str
    #: The store's name. Always present and never blank.
    name: str
    tagline: str | None
    #: The header wordmark. Null means the renderer falls back to `name` — this service does not
    #: copy one into the other, so a later name change cannot leave a stale wordmark behind.
    logo_text: str | None
    status: Annotated[
        Literal["draft", "published", "suspended"],
        Field(
            description=(
                "`draft` is invisible to buyers; `published` makes the anonymous serviceability "
                "check answer for this store. Both are reached only through "
                "`POST /storefront/store/publish` and `.../unpublish`. `suspended` is set by the "
                "platform and no merchant route can set or leave it."
            )
        ),
    ]
    currency: str
    timezone: str
    custom_domain: Annotated[
        str | None,
        Field(
            description=(
                "The domain the merchant intends to use. **It is not an address the store is "
                "reachable at.** Nothing in this platform serves a custom domain — there is no "
                "ingress, no TLS termination and no host-based routing — so this records an "
                "intention and changes nothing about reachability."
            )
        ),
    ]
    custom_domain_status: Annotated[
        Literal["unverified", "verified"] | None,
        Field(
            description=(
                "`unverified` whenever `custom_domain` is set, `null` when it is not. **Nothing "
                "can move it off `unverified` today**: verifying that a merchant owns a domain "
                "is DNS work nobody has done, and it is a precondition of ever serving one — "
                "serving a domain on the strength of a form submission is how one merchant "
                "takes another's traffic."
            )
        ),
    ]
    created_at: dt.datetime
    updated_at: dt.datetime


class StorePut(BaseModel):
    """Create or update the organization's single storefront.

    A PUT rather than a PATCH because a merchant editing a store's identity is editing one form,
    all of it at once. **`status` is deliberately not on this payload** (ADR-0011 §4): a settings
    save must not be able to put a store in front of the internet, so publishing is its own named
    transition. A store is created `draft`, always.

    `slug` is required on create and must **match the stored value** on update — a rename is a
    409 `slug_immutable` rather than a field this payload silently ignores (ADR-0011 §7).
    """

    model_config = ConfigDict(extra="forbid")

    slug: Slug
    #: Trimmed, whitespace-collapsed, and required. Blank-after-trimming is a 422
    #: `name_required`, not an empty store name.
    #: No `min_length`: a blank name is refused by `domain.clean_brand_text` with
    #: `name_required` and a sentence, rather than by pydantic with "String should have at least
    #: 1 character" — the same reason `SLOTTED` is refused by the writer and not by the type.
    name: Annotated[
        BrandText,
        Field(
            max_length=STORE_NAME_LENGTH,
            description=(
                "The store's name, as a buyer sees it. Trimmed and whitespace-collapsed before "
                "it is stored; blank after trimming is a 422 `name_required`. Stored verbatim "
                "otherwise — it is **not** HTML-escaped, and encoding it for output is the "
                "renderer's job."
            ),
        ),
    ]
    tagline: (
        Annotated[
            BrandText,
            Field(
                max_length=TAGLINE_LENGTH,
                description="One line under the name. `null` or an empty string clears it.",
            ),
        ]
        | None
    ) = None
    logo_text: (
        Annotated[
            BrandText,
            Field(
                max_length=LOGO_TEXT_LENGTH,
                description=(
                    "The header wordmark. `null` or an empty string clears it, and the renderer "
                    "falls back to `name`."
                ),
            ),
        ]
        | None
    ) = None
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")] = "INR"
    #: IANA. Validated against the system's timezone database, because a bad one would produce a
    #: promised date that is wrong by hours and looks perfectly well-formed.
    timezone: Annotated[str, Field(min_length=1, max_length=64)] = "Asia/Kolkata"
    custom_domain: (
        Annotated[
            str,
            Field(
                max_length=CUSTOM_DOMAIN_LENGTH,
                description=(
                    "The domain the merchant **intends** to use, stored lower-cased with no "
                    "scheme, path or port. **Setting it records an intention and changes "
                    "nothing about reachability**: no request is served on it, nothing here "
                    "checks that the merchant owns it, and the stored status is `unverified` "
                    "with no path off it. `null` or an empty string releases the claim. A "
                    "domain another organization has already claimed is a 409 "
                    "`custom_domain_taken`."
                ),
                examples=["shop.example.com"],
            ),
        ]
        | None
    ) = None


# --------------------------------------------------------------------- zones


class ZoneOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    mode: AnswerMode
    promise_kind: PromiseKind | None
    promise_minutes: int | None
    promise_offset_days: int | None
    promise_cutoff_time: dt.time | None
    delivery_fee_minor: int
    free_delivery_above_minor: int | None
    min_order_minor: int
    is_active: bool
    buyer_note: str | None
    #: How many postal codes are mapped to this zone. Loaded for the whole page in one grouped
    #: query — a per-zone count in the loop would be the N+1 this list is most likely to grow.
    postal_code_count: int = 0
    created_at: dt.datetime
    updated_at: dt.datetime


class ZoneCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: ZoneCode
    name: Annotated[str, Field(min_length=1, max_length=120)]
    #: `SLOTTED` is accepted by the type and refused by the writer — see `AnswerMode`.
    mode: AnswerMode
    promise_kind: PromiseKind | None = None
    promise_minutes: Annotated[int, Field(gt=0, le=60 * 24 * 14)] | None = None
    promise_offset_days: Annotated[int, Field(ge=0, le=90)] | None = None
    promise_cutoff_time: dt.time | None = None
    delivery_fee_minor: Annotated[int, Field(ge=0)] = 0
    free_delivery_above_minor: Annotated[int, Field(ge=0)] | None = None
    min_order_minor: Annotated[int, Field(ge=0)] = 0
    is_active: bool = True
    buyer_note: Annotated[str, Field(max_length=200)] | None = None


class ZonePatch(BaseModel):
    """A partial edit. **Absent leaves a field alone; an explicit `null` clears it.**

    Carried by `model_dump(exclude_unset=True)`, the same mechanism `account.MemberPatch` uses
    for the employee link. It matters here for the promise fields: clearing a promise and never
    mentioning it are different intentions, and a zone whose mode changed to `UNSERVED` has to
    be able to say the first.
    """

    model_config = ConfigDict(extra="forbid")

    code: ZoneCode | None = None
    name: Annotated[str, Field(min_length=1, max_length=120)] | None = None
    mode: AnswerMode | None = None
    promise_kind: PromiseKind | None = None
    promise_minutes: Annotated[int, Field(gt=0, le=60 * 24 * 14)] | None = None
    promise_offset_days: Annotated[int, Field(ge=0, le=90)] | None = None
    promise_cutoff_time: dt.time | None = None
    delivery_fee_minor: Annotated[int, Field(ge=0)] | None = None
    free_delivery_above_minor: Annotated[int, Field(ge=0)] | None = None
    min_order_minor: Annotated[int, Field(ge=0)] | None = None
    is_active: bool | None = None
    buyer_note: Annotated[str, Field(max_length=200)] | None = None


class ZonePostalCodesOut(BaseModel):
    """Every postal code in one zone. Bounded by the zone, not by a page.

    The zone editor's textarea is one control holding one list, so paging it would mean a
    merchant editing a fragment of their own zone and saving the fragment. The bound is
    `MAX_POSTAL_CODES_PER_ZONE` on the write, which is what keeps this read finite.
    """

    zone_id: uuid.UUID
    zone_code: str
    postal_codes: list[str]
    total: int


class ZonePostalCodesPut(BaseModel):
    """The textarea path: the complete list for this zone, replacing whatever was there.

    Replace rather than append, because without it a merchant who **shrinks** a zone watches
    their removal do nothing and the old area keep delivering.
    """

    model_config = ConfigDict(extra="forbid")

    #: One per line or comma-separated, pasted from a spreadsheet. De-duplicated on save.
    postal_codes: str = Field(
        examples=["400050\n400051, 400052"],
        description="Postal codes, one per line or comma-separated.",
    )


class ImportResultOut(BaseModel):
    """What a bulk import did. Counters, and the offending line numbers if it refused.

    The audit row carries the same five counters and the mode — **one row per import**, not
    19,000. The mode is on the row because a `replace` is destructive and a `merge` is not, and
    the trail has to distinguish them (ADR-0010 §14).
    """

    mode: Literal["merge", "replace"]
    rows_read: int
    inserted: int
    updated: int
    deleted: int
    rejected: int = 0


class DemandRowOut(BaseModel):
    """One postal code's demand on one day."""

    check_date: dt.date
    postal_code: str
    outcome: Literal["SERVED", "NO_MAPPING", "EXCLUDED", "ZONE_INACTIVE"]
    checks: int
