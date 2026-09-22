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

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field

from app import domain

# The lengths are the column's, imported rather than repeated: a limit that disagrees with the
# column is a 500 waiting for the first merchant who reaches it.
from app.models import (
    CATEGORY_BLURB_LENGTH,
    CATEGORY_KEY_LENGTH,
    CATEGORY_LABEL_LENGTH,
    CUSTOM_DOMAIN_LENGTH,
    ENQUIRY_COMPANY_LENGTH,
    ENQUIRY_GSTIN_LENGTH,
    ENQUIRY_NAME_LENGTH,
    ENQUIRY_QUANTITY_LENGTH,
    IMAGE_PATH_LENGTH,
    LOGO_TEXT_LENGTH,
    PRODUCT_BRAND_LENGTH,
    PRODUCT_CODE_LENGTH,
    PRODUCT_MOQ_UNIT_LENGTH,
    PRODUCT_NAME_LENGTH,
    PRODUCT_SPEC_LENGTH,
    PRODUCT_UNIT_LENGTH,
    STORE_NAME_LENGTH,
    TAGLINE_LENGTH,
    THEME_KEY_LENGTH,
    VARIANT_LABEL_LENGTH,
)
from platform_core.pagination import Page


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
ThemeMode = Literal["trade", "retail"]
#: Why the answer is what it is. `no_mapping` is **not** the same fact as `excluded`: one says
#: "we have not got round to it", the other says "we decided not to", and the demand report is
#: worth far more when a merchant can tell them apart (ADR-0010 §3).
UnservedReason = Literal["no_mapping", "excluded", "zone_inactive"]

Slug = Annotated[str, Field(min_length=2, max_length=63, pattern=r"^[a-z0-9][a-z0-9-]{1,62}$")]
#: A host as a browser reports it — **bounded, not validated, here**. Deliberately no pattern:
#: the value may arrive with a port, a trailing dot or a U-label, all of which
#: `domain.normalise_request_host` strips or converts, and all of which a regex would refuse
#: with pydantic's wording instead of a sentence. The bound is RFC 1035's, so an oversized body
#: is refused before any of that runs.
Hostname = Annotated[str, Field(min_length=1, max_length=domain.MAX_HOST_LENGTH)]
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


# --------------------------------------------------------------------- the buyer's bootstrap


class PublicStoreContextIn(BaseModel):
    """The first call a buyer's browser makes, before anything is rendered (ADR-0012 §2).

    `host` is `window.location.hostname` — nothing else, and in particular not a value the
    bundle was built with. **The bundle is identical for every merchant**, which is what lets
    one build serve `greenleaf.stores.opsgrids.com` and `merchant.com` without a config file
    per store.

    It is bounded here at a hostname's own ceiling so an oversized body is refused by the schema
    before any code touches it; `domain.normalise_request_host` is what decides whether what is
    inside that bound is a hostname at all.
    """

    model_config = ConfigDict(extra="forbid")

    host: Hostname


class TlsAuthorizeOut(BaseModel):
    """The edge may obtain a certificate for `host` (ADR-0012 §5). Only ever a 200 body.

    `host` is the normalised name the decision was made for, so a human with curl can see what
    Caddy's SNI was reduced to. `reason` says which of the two allowed kinds it was — a listed
    platform host or a published store's name — and nothing about the store: this is answered
    to Caddy, and the name was chosen by whoever pointed DNS at us.
    """

    host: str
    reason: Literal["platform", "store"]


class PublicThemeOut(BaseModel):
    """The theme document, minus `version` and the timestamps — a buyer has no concurrent
    write to race and no reason to see when a merchant last saved."""

    theme_key: str
    mode: ThemeMode
    tokens: dict[str, Any]
    pages: list[dict[str, Any]]


