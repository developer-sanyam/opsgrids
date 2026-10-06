# ADR-0036 — Removing test organisations: an owner-run tool, never an in-app delete

**Status:** Accepted (owner request, 2026-10-06: "Remove the dummy data from the production database"; the owner
confirmed the edge box is a staging server holding dummy organisations created by end-to-end, smoke and demo runs
beside his own). Built on `feat/purge-test-orgs-tool`, not merged or deployed · **Date:** 2026-10-06 ·
**Changes:** `tools/purge_test_orgs.py`, `tests/infra/test_purge_test_orgs*.py`, the RUNBOOK's "Removing test
organisations"; no service, migration, route or event

Builds on [ADR-0000](0000-phase-0-decisions.md) §1 (a user is global, membership is per organisation) and §10 (no
foreign key crosses a schema); [ADR-0002](0002-row-level-security.md) §2 (the owner role `opsgrids_admin` is BYPASSRLS
and owns every schema); [ADR-0009](0009-uploaded-images-and-object-storage.md) §3 (an object's key starts with its
organisation); and [ADR-0028](0028-putaway-gps-captures.md), which set the precedent this follows: an erasure the
application cannot do by privilege is a tool a person runs, with the admin DSN, a dry run by default, and counts-only
output.

## Context

The edge box holds 26 organisations. Most are not customers: every `e2e-…`, `e2e-signup-…`, `buyer-…` and `Smoke …`
run, and every demo, left an organisation behind with its users, subscriptions, invoices, ledgers and audit trail.
The owner asked for them to be removed from the database.

Verified in code before deciding:

1. **There is no way to delete an organisation.** No route, no consumer, no event (`organization.deleted` exists
   nowhere). That is by design: a tenant's records are append-only and audited, and the rule that deleting is a
   decision a person takes and records is the one ADR-0028 already applied to one table.
