"""list prices of 2026-10-06: a monthly and a yearly version of every public flat plan

The owner's decision of 2026-10-06 (ADR-0035, `docs/adr/0035-list-prices-2026-10-06.md`): every
product together costs **INR 30,000 a month or INR 3,00,000 a year** — the yearly price
deliberately the cheaper one, ten months' price for twelve — split across the eleven products in
proportion to what each public flat plan cost until today (the placeholders `e1f2a3b4c5d6`,
`10e6b62d9ac7` and `53f1a7a8b99d` seeded). The split lives in `NEW_PRICES` below as integer paise
(ADR-0000 §8): the monthly amounts sum to exactly 3,000,000 paise and the yearly ones, each exactly
ten times its monthly amount, to exactly 30,000,000.

**Shape.** For each of the eleven public flat base plans — found by `(product_key, code)`, the
plan's own unique key — two new **published, public** versions are inserted: a **yearly** one at
the plan's highest `version_no` + 1, then a **monthly** one at + 2. Each carries one `flat` INR
price and clones its features (every key, the value exactly as stored) and its seat classes from
the plan's newest published public version. Nothing else changes: the per-seat Starter/Growth
plans, the suite bundles and every bespoke (`restricted_to_org_id`) version are not read as a
source and get no new version.

**Why the yearly version is numbered first.** Three reads take "the newest published public
version with a flat price" as a product's list price without looking at cadence —
`get_public_flat_plan` (the source a bespoke price clones), `all_public_flat_plans` (console's
pricing baseline and its product names) and `current_public_versions` (the Plans page's "sells
today"). Numbering the monthly version last keeps all three on the monthly price, which is what
they meant before a product had a yearly price. `offerable_versions` keys by cadence, so it offers
both either way, and supersedes the old monthly version with the new one.

**Immutability.** Inserts only; no published row is ever updated. Since `b7e4d2a9c613` nothing can
be inserted *under* a published version, so each version is inserted as a draft, its sheet
written, and then published — the same `draft -> published` step every operator publish takes.
The versions in force before today are left exactly as they are: still published, still the pin of
every existing subscription, superseded for anything new (ADR-0004 §2, ADR-0029 §8).

**Refused, not repriced.** A source whose sheet is not exactly one flat INR price, or that has a
paid seat class, stops the migration (it raises, and the whole transaction rolls back): re-pricing
it as one flat line would silently drop part of what it sells. **So does an open public draft** on
a plan it would re-price: the new versions would be numbered above the draft, and everything that
decides what sells takes the newest version per audience and cadence — so the draft, published
later, would be live on paper (its audit row and event say so) and never offered or shown as the
price. Every plan is checked before anything is inserted, and the error names every open draft;
delete each on the Plans page (or publish it first, knowing this release supersedes it at once)
and re-run. A bespoke draft is its own audience and is not in the way. **Skipped, with a
warning:** a plan that does not exist (a database whose definitional tables were cleared — the
test suite does this), a retired plan, and a plan with no published public version (withdrawn on
purpose; publishing here would put it back on sale). A source whose flat amount is not the price
the split was computed from (`SEEDED_MINOR`) is applied but warned about: the owner re-priced it
since, and the warning is the cue to check the table still holds.

**Idempotent.** Every id is `uuid5` of this migration's own namespace, the product key, the
cadence and the row kind; a version whose id already exists is left alone, children included.

**Downgrade.** Catalog cannot see which subscriptions pin these versions (a cross-schema read is
not allowed), and deleting a pinned version breaks the pin lookup — billing and every plan read —
for each subscriber on it. So the downgrade **refuses** whenever any of these versions exists,
unless `OPSGRIDS_ALLOW_LIST_PRICE_DOWNGRADE=1` is set (the pattern of `d7a3c9e5b184`); with it, it
deletes exactly the rows this migration inserted, with the frozen-row triggers disabled around the
delete as the seeds' downgrades do. Prefer rolling the code back: older catalog code reads these
rows fine. Its own variable, not the shared `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE`: this refusal
fires on every database the seeds ran on, fresh ones included, so `tools/roundtrip.sh` has to pass
the override for every service's `downgrade base` — and the shared one there would also switch off
operations' refusals to drop real lots, rolls and put-away captures.

No outbox event and no audit row, as with every seeded plan: this file and ADR-0035 are the record.

Revision ID: b88a833020dc
Revises: b7e4d2a9c613
Create Date: 2026-10-06 18:00:00.000000
"""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b88a833020dc"  # pragma: allowlist secret
down_revision: str | None = "b7e4d2a9c613"  # pragma: allowlist secret
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The root logger, deliberately. `migrations/env.py` runs `fileConfig(alembic.ini)`, which names
#: only root and disables every other logger that already exists — `alembic.runtime.migration`
#: always, and this module's own when it was imported before (a test, a second run in one
#: process) — so a warning sent anywhere else is dropped and the migrate output never shows it.
#: Root is configured there (WARNING, to stderr) and never disabled.
log = logging.getLogger()