class StoreContextOut(BaseModel):
    """Which store this address is, and what it is called.

    ────────────────────────────────────────────────────────────────────────────────────────
    **This is the only public representation of ADR-0011's brand columns**, and the
    storefront-document ADR extends *this* payload rather than adding a second one (ADR-0012
    §11). A second bootstrap would mean two calls on the first-paint path and two shapes that
    drift.
    ────────────────────────────────────────────────────────────────────────────────────────

    **`name`, `tagline` and `logo_text` are merchant-authored text, returned verbatim and
    deliberately not escaped.** They are stored trimmed, bounded, single-line and free of
    control characters and bidi overrides, and that is the whole of the sanitising this service
    does. Output encoding belongs to whatever renders the page — escaping here would
    double-escape the moment anything else read the column, and would still not protect a client
    that injected the value as HTML (ADR-0011 §6). **The buyer bundle owns escaping these three
    fields.**

    Every field is always present. `tagline` and `logo_text` are nullable rather than absent, so
    "the merchant has not written one" is one state on the wire rather than two that compare
    differently. The renderer decides the fallback — `logo_text` falling back to `name` is its
    decision and not a value this service copies.
    """

    #: The slug, and **what every subsequent call names the store by** — including ADR-0010's
    #: `POST /public/serviceability`, whose body is unchanged by this ADR. Resolving the host
    #: once in a bootstrap call is what keeps a `host` field off every other public endpoint.
    store: str
    name: str
    tagline: str | None
    logo_text: str | None
    #: ISO-4217, for formatting prices the buyer is shown.
    currency: str
    #: IANA. The server computes dates in it; the browser's own zone is never an input.
    timezone: str
    canonical_host: Annotated[
        str,
        Field(
            description=(
                "The address this store should be linked to. The merchant's own domain once it "
                "is live, and the platform host otherwise — **including when a live domain "
                "starts failing**, so the canonical pointer never aims at a name that does not "
                "answer. Slice 1 does not redirect: this is what the bundle puts in "
                '`<link rel="canonical">`.'
            )
        ),
    ]
    #: Whether the host that was asked about *is* the canonical one. `false` is not an error and
    #: not a redirect — both addresses serve, permanently — it is what lets the page render a
    #: one-line banner and a correct canonical link.
    is_canonical: bool
    #: The adopted theme's `mode`/`tokens`/`pages` — **the only public representation of the
    #: theme document** (ADR-0012 §11's coordination note: extend this payload, not add a
    #: second one). `null` when the store has published with no theme adopted, which
    #: `ShopFront.tsx` renders as its own empty state rather than a 404 — a store name and
    #: nothing else is still a valid published store.
    theme: PublicThemeOut | None = None
    #: When the server answered. The bundle caches this in `sessionStorage` for the tab, so it is
    #: one call per session rather than one per page view.
    checked_at: dt.datetime


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
        Literal["unverified", "verified", "live", "failing", "suspended"] | None,
        Field(
            description=(
                "`unverified` whenever `custom_domain` is set and never yet proved; `null` when "
                "there is no claim. `verified`, `live`, `failing` and `suspended` are the "
                "domain-hosting state machine's (ADR-0012 §6) — this is the same column "
                "`GET /storefront/store/domain`'s `status` reads, so the two screens cannot "
                "disagree. See that route for the verification token, the DNS records to "
                "publish, and each probe's own state."
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
                    "The domain the merchant intends to use, stored lower-cased with no "
                    "scheme, path or port. Setting it here always lands the store on "
                    "`unverified` — the same value a fresh `PUT /storefront/store/domain` "
                    "leaves it at. **That dedicated route is what mints a verification token "
                    "and runs the checks**; a domain set through this identity form has no "
                    "token until it is claimed there too. `null` or an empty string releases "
                    "the claim (and any verification progress with it). A domain another "
                    "organization has already claimed is a 409 `custom_domain_taken`."
                ),
                examples=["shop.example.com"],
            ),
        ]
        | None
    ) = None


# --------------------------------------------------------------------- the custom domain (ADR-0012)


