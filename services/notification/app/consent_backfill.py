# ruff: noqa: S608 — every f-string here interpolates only the SCHEMA constant; values are bound.
"""Carry what the two legacy stores already say into the consent ledger.

Run once by the migration that creates `consent_events`, and safe to run again at any time
(`python -m app.consent_backfill`, as the **admin** role): every row it writes carries a
deterministic `idempotency_key` (`legacy:<store>:<row id>[:<purpose>]`) under the ledger's unique
index, so a second run writes nothing. That matters for one real window — contacts created by the
previous release between the migration and the new code taking over have no ledger event, and the
new code will not select them for campaigns until they do; re-running this picks them up.

**What becomes what.**

* Every `Suppression` becomes an `opted_out` event, the reason mapped to a source:
  `unsubscribed` → `unsubscribe_link`, `manual` → `admin`, `bounced` → `bounce`,
  `complained` → `complaint`. *Which purposes* is chosen to keep today's behaviour exactly: an
  unsubscribed or manual suppression only ever stopped **campaigns**, so it opts out of
  `marketing` alone (rule-driven transactional mail to that address carried on, and still does);
  a bounce or complaint is a fact about the mailbox and opts out of both.
* Every `Preference` refusal (`suppressed = true`, email or WhatsApp) becomes `opted_out` events
  for the purposes its category covers (`consent.purposes_for_category`): `*` is both,
  `marketing` is marketing, anything else is a rule-authored transactional bucket. A refusal a
  customer typed (`reason = customer_stop`) is sourced `inbound_keyword`; any other is `admin`.
  A lifted preference (`suppressed = false`) is not a refusal and writes nothing.
* Every active, **unsuppressed** `Contact` with no marketing event yet becomes an `opted_in`
  email/marketing event, `source = legacy_import`, so the campaigns that select them today keep
  selecting the same people. **That is unverified consent**: nothing records how or when these
  people agreed, and the evidence text says so. There is deliberately **no** WhatsApp equivalent —
  no legacy WhatsApp marketing send exists, so WhatsApp marketing needs an explicit `opted_in`
  event from now on.

The sealed address is **copied, never opened**, for suppressions and contacts (`email_enc` →
`subject_enc`); only a `Preference`, which stores its recipient in the clear, has to be sealed —
and once it is sealed here it is sealed for good.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import settings
from app.consent import (
    SOURCE_ADMIN,
    SOURCE_INBOUND_KEYWORD,
    SOURCE_LEGACY_IMPORT,
    purposes_for_category,
)
from app.models import SCHEMA
from platform_core import pii
from platform_core.logging import get_logger

log = get_logger("notification.consent_backfill")

#: Stated on every carried-over contact so nobody reading the ledger mistakes it for proof.
LEGACY_CONTACT_EVIDENCE = (
    "Existing contact carried over when the consent ledger was introduced; how and when they "
    "agreed is not recorded (unverified)."
)

_ON_CONFLICT = (
    "ON CONFLICT (organization_id, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING"
)

_COLUMNS = (
    "id, organization_id, channel, purpose, subject_enc, subject_masked, subject_index, "
    "contact_id, status, source, evidence, recorded_by, occurred_at, created_at, idempotency_key"
)

#: How many preference rows are sealed and inserted per round trip.
_PREFERENCE_BATCH = 500


@dataclass(frozen=True, slots=True)
class BackfillCounts:
    """Rows written by one run — zero across the board on a re-run."""

    suppressions: int
    preferences: int
    contacts: int

    @property
    def total(self) -> int:
        return self.suppressions + self.preferences + self.contacts


def _backfill_suppressions(connection: Connection) -> int:
    """One `INSERT ... SELECT`: the sealed address is copied column to column, never opened."""
    result = connection.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.consent_events ({_COLUMNS})
            SELECT gen_random_uuid(), s.organization_id, 'email', m.purpose,
                   s.email_enc, s.email_masked, s.email_index,
                   NULL, 'opted_out', m.source,
                   'Legacy suppression (reason: ' || s.reason || '); carried over from '
                       || 'notification.suppressions when the consent ledger was introduced.',
                   NULL, s.created_at, now(),
                   'legacy:suppression:' || s.id::text || ':' || m.purpose
            FROM {SCHEMA}.suppressions s
            JOIN (VALUES
                ('unsubscribed', 'marketing',     'unsubscribe_link'),
                ('manual',       'marketing',     'admin'),
                ('bounced',      'marketing',     'bounce'),
                ('bounced',      'transactional', 'bounce'),
                ('complained',   'marketing',     'complaint'),
                ('complained',   'transactional', 'complaint')
            ) AS m(reason, purpose, source) ON m.reason = s.reason
            WHERE s.deleted_at IS NULL
            {_ON_CONFLICT}
            """
        )
    )
    return int(result.rowcount or 0)