# Fixed so a rerun of this file always names the same rows, in this database or any other. Its own
# namespace, as `53f1a7a8b99d` chose, so these ids cannot collide with a row another migration owns.
_NAMESPACE = uuid.UUID("744fb268-2411-4b28-80a9-d1550ca68414")

#: Set to `1` to let `downgrade()` delete versions a subscription may be pinned to. This
#: migration's own switch (see the module docstring), never the shared destructive-downgrade one.
ALLOW_DESTRUCTIVE_ENV = "OPSGRIDS_ALLOW_LIST_PRICE_DOWNGRADE"

CURRENCY = "INR"
MONTH = "month"
YEAR = "year"
#: Inserted in this order, so the monthly version is the newest (see the module docstring).
CADENCES: tuple[str, ...] = (YEAR, MONTH)

#: (product_key, code, monthly paise, yearly paise) — the owner's split of 2026-10-06. Integer minor
#: units only. The yearly amount is ten times the monthly one: a year is two months cheaper than
#: twelve monthly periods, which is the owner's choice, not a rounding.
NEW_PRICES: tuple[tuple[str, str, int, int], ...] = (
    ("crm", "revenue", 300000, 3000000),
    ("engage", "engage", 225000, 2250000),
    ("finance", "finance", 375000, 3750000),
    ("hrms", "people", 300000, 3000000),
    ("itsm", "it", 300000, 3000000),
    ("marketing", "marketing", 225000, 2250000),
    ("operations", "inventory", 375000, 3750000),
    ("projects", "projects", 225000, 2250000),
    ("pulseboard", "pulseboard", 225000, 2250000),
    ("storefront", "storefront", 225000, 2250000),
    ("support", "support", 225000, 2250000),
)

#: What all eleven together cost, the owner's two numbers: INR 30,000 a month, INR 3,00,000 a year.
MONTHLY_TOTAL_MINOR = 3000000
YEARLY_TOTAL_MINOR = 30000000

#: What each plan's public flat price was when the split was computed — the seeds' placeholders,
#: which the split is proportional to. A source at another amount is warned about, not refused.
SEEDED_MINOR: dict[str, int] = {
    "crm": 199900,
    "hrms": 199900,
    "itsm": 199900,
    "finance": 249900,
    "operations": 249900,
    "engage": 149900,
    "marketing": 149900,
    "projects": 149900,
    "pulseboard": 149900,
    "storefront": 149900,
    "support": 149900,
}


def amount_for(product_key: str, cadence: str) -> int:
    for key, _code, monthly, yearly in NEW_PRICES:
        if key == product_key:
            return monthly if cadence == MONTH else yearly
    raise KeyError(product_key)