class DomainClaimIn(BaseModel):
    """`PUT /storefront/store/domain` — the merchant's request to put the store on a domain
    they own. A separate resource from `StorePut`, deliberately: this route mints a
    verification token and creates the evidence row `GET` reads back; `StorePut.custom_domain`
    is the older, evidence-free intention field ADR-0011 shipped first."""

    model_config = ConfigDict(extra="forbid")

    custom_domain: Annotated[
        str,
        Field(
            min_length=1,
            max_length=CUSTOM_DOMAIN_LENGTH,
            description=(
                "The domain to claim, e.g. `shop.example.com`. Replacing an existing claim "
                "releases the old one and mints a new token — a verification proves control of "
                "the value claimed, and must not survive that value changing."
            ),
            examples=["shop.example.com"],
        ),
    ]


class DnsRecordOut(BaseModel):
    """One DNS record row the merchant is told to publish, exactly as ADR-0012 §11 prints it."""

    record_type: str
    name: str
    value: str
    note: str | None = None


class DomainVerificationOut(BaseModel):
    """The TXT record that proves ownership (ADR-0012 §3). `value` carries the token in the
    clear — it is returned only over this tenant-bound, permission-gated route, and it is never
    logged."""

    record_type: Literal["TXT"] = "TXT"
    name: str
    value: str
    issued_at: dt.datetime
    claim_expires_at: dt.datetime


class DomainDnsOut(BaseModel):
    """What the merchant is told to publish, and how (ADR-0012 §4). `apex` decides which of
    `preferred` / `fallback` a UI leads with — an ALIAS is offered first everywhere it works,
    the literal A records are the "works everywhere" fallback."""

    apex: bool
    preferred: DnsRecordOut
    fallback: list[DnsRecordOut]
    proxy_warning: str
    also_served: list[str]


class DomainCheckStateOut(BaseModel):
    """One probe's last result (ADR-0012 §6). `state` is `pending` until the probe has ever
    run — routing and certificate stay `pending` for the whole time a claim is `unverified`,
    because neither is checked before ownership is proved. `not_after` is only ever set by the
    certificate probe; `null` on the other two."""

    state: Literal["pending", "ok", "failing"]
    last_checked_at: dt.datetime | None
    next_check_at: dt.datetime | None
    code: str | None
    detail: str | None
    not_after: dt.datetime | None = None


class DomainChecksOut(BaseModel):
    ownership: DomainCheckStateOut
    routing: DomainCheckStateOut
    certificate: DomainCheckStateOut


class DomainOut(BaseModel):
    """`GET /storefront/store/domain` — the whole of what a merchant sees about their custom
    domain, per ADR-0012 §11's printed wire format.

    Every field is present on every response; `verification`, `dns` and `checks` are `null`
    together, exactly once — when there is no claim at all. **`status` reads the same column
    `StoreOut.custom_domain_status` does**, so the settings screen and this one cannot disagree.
    """

    custom_domain: str | None
    status: Literal["unverified", "verified", "live", "failing", "suspended"] | None
    #: `<slug>.stores.opsgrids.com` — never retires, and always present, claim or no claim
    #: (ADR-0012 §8).
    platform_host: str
    #: The custom domain once it is `live`; the platform host otherwise, including while a
    #: previously-live domain is `failing` (ADR-0012 §8) — the same value
    #: `StoreContextOut.canonical_host` computes for a buyer.
    canonical_host: str
    verification: DomainVerificationOut | None
    dns: DomainDnsOut | None
    checks: DomainChecksOut | None


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


# --------------------------------------------------------------------- the theme document

ThemeKey = Annotated[str, Field(min_length=1, max_length=THEME_KEY_LENGTH)]


class ThemeDocOut(BaseModel):
    """The document the editor loads and saves. No `brand` — that left for `StoreOut` in
    ADR-0011 §2; the renderer reads name/tagline/logo_text off the store, not off this."""

    model_config = ConfigDict(from_attributes=True)

    theme_key: str
    mode: ThemeMode
    tokens: dict[str, Any]
    pages: list[dict[str, Any]]
    #: Optimistic concurrency. Echo this back unchanged on `PUT` to win the race; a stale value
    #: is a 409 `theme_version_conflict`, never a silent overwrite.
    version: int
    created_at: dt.datetime
    updated_at: dt.datetime