2. **Append-only is enforced two ways, and the owner role is below both.** `UPDATE` and `DELETE` are revoked from each
   *service* role (`apply_schema_grants` and each service's `migrations/env.py`), and 21 tables carry a row trigger that
   raises on `DELETE` (finance's filed returns and ledgers, payment's `refunds`, `provider_events` and
   `status_history`, subscription transitions, approval steps, deal activities, ITSM worklogs, a published automation
   version, and the frozen catalogue). None of them has an escape hatch: the function just raises. The owner role holds
   `DELETE` on all 342 tables and owns them, so it may `ALTER TABLE … DISABLE TRIGGER <name>`; it is not a superuser,
   so `session_replication_role` is not available to it.
3. **The foreign-key graph can be ordered.** 178 constraints, none across schemas, none deferrable, and no cycle
   even counting the `CASCADE` and `SET NULL` ones; many are `RESTRICT`, so an order matters. 252 tables carry an
   `organization_id`; ten more hang off those or off the user directory (`membership_roles`, `role_permissions`, the
   per-user `auth` tables).
4. **Row-level security would make a wrong role fail silently.** 246 tables force RLS; a role that does not bypass it
   sees no rows and a `DELETE` matches nothing without an error. That is the failure ADR-0002 already names for the
   outbox relay, and a purge that reports success having deleted nothing is the worst version of it.
5. **A user is global.** A person can belong to a test organisation and to the owner's own, and the only thing that
   ties them to either is a membership row.

## Decision

1. **Destruction is owner-run only.** The tool is run by the owner, by hand, on the box, after a backup. It is **never
   run by CI, never run by a deploy, never run by an agent** (no automation of any kind), has no `make` target or
   `remote.sh` subcommand, and is reachable through no HTTP route. A repository change can add or fix the tool; it
   cannot run it. Reading or writing production customer data is the owner's act, not a pipeline's or a developer's
   tooling's, and the tool is built so that nothing else needs to.
2. **Nothing is selected for you.** With no ids the tool only lists every organisation with a hint (a name or slug
   token like `e2e`, `smoke`, `buyer`, `test`, `demo`; every member on a throwaway mail domain; "idle": nothing active
   and nothing paid). A hint is printed next to a name and is never an input. Deletion takes explicit ids
   (`--org-id`, repeatable, or `--ids-file`); the option set is pinned by a test so a "delete everything that looks
   like test data" switch cannot be added without it noticing. **A name is a stranger's text**: signup is public and
   an organisation name is only length checked, so the listing and the candidates file print names through `inert`
   (every non-printable character, so every line break, escape and separator, becomes `?`; cut at 80), and an ids
   file is read as the owner's eyes read it: lines end at LF only, a line with any other control or separator
   character is refused, and an id must be the canonical hyphenated ASCII form. Without that, a name such as
   `smoke test<U+2028><another organisation's id> # x` put a second id in a file the owner believed held only hinted
   rows. Flags are matched in full, `--confirm-count` is given once, and every `--keep-file` and `--ids-file` repeated
   is read.
3. **Four gates, all before it connects.** `--apply`, `--confirm-count N` equal to the organisations named,
   `--i-took-a-backup`, and, when the environment is `production`, `staging` or `uat` (the set `ServiceSettings`
   already treats as real) **or the database host is not this machine**, `--i-know-this-is-the-live-database`. Listing
   and the dry run need none of them. It also refuses the nil-UUID platform organisation, any id named by `--keep` or
   `--keep-file`, a selection that would leave no organisation, and one that would orphan a child organisation. Naming
   a parent and its sub-organisation together is allowed: `parent_org_id` is `ON DELETE RESTRICT` and each organisation
   is its own transaction, so the child is processed first (the rest in id order), in the dry run and the apply alike;
   a loop of parent links has no order and is refused.
4. **The dry run is the same statements, read-only.** `would_delete` is `SELECT count(*)` over exactly the predicates
   `--apply` deletes with, in a `REPEATABLE READ READ ONLY` transaction, per table. `--apply` counts again inside each
   organisation's transaction, deletes, and checks that nothing is left before it commits.
5. **One transaction per organisation, as `opsgrids_admin`.** An interrupted or failed run leaves each organisation
   whole or untouched, and a re-run finishes the rest. Tables are *discovered* from the catalog (`pg_class`, `pg_attribute`,
   `pg_constraint`, `pg_trigger`; not `information_schema`, which hides a table the role has no privilege on), not
   listed, so a service added later is covered, and a table the role cannot read and delete from refuses the run
   instead of being skipped. They are deleted children first from the constraint graph; `--explain` lists the tables
   that are not reached and a database test pins that list. A table with no `organization_id` that hangs off one by a delete-blocking or cascading key is found by walking
   the keys and selected through its parent. The transaction sets the tenant GUC locally, takes an advisory lock so two
   runs cannot interleave, and uses a lock timeout so it fails rather than queues behind traffic.
6. **Delete guards are lifted by name, in the transaction, and put back.** Only a trigger that fires on `DELETE`, only on
   a table where the organisation has rows, `ALTER TABLE … DISABLE TRIGGER "<name>"` and then `ENABLE`, with `pg_trigger`
   checked before the commit. DDL is transactional, so a failure restores everything, and the lock the `ALTER` takes
   keeps writers off that table for the duration, so no other session ever runs unguarded. No superuser, no
   `session_replication_role`, no `DISABLE TRIGGER ALL` or `USER`, no `TRUNCATE`; tests read the tool's own SQL for each.
7. **Users follow their memberships.** The memberships go with the organisation. A user is deleted, with their
   credentials, second factors and sessions, only if every membership they had was in the organisations being removed
   (so a user in two selected organisations goes with the later one), they are not `is_staff`, and they are not named by
   `--keep-user`; a user in any organisation that stays is never touched. `--keep-orphan-users` removes memberships only.
   Operator accounts (`auth.staff_accounts`) are a separate table and are never touched.
8. **Counts only, and one durable record.** Output is per-table counts, ids and SQLSTATEs: no row, name, address or
   connection string (the interactive listing prints organisation names to the owner's terminal, because he cannot choose
   by id alone). Beside every id the dry run and the apply print `live_signals`: members, subscriptions by state and
   captured payments, counts a tenant cannot dress up as it can a name, so an id that is a customer stands out when the
   owner's last look is the dry run. With several organisations the dry run counts each as it will be found when its
   turn comes, without the rows an earlier one has already taken, so `would_delete` equals what the apply deleted for
   any selection, not only for one organisation. Each purged organisation leaves one counts-only row in `auth.audit_logs` under the platform sentinel
   organisation, in the same transaction, so a deletion that committed is on record and one that did not is not.
9. **What lives outside the database is named, never touched.** Object storage under `<bucket>/<organization>/`,
   provider-side mandates and connected accounts, DNS and certificates, in-flight events and dead letters, and backups
   taken before the run.

## What it leaves, on purpose

* `processed_events` in every schema: an idempotency ledger keyed by event id, with no organisation on it.
* `catalog.plan_versions.restricted_to_org_id`: a published plan version is immutable (CLAUDE.md); the count is
  reported per organisation. The catalogue is global.
* `auth.audit_logs` rows filed under the platform's nil organisation that name a removed organisation or one of its
  removed users: operator actions on the organisation, and authentication events that have no active organisation
  (an MFA factor re-sealed or enrolled), which carry the user's id, IP address and user agent. They are the platform's
  audit trail, not the tenant's, and the tool's own `organization.purged` rows live among them; deleting rows from the
  record of who did what to whom is a decision for a person, not a side effect of removing a tenant. They are counted
  per organisation in `left_in_place` (`auth.audit_logs.platform_rows_naming_removed`) so that "user deleted" is not
  read as "gone from every table".
* Anything that was only ever copied elsewhere: a backup, a provider's dashboard, a mail provider's log.

## Consequences

* **Irreversible except by restoring the backup.** The RUNBOOK says so first and puts the backup before the dry run;
  `--i-took-a-backup` is an attestation because the tool cannot see one. Restoring is the whole-database restore of
  "Restore from backup" (and loses what was written since), or an engineer copying one organisation back by hand,
  parents first. The owner is asked to run it when the box is quiet for that reason.
* **Not an erasure mechanism.** It removes organisations from the live database. It is not a data-subject erasure
  (`docs/engage-erasure-and-unsubscribe.md` covers what that takes) and it does not shorten any backup's life.
* **A new table is covered the day it ships**, provided it carries `organization_id` (CLAUDE.md already requires it).
  A new column that points at an organisation under another name must be added to `KNOWN_ORG_REFERENCES`; a test scans a
  migrated database and fails when one is not. A table that has neither is not reached, so the complement is pinned
  too: a database test lists every table the purge does not reach and fails until a new one is named there as not
  tenant data.
* **A late event recreates projection rows** for a removed organisation: a consumer folds an event with no check that
  the organisation exists (no foreign key crosses a schema, and consumers run inside the API containers), so an event
  still queued or in an outbox when the purge runs comes back as rows in `console` and elsewhere. The RUNBOOK therefore
  says to let the queues and outboxes drain before applying, to re-run the same command a few minutes after (a re-run
  removes what reappeared, and a non-zero re-run is that, not a tool error), and not to requeue a dead letter that
  names a removed organisation.
* **The real-database test is opt-in** (`OPSGRIDS_PURGE_ORGS_TEST_DATABASE_URL`), like the put-away purge's, because
  `tests/infra` is database-free. It builds one row in every table the tool reaches from the live catalogue, not a
  hand-picked few, and removes everything it made.

## Alternatives considered

* **Mark organisations `deleted` / soft-delete.** Hides them from the console but removes none of the data the owner
  asked to remove, and every consumer would need to learn the state. Rejected.
* **An in-app "delete organisation".** Needs a permission, a step-up, an audit design for the append-only ledgers it
  would have to destroy, and an event for every consumer. A decision this rare and this destructive belongs to a person
  with a backup. Rejected; ADR-0028's reasoning applies unchanged.
* **`session_replication_role = replica`, or dropping the triggers and re-creating them.** Switches every guard and every
  foreign-key check off for the whole session, needs a role the tool does not otherwise ask for, and a crash between
  drop and re-create leaves the guards gone. Rejected for the transactional, per-trigger `ALTER`.
* **Choosing by pattern** (`--delete-all-matching 'e2e-*'`). Quicker, and one wrong pattern away from a customer. Rejected:
  the hint prints; the person chooses.
* **A `make` target or `remote.sh` subcommand.** Shorter to type and one step closer to a pipeline running it. Rejected.

## Deviations from the request, recorded

* The request asked the tool to refuse "ENVIRONMENT anything but the owner explicitly passing" the live flag. The rule
  implemented: the flag is required for `--apply` (not for listing or the dry run, which read only) when the environment
  is `production`, `staging` or `uat`, or the host is not loopback whatever the environment says. The box says
  `ENVIRONMENT=production` even though the owner calls it staging, so the flag is what he will type.
* A user who is only a member of removed organisations is deleted by default (the request listed `account.users`
  among the directory tables to remove); `--keep-orphan-users` is the opt-out, and staff-flagged and `--keep-user`
  accounts are always kept.
* The deletion is audited in `auth.audit_logs` under the platform sentinel (not asked for): the deleted organisation's
  own audit rows go with it, so something has to say it happened.