def version_id(product_key: str, cadence: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{product_key}:{cadence}:version"))


def price_id(product_key: str, cadence: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{product_key}:{cadence}:price"))


def feature_id(product_key: str, cadence: str, feature_key: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{product_key}:{cadence}:feature:{feature_key}"))


def seat_class_id(product_key: str, cadence: str, code: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, f"{product_key}:{cadence}:seat_class:{code}"))


#: Every version this migration can insert, computed once at import time so `upgrade`, `downgrade`
#: and any test that loads this module by path name the same rows.
VERSION_IDS: dict[tuple[str, str], str] = {
    (product_key, cadence): version_id(product_key, cadence)
    for product_key, *_rest in NEW_PRICES
    for cadence in CADENCES
}


class SourceShapeError(RuntimeError):
    """The plan's current public version sells something one flat price cannot replace."""


class OpenDraftError(RuntimeError):
    """A plan this migration re-prices has a public draft its new versions would supersede."""


def _open_public_drafts(conn: sa.Connection, plan_id: str) -> list[int]:
    """The `version_no` of every public draft of the plan, never one of ours."""
    return [
        int(n)
        for n in conn.execute(
            sa.text(
                """
                SELECT version_no FROM catalog.plan_versions
                WHERE plan_id = CAST(:plan AS uuid)
                  AND restricted_to_org_id IS NULL
                  AND state = 'draft'
                  AND id <> ALL(CAST(:ours AS uuid[]))
                ORDER BY version_no
                """
            ),
            {"plan": plan_id, "ours": list(VERSION_IDS.values())},
        ).scalars()
    ]


def _source_version(conn: sa.Connection, plan_id: str) -> tuple[str, str] | None:
    """The plan's newest published public version (id, currency), never one of ours."""
    row = conn.execute(
        sa.text(
            """
            SELECT id, currency
            FROM catalog.plan_versions
            WHERE plan_id = CAST(:plan AS uuid)
              AND restricted_to_org_id IS NULL
              AND state = 'published'
              AND id <> ALL(CAST(:ours AS uuid[]))
            ORDER BY version_no DESC
            LIMIT 1
            """
        ),
        {"plan": plan_id, "ours": list(VERSION_IDS.values())},
    ).first()
    return (str(row[0]), str(row[1])) if row is not None else None


def _check_source_shape(
    conn: sa.Connection, product_key: str, source_id: str, source_currency: str
) -> tuple[list[str], list[str]]:
    """The source's feature keys and seat-class codes, after refusing any sheet this migration
    cannot re-price as one flat INR line without losing part of it."""
    prices = conn.execute(
        sa.text(
            """
            SELECT kind, currency, amount_minor FROM catalog.prices
            WHERE plan_version_id = CAST(:v AS uuid)
            """
        ),
        {"v": source_id},
    ).all()
    if source_currency != CURRENCY or [(k, c) for k, c, _a in prices] != [("flat", CURRENCY)]:
        raise SourceShapeError(
            f"{product_key}: the current public version {source_id} is not one flat {CURRENCY} "
            f"price (currency {source_currency}, prices {[(k, c) for k, c, _a in prices]}); "
            "re-pricing it as one flat line would drop part of what it sells. Publish a version "
            "that is exactly one flat INR price on the Plans page, or withdraw this plan's public "
            "versions so it is skipped, then re-run."
        )
    amount = prices[0][2]
    if amount != SEEDED_MINOR[product_key]:
        log.warning(
            "list prices 2026-10-06: %s sold at %s paise, not the %s paise the split was computed "
            "from; applying the table anyway — check it with the owner",
            product_key,
            amount,
            SEEDED_MINOR[product_key],
        )
    seats = conn.execute(
        sa.text(
            """
            SELECT code, price_id FROM catalog.seat_classes
            WHERE plan_version_id = CAST(:v AS uuid) ORDER BY code
            """
        ),
        {"v": source_id},
    ).all()
    paid = [code for code, seat_price in seats if seat_price is not None]
    if paid:
        raise SourceShapeError(
            f"{product_key}: the current public version {source_id} has paid seat classes "
            f"{paid}; a flat plan cannot carry them. Publish a version with only free seat "
            "classes on the Plans page, or withdraw this plan's public versions so it is "
            "skipped, then re-run."
        )
    features = conn.execute(
        sa.text(
            """
            SELECT feature_key FROM catalog.features
            WHERE plan_version_id = CAST(:v AS uuid) ORDER BY feature_key
            """
        ),
        {"v": source_id},
    ).scalars()
    return list(features), [str(code) for code, _price in seats]


def _insert_version(
    conn: sa.Connection,
    *,
    product_key: str,
    plan_id: str,
    cadence: str,
    source_id: str,
    feature_keys: list[str],
    seat_codes: list[str],
) -> None:
    """One new version: a draft, its sheet, then published — every step an insert except the
    draft's own `draft -> published`, which is the transition every operator publish makes."""
    new_version = VERSION_IDS[(product_key, cadence)]
    conn.execute(
        sa.text(
            """
            INSERT INTO catalog.plan_versions
                (id, plan_id, version_no, state, published_at, restricted_to_org_id,
                 billing_interval, currency)
            SELECT CAST(:v AS uuid), CAST(:plan AS uuid), COALESCE(max(version_no), 0) + 1,
                   'draft', NULL, NULL, :cadence, :currency
            FROM catalog.plan_versions
            WHERE plan_id = CAST(:plan AS uuid)
            """
        ),
        {"v": new_version, "plan": plan_id, "cadence": cadence, "currency": CURRENCY},
    )
    conn.execute(
        sa.text(
            """
            INSERT INTO catalog.prices (id, plan_version_id, kind, amount_minor, currency, unit)
            VALUES (CAST(:p AS uuid), CAST(:v AS uuid), 'flat', :amount, :currency, NULL)
            """
        ),
        {
            "p": price_id(product_key, cadence),
            "v": new_version,
            "amount": amount_for(product_key, cadence),
            "currency": CURRENCY,
        },
    )
    for key in feature_keys:
        # Copied in SQL, so the JSONB value is the stored one exactly — never re-serialised.
        conn.execute(
            sa.text(
                """
                INSERT INTO catalog.features (id, plan_version_id, feature_key, value)
                SELECT CAST(:id AS uuid), CAST(:v AS uuid), feature_key, value
                FROM catalog.features
                WHERE plan_version_id = CAST(:src AS uuid) AND feature_key = :key
                """
            ),
            {
                "id": feature_id(product_key, cadence, key),
                "v": new_version,
                "src": source_id,
                "key": key,
            },
        )
    for code in seat_codes:
        conn.execute(
            sa.text(
                """
                INSERT INTO catalog.seat_classes
                    (id, plan_version_id, code, name, price_id, included_count, can_write,
                     sort_order, description)
                SELECT CAST(:id AS uuid), CAST(:v AS uuid), code, name, NULL, included_count,
                       can_write, sort_order, description
                FROM catalog.seat_classes
                WHERE plan_version_id = CAST(:src AS uuid) AND code = :code
                """
            ),
            {
                "id": seat_class_id(product_key, cadence, code),
                "v": new_version,
                "src": source_id,
                "code": code,
            },
        )
    published = conn.execute(
        sa.text(
            """
            UPDATE catalog.plan_versions
            SET state = 'published', published_at = now(), updated_at = now()
            WHERE id = CAST(:v AS uuid) AND state = 'draft'
            """
        ),
        {"v": new_version},
    ).rowcount
    if published != 1:  # pragma: no cover - the row was inserted as a draft just above
        raise RuntimeError(f"{product_key}: version {new_version} could not be published")


def upgrade() -> None:
    conn = op.get_bind()
    # Two passes: every plan is checked — source shape, open drafts — before anything is inserted,
    # so a refusal names every plan in the way at once and the transaction has nothing to undo.
    pending: list[tuple[str, str, str, list[str], list[str], list[str]]] = []
    drafts: list[str] = []
    for product_key, code, _monthly, _yearly in NEW_PRICES:
        plan = conn.execute(
            sa.text(
                "SELECT id, state FROM catalog.plans WHERE product_key = :key AND code = :code"
            ),
            {"key": product_key, "code": code},
        ).first()
        if plan is None:
            log.warning("list prices 2026-10-06: no plan %s/%s; skipped", product_key, code)
            continue
        plan_id, plan_state = str(plan[0]), str(plan[1])
        if plan_state != "active":
            log.warning(
                "list prices 2026-10-06: plan %s/%s is %s; skipped", product_key, code, plan_state
            )
            continue

        missing = [
            cadence
            for cadence in CADENCES
            if conn.execute(
                sa.text("SELECT 1 FROM catalog.plan_versions WHERE id = CAST(:v AS uuid)"),
                {"v": VERSION_IDS[(product_key, cadence)]},
            ).first()
            is None
        ]
        if not missing:
            continue  # already applied: idempotent

        source = _source_version(conn, plan_id)
        if source is None:
            log.warning(
                "list prices 2026-10-06: plan %s/%s has no published public version; skipped",
                product_key,
                code,
            )
            continue
        source_id, source_currency = source
        feature_keys, seat_codes = _check_source_shape(
            conn, product_key, source_id, source_currency
        )
        open_drafts = _open_public_drafts(conn, plan_id)
        if open_drafts:
            drafts.append(f"{product_key}/{code} " + ", ".join(f"v{n}" for n in open_drafts))
        pending.append((product_key, plan_id, source_id, missing, feature_keys, seat_codes))

    if drafts:
        raise OpenDraftError(
            "list prices 2026-10-06: nothing applied — open public draft(s) on "
            + "; ".join(drafts)
            + ". The new versions would be numbered above each draft, so publishing it later "
            "would never take effect: the newest version per cadence is what is offered and shown "
            "as the price. Delete each draft on the Plans page (or publish it first, knowing this "
            "release supersedes it at once), then re-run."
        )

    for product_key, plan_id, source_id, missing, feature_keys, seat_codes in pending:
        for cadence in missing:
            _insert_version(
                conn,
                product_key=product_key,
                plan_id=plan_id,
                cadence=cadence,
                source_id=source_id,
                feature_keys=feature_keys,
                seat_codes=seat_codes,
            )


#: The frozen-row guards a delete of a published version and its sheet would trip. The insert
#: guards (`trg_<table>_frozen_no_insert`) are not involved in a delete.
_FROZEN_TRIGGERS = (
    ("prices", "trg_prices_frozen_immutable"),
    ("features", "trg_features_frozen_immutable"),
    ("seat_classes", "trg_seat_classes_frozen_immutable"),
    ("plan_versions", "trg_plan_versions_no_delete"),
)


def _present(conn: sa.Connection) -> list[str]:
    return [
        str(v)
        for v in conn.execute(
            sa.text("SELECT id FROM catalog.plan_versions WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": list(VERSION_IDS.values())},
        ).scalars()
    ]


def downgrade() -> None:
    conn = op.get_bind()
    present = _present(conn)
    if not present:
        return
    if os.environ.get(ALLOW_DESTRUCTIVE_ENV) != "1":
        raise RuntimeError(
            f"Refusing to downgrade {revision}: {len(present)} published plan version(s) of the "
            "2026-10-06 list prices exist, and catalog cannot see whether a subscription is pinned "
            "to one — deleting a pinned version breaks billing and the plan reads for everyone on "
            "it. Roll the application back instead (older catalog code reads these rows fine). To "
            f"delete them deliberately — after checking no subscription pins them — set "
            f"{ALLOW_DESTRUCTIVE_ENV}=1."
        )
    for table, trigger in _FROZEN_TRIGGERS:
        conn.execute(sa.text(f"ALTER TABLE catalog.{table} DISABLE TRIGGER {trigger}"))
    try:
        # Children first: a seat class may name a price (RESTRICT). Every row under these versions
        # is this migration's — nothing else can insert under a published version.
        for statement in (
            "DELETE FROM catalog.seat_classes WHERE plan_version_id = ANY(CAST(:ids AS uuid[]))",
            "DELETE FROM catalog.features WHERE plan_version_id = ANY(CAST(:ids AS uuid[]))",
            "DELETE FROM catalog.prices WHERE plan_version_id = ANY(CAST(:ids AS uuid[]))",
        ):
            conn.execute(sa.text(statement), {"ids": present})
        conn.execute(
            sa.text("DELETE FROM catalog.plan_versions WHERE id = ANY(CAST(:ids AS uuid[]))"),
            {"ids": present},
        )
    finally:
        for table, trigger in _FROZEN_TRIGGERS:
            conn.execute(sa.text(f"ALTER TABLE catalog.{table} ENABLE TRIGGER {trigger}"))