class ThemeDocPut(BaseModel):
    """Upsert — **this is adopt**, per ADR-0011 §3: the front end forks a theme client-side and
    PUTs the result, so no separate `/adopt` route exists.

    `version` is required on an update and ignored on create (there is nothing to conflict
    with yet); a caller creating for the first time sends `1`.
    """

    model_config = ConfigDict(extra="forbid")

    theme_key: ThemeKey
    mode: ThemeMode
    tokens: dict[str, Any]
    pages: list[dict[str, Any]]
    version: Annotated[int, Field(ge=1)]


# --------------------------------------------------------------------- the catalogue

ProductStatus = Literal["draft", "active", "archived"]
CategoryKeyField = Annotated[
    str, Field(min_length=1, max_length=CATEGORY_KEY_LENGTH, pattern=r"^[a-z0-9][a-z0-9-]*$")
]
ImagePath = Annotated[str, Field(max_length=IMAGE_PATH_LENGTH)]


class CategoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    key: str
    label: str
    blurb: str | None
    image_url: str | None
    sort_order: int
    is_active: bool
    #: Live products carrying this category — one grouped query for the whole page, the same
    #: shape as `ZoneOut.postal_code_count`, never a per-row count in a loop.
    line_count: int = 0
    created_at: dt.datetime
    updated_at: dt.datetime


class CategoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: CategoryKeyField
    label: Annotated[str, Field(min_length=1, max_length=CATEGORY_LABEL_LENGTH)]
    blurb: Annotated[str, Field(max_length=CATEGORY_BLURB_LENGTH)] | None = None
    image_url: ImagePath | None = None
    sort_order: int = 0
    is_active: bool = True