def _backfill_preferences(connection: Connection) -> int:
    """Sealed here because a `Preference` holds its recipient in the clear — the one legacy row
    that has to be opened rather than copied. Batched by primary key, and a row that already has
    events is skipped before it is sealed, so a re-run does no crypto for rows it has done."""
    cfg = settings()
    written = 0
    after = uuid.UUID(int=0)
    while True:
        rows = (
            connection.execute(
                text(
                    f"""
                    SELECT p.id, p.organization_id, p.recipient, p.channel, p.category,
                           p.reason, p.updated_at
                    FROM {SCHEMA}.preferences p
                    WHERE p.suppressed
                      AND p.deleted_at IS NULL
                      AND p.channel IN ('email', 'whatsapp')
                      AND p.id > :after
                      AND NOT EXISTS (
                          SELECT 1 FROM {SCHEMA}.consent_events e
                          WHERE e.organization_id = p.organization_id
                            AND e.idempotency_key LIKE 'legacy:preference:' || p.id::text || ':%'
                      )
                    ORDER BY p.id
                    LIMIT :batch
                    """
                ),
                {"after": after, "batch": _PREFERENCE_BATCH},
            )
            .mappings()
            .all()
        )
        if not rows:
            return written
        params: list[dict[str, object]] = []
        for row in rows:
            channel = str(row["channel"])
            kind = "email" if channel == "email" else "phone"
            recipient = str(row["recipient"])
            enc, masked = pii.seal(recipient, kind=kind, settings=cfg)
            index = pii.blind_index(recipient, kind=kind, settings=cfg)
            customer_stop = row["reason"] == "customer_stop"
            # The operator's free-text `reason` is deliberately not copied: this column is not
            # sealed, and a sentence somebody typed into a reason box may name the person.
            evidence = (
                f"Legacy preference opt-out (category {row['category']}; "
                f"{'customer STOP' if customer_stop else 'operator-set'}); carried over from "
                "notification.preferences when the consent ledger was introduced."
            )[:500]
            for purpose in purposes_for_category(str(row["category"])):
                params.append(
                    {
                        "id": uuid.uuid4(),
                        "organization_id": row["organization_id"],
                        "channel": channel,
                        "purpose": purpose,
                        "subject_enc": enc,
                        "subject_masked": masked,
                        "subject_index": index,
                        "source": SOURCE_INBOUND_KEYWORD if customer_stop else SOURCE_ADMIN,
                        "evidence": evidence,
                        "occurred_at": row["updated_at"],
                        "idempotency_key": f"legacy:preference:{row['id']}:{purpose}",
                    }
                )
        result = connection.execute(
            text(
                f"""
                INSERT INTO {SCHEMA}.consent_events ({_COLUMNS})
                VALUES (:id, :organization_id, :channel, :purpose, :subject_enc, :subject_masked,
                        :subject_index, NULL, 'opted_out', :source, :evidence, NULL,
                        :occurred_at, now(), :idempotency_key)
                {_ON_CONFLICT}
                """
            ),
            params,
        )
        written += int(result.rowcount or 0)
        after = rows[-1]["id"]


def _backfill_contacts(connection: Connection) -> int:
    """Active contacts that are not suppressed and have no marketing event yet. Excluding the
    suppressed keeps a contact re-imported after an unsubscribe from being handed an opt-in that
    post-dates the refusal; excluding the ones with events leaves any decision already recorded —
    including one made by the new code before this ran again — exactly as it is."""
    result = connection.execute(
        text(
            f"""
            INSERT INTO {SCHEMA}.consent_events ({_COLUMNS})
            SELECT gen_random_uuid(), c.organization_id, 'email', 'marketing',
                   c.email_enc, c.email_masked, c.email_index,
                   c.id, 'opted_in', :source, :evidence,
                   NULL, c.created_at, now(),
                   'legacy:contact:' || c.id::text
            FROM {SCHEMA}.contacts c
            WHERE c.deleted_at IS NULL
              AND NOT EXISTS (
                  SELECT 1 FROM {SCHEMA}.suppressions s
                  WHERE s.organization_id = c.organization_id
                    AND s.email_index = c.email_index
                    AND s.deleted_at IS NULL
              )
              AND NOT EXISTS (
                  SELECT 1 FROM {SCHEMA}.consent_events e
                  WHERE e.organization_id = c.organization_id
                    AND e.channel = 'email'
                    AND e.purpose = 'marketing'
                    AND e.subject_index = c.email_index
              )
            {_ON_CONFLICT}
            """
        ),
        {"source": SOURCE_LEGACY_IMPORT, "evidence": LEGACY_CONTACT_EVIDENCE},
    )
    return int(result.rowcount or 0)


def backfill_legacy_consent(connection: Connection) -> BackfillCounts:
    """Fill the ledger from what already exists. Idempotent; see the module docstring.

    Takes a synchronous `Connection` because its two callers hold one: alembic's `op.get_bind()`,
    and `await conn.run_sync(...)` from a test or the CLI below. Runs as the admin role — the
    ledger's RLS policy names the application role only, and a backfill has no tenant to bind.

    Order matters: suppressions first, so the contacts pass can see them.
    """
    counts = BackfillCounts(
        suppressions=_backfill_suppressions(connection),
        preferences=_backfill_preferences(connection),
        contacts=_backfill_contacts(connection),
    )
    log.info(
        "notification.consent_backfill.done",
        suppressions=counts.suppressions,
        preferences=counts.preferences,
        contacts=counts.contacts,
    )
    return counts


async def _main() -> BackfillCounts:
    cfg = settings()
    engine = create_async_engine(
        cfg.admin_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    try:
        async with engine.begin() as connection:
            return await connection.run_sync(backfill_legacy_consent)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    print(asyncio.run(_main()))  # noqa: T201 - a CLI's output is its result