class CategoryPatch(BaseModel):
    """Absent leaves a field alone; explicit `null` clears the nullable ones. `key` is
    immutable once created — it is what a product's `category_id` resolves through, and a
    rename would silently repoint every product to a category a client thinks is different."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=CATEGORY_LABEL_LENGTH)] | None = None
    blurb: Annotated[str, Field(max_length=CATEGORY_BLURB_LENGTH)] | None = None
    image_url: ImagePath | None = None
    sort_order: int | None = None
    is_active: bool | None = None


class ProductVariantIn(BaseModel):
    """One pack size. Variants are replaced wholesale with the product — there is no
    independent variant id a client holds across two requests, so nested writes stay atomic
    with the price-sync invariant `services.py` enforces."""

    model_config = ConfigDict(extra="forbid")

    label: Annotated[str, Field(min_length=1, max_length=VARIANT_LABEL_LENGTH)]
    price_min_minor: Annotated[int, Field(ge=0)]
    price_max_minor: Annotated[int, Field(ge=0)]
    mrp_minor: Annotated[int, Field(ge=0)] | None = None
    in_stock: bool = True


class ProductVariantOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    label: str
    price_min_minor: int
    price_max_minor: int
    mrp_minor: int | None
    in_stock: bool


class ProductOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    name: str
    brand: str
    category_id: uuid.UUID
    #: The category's `key` — resolved once per page, batched, never a query per row
    #: (`selectors.list_products`). Kept alongside `category_id` so a write can target the
    #: relation and a read can render the label the client already expects (`types.ts`).
    category: str
    spec: str
    unit: str
    description: str | None
    price_min_minor: int
    price_max_minor: int
    mrp_minor: int | None
    #: `storefronts.currency` — one value per store, not repeated on every row by hand.
    currency: str
    gst_inclusive: bool
    moq: int
    moq_unit: str
    in_stock: bool
    lead_time_days: int
    delivery_eta_minutes: int | None
    featured: bool
    image_url: str | None
    status: ProductStatus
    is_active: bool
    variants: list[ProductVariantOut]
    created_at: dt.datetime
    updated_at: dt.datetime


class ProductCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: Annotated[str, Field(min_length=1, max_length=PRODUCT_CODE_LENGTH)]
    name: Annotated[str, Field(min_length=1, max_length=PRODUCT_NAME_LENGTH)]
    brand: Annotated[str, Field(max_length=PRODUCT_BRAND_LENGTH)] = ""
    category_id: uuid.UUID
    spec: Annotated[str, Field(max_length=PRODUCT_SPEC_LENGTH)] = ""
    unit: Annotated[str, Field(max_length=PRODUCT_UNIT_LENGTH)] = ""
    description: str | None = None
    price_min_minor: Annotated[int, Field(ge=0)]
    price_max_minor: Annotated[int, Field(ge=0)]
    mrp_minor: Annotated[int, Field(ge=0)] | None = None
    gst_inclusive: bool = True
    moq: Annotated[int, Field(ge=1)] = 1
    moq_unit: Annotated[str, Field(max_length=PRODUCT_MOQ_UNIT_LENGTH)] = ""
    in_stock: bool = True
    lead_time_days: Annotated[int, Field(ge=0)] = 0
    delivery_eta_minutes: Annotated[int, Field(ge=0)] | None = None
    featured: bool = False
    image_url: ImagePath | None = None
    status: ProductStatus = "draft"
    is_active: bool = True
    #: Empty is the ordinary case — a single-pack line (`types.ts`'s "required, sometimes
    #: empty"). When non-empty, `variants[0]`'s price band and MRP become this product's own.
    variants: list[ProductVariantIn] = Field(default_factory=list)


class ProductPatch(BaseModel):
    """Absent leaves a field alone. `code` is immutable — it is what a buyer quotes on a PO
    and what CSV-style re-imports would key on; send a new product instead of renaming one."""

    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=PRODUCT_NAME_LENGTH)] | None = None
    brand: Annotated[str, Field(max_length=PRODUCT_BRAND_LENGTH)] | None = None
    category_id: uuid.UUID | None = None
    spec: Annotated[str, Field(max_length=PRODUCT_SPEC_LENGTH)] | None = None
    unit: Annotated[str, Field(max_length=PRODUCT_UNIT_LENGTH)] | None = None
    description: str | None = None
    price_min_minor: Annotated[int, Field(ge=0)] | None = None
    price_max_minor: Annotated[int, Field(ge=0)] | None = None
    mrp_minor: Annotated[int, Field(ge=0)] | None = None
    gst_inclusive: bool | None = None
    moq: Annotated[int, Field(ge=1)] | None = None
    moq_unit: Annotated[str, Field(max_length=PRODUCT_MOQ_UNIT_LENGTH)] | None = None
    in_stock: bool | None = None
    lead_time_days: Annotated[int, Field(ge=0)] | None = None
    delivery_eta_minutes: Annotated[int, Field(ge=0)] | None = None
    featured: bool | None = None
    image_url: ImagePath | None = None
    status: ProductStatus | None = None
    is_active: bool | None = None
    variants: list[ProductVariantIn] | None = None


# --------------------------------------------------------------------- public catalogue


class PublicCatalogueIn(BaseModel):
    """The buyer bundle's one catalogue call — store in the body, same reason as every other
    public endpoint here (`GatewayPolicy.public_paths` is exact-match, ADR-0010 §4)."""

    model_config = ConfigDict(extra="forbid")

    store: Slug
    category: CategoryKeyField | None = None


class CategoryPublicOut(BaseModel):
    """`CategoryOut` minus what a stranger has no business seeing — `is_active` is implicit
    (every row here already is one) and `id`/`sort_order` stay merchant-only detail."""

    key: str
    label: str
    blurb: str | None
    image_url: str | None
    line_count: int = 0


class ProductPublicOut(BaseModel):
    """Field-for-field `types.ts` `Product`, built from the same row `ProductOut` is — this is
    the shape gap 3's merchant screen and gap 4's buyer screen share."""

    id: uuid.UUID
    code: str
    name: str
    brand: str
    category: str
    spec: str
    unit: str
    price_min_minor: int
    price_max_minor: int
    currency: str
    gst_inclusive: bool
    moq: int
    moq_unit: str
    in_stock: bool
    lead_time_days: int
    featured: bool
    image_url: str | None
    mrp_minor: int | None
    delivery_eta_minutes: int | None
    variants: list[ProductVariantOut]


class PublicCatalogueOut(BaseModel):
    categories: list[CategoryPublicOut]
    products: Page[ProductPublicOut]


# --------------------------------------------------------------------- enquiries

EnquiryStatus = Literal["new", "contacted", "won", "lost"]


#: A buyer's paste is trusted for shape, never for content: `quantity` is bounded the same
#: order of magnitude `Product.moq`/`ProductIn.moq` are, so a fabricated line cannot read back
#: as a plausible-looking bulk order.
MAX_ENQUIRY_LINE_QUANTITY = 1_000_000


class EnquiryLineIn(BaseModel):
    """One line of the buyer's list. `product_id` is validated against *this* published store's
    active catalogue and `name` is taken from the product there, never from this body — kept
    accepting `name` here only because the buyer bundle's form still sends it, and it is ignored
    server-side (`services.create_enquiry_for_store`). The enquiry is still a record of what was
    asked for at the time, not a live view: it stores the catalogue name as of the enquiry, so a
    later rename does not rewrite history."""

    model_config = ConfigDict(extra="forbid")

    product_id: uuid.UUID
    name: Annotated[str, Field(min_length=1, max_length=200)]
    quantity: Annotated[int, Field(ge=1, le=MAX_ENQUIRY_LINE_QUANTITY)]


#: Bounds a buyer's paste the same way `ZonePostalCodesPut` bounds a merchant's — a line list
#: this long is not a real enquiry.
MAX_ENQUIRY_LINES = 200


class EnquiryCreate(BaseModel):
    """The public body. `store` names the tenant the same way every other public endpoint's
    body does; `email` and `phone` are sealed rather than stored, `phone` optional because the
    front end's own form only requires an email (`EnquiryForm.tsx`)."""

    model_config = ConfigDict(extra="forbid")

    store: Slug
    name: Annotated[str, Field(min_length=1, max_length=ENQUIRY_NAME_LENGTH)]
    company: Annotated[str, Field(min_length=1, max_length=ENQUIRY_COMPANY_LENGTH)]
    email: Annotated[EmailStr, Field(max_length=254)]
    phone: Annotated[str, Field(max_length=40)] | None = None
    gstin: Annotated[str, Field(max_length=ENQUIRY_GSTIN_LENGTH)] | None = None
    requirement: Annotated[str, Field(min_length=1, max_length=4000)]
    quantity: Annotated[str, Field(max_length=ENQUIRY_QUANTITY_LENGTH)] = ""
    lines: Annotated[list[EnquiryLineIn], Field(max_length=MAX_ENQUIRY_LINES)] = Field(
        default_factory=list
    )


class EnquiryReceiptOut(BaseModel):
    id: uuid.UUID
    received_at: dt.datetime


class EnquiryOut(BaseModel):
    """The merchant's ordinary read. Contact fields are **masked by default** — `email_masked`
    and `phone_masked` are the same `•••`-bulleted hint `platform_core.pii.mask` produces for
    `services/customer`'s contacts. Unmasking is `POST /storefront/enquiries/{id}:reveal`."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    company: str
    email_masked: str
    phone_masked: str
    gstin: str | None
    requirement: str
    quantity: str
    lines: list[dict[str, Any]]
    status: EnquiryStatus
    created_at: dt.datetime
    updated_at: dt.datetime


class EnquiryPatch(BaseModel):
    """Only `status` ever changes — the enquiry's content is append-only. `domain.
    assert_enquiry_transition` is the state machine; an illegal move is a 409."""

    model_config = ConfigDict(extra="forbid")

    status: EnquiryStatus


class EnquiryRevealed(BaseModel):
    """The audited, stepped-up read. `None` for a field means nothing was ever sealed for it —
    `phone` is optional on create — not that the caller lacked permission to see it."""

    id: uuid.UUID
    email: str | None
    phone: str | None
