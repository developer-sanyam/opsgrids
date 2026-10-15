# MEMORY.md — state and architecture log

The durable record of **where the system stands, what was decided, and what broke before**.
Append; do not rewrite history. A wrong entry gets a dated correction underneath, not a deletion.
What is in flight this week belongs in [`HANDOVER.md`](HANDOVER.md); the target design belongs in
[`SPEC.md`](SPEC.md).

Never record secrets, `.env` values, IP addresses, ssh key paths or customer data here.

---

## 1. Current architecture (as of 2026-10-15)

- **Production = `main` @ `1dbeebd4`** (deployed 2026-10-15 in three batches, PRs #148–#170; the
  previous release was `d73bb9e4`, 2026-10-12). No open PRs. Pre-deploy backups exist on the box
  only: `BACKUP_S3_*` is empty, so there is no off-box copy.
- 34 service directories, 2 of them gateways; 3 are still skeletons (`config`, `eventbridge`,
  `quotation`). 13 permission manifests, 34 OpenAPI documents.
- Shared Python helpers now live in `platform-core` (#166, #169): `platform_core.catalogue`
  (permission-catalogue classes), `GuardedSweep` (every hand-written cron sweep), `supervise`,
  `db.constraint_name`/`sqlstate`, `pagination` keyset cursors, `auth.column_scope_resolver`,
  `events.actor_for`/`parse_envelope_dt`, `validation.reject_explicit_null`, `dns.answer_text`.
- Frontend shells are shared: `ProductShell`, `ProductLoginScreen` (`opsgrids-ui`) and
  `ProductAuthProvider` (`opsgrids-client`) replace the per-app copies. On a phone every app gets a
  bottom nav (destinations and quick actions per product), sheets for dialogs and menus, and
  card-style tables.
- Layout sweep (`frontend/opsgrids-visual/sweep/`) opens every route of 14 apps in sample mode; a
  developer tool, not CI. Visual baselines: 402 stories + 7 app pages, 2,454 PNGs, darwin only.
- One Azure VM runs everything (ADR-0012/0013): ~36 containers, Compose profiles per product,
  Caddy, one shared `edge-relay`, consumers folded into API processes. The box is RAM-bound and at
  its owner-accepted ceiling (`EDGE_BUDGET_ACCEPTED_MB=3945`); **no new container without an owner
  decision on the ceiling.**
- GitHub Actions is unavailable (account billing) — CI and the Deploy workflow do not run. Deploys
  are manual `make deploy` by, or with explicit approval of, the owner.
- 15 web bundles published from `main`: dashboard, internal, buyer, worker, and one per product app.
  `ci-plan.sh` now plans the worker and internal bundles too (#156).

## 2. Decisions

The ADRs are authoritative ([`docs/adr/`](docs/adr/), index in [`docs/decisions.md`](docs/decisions.md)).
The ones that bite most often in day-to-day work:

| ADR | Decision in one line |
|---|---|
| 0000 | Eleven irreversible choices: token shape, permission grammar, money as minor units, tenancy model |
| 0001 | One SPA per surface; apps never fork a shared component |
| 0002 | Forced RLS on a transaction-local GUC — a net, not the control |
| 0004 | Commerce is five services: catalog (immutable) / subscription (mutable pointer) / metering / billing / payment |
| 0007 | New permission codes reach live tenants only via `edge-reconcile`, which also grants new admin roles to owners |
| 0012/0013 | One box, not a cluster; products behind Compose profiles |
| 0017 | `lead` joins `crm`; `pipeline`/`activity` services deleted — `deal` owns `crm.pipeline.*` |
| 0021 | Consent is an append-only ledger; every send path asks one decision function |
| 0023 | Revealing PII needs a short-lived `otp` step-up token |
| 0035 | List price INR 30,000/month or 3,00,000/year for every product |
| 0036 | Test orgs are removed by an owner-run tool, never an in-app delete |
| 0038 | CRM programme D-1…D-41 — owner accepted every default on 2026-10-11 |
| 0040 | CI shards service tests by item on a tuned throwaway Postgres |

Recorded outside an ADR:

- **Mobile (2026-10-14, #157).** `DataTable` renders cards below 768px by default; opt out with
  `mobileCard="table"`. Text fields are 16px below 768px (iOS zooms smaller ones). Compact controls
  get a 44px target with the `hit-44` class. `PageHeader` folds `secondaryActions` into a menu on a
  phone. `StickyActionBar` is the one bottom action bar (#167). `FormDialog`, `Drawer`, `Menu` and
  `CommandMenu` become sheets that clear the notch.
- **Worker task display fields (2026-10-14, #159, ADR-0024 addendum).** Order number, bin code and
  item name on fulfilment tasks and packages are resolved server-side inside `operations`, and only
  for a caller with the matching org-wide read or a live offer on that task. No permission or role
  was widened, so there is no reconcile step.
- **Finance tax invoice (2026-10-15, `feat/gst-tax-invoice`).** The Finance customer invoice is a
  CGST Rule 46 tax invoice. Supplier address lives on `gst_registrations` (per state registration);
  PAN and bank details on `supplier_profiles`, the account number sealed with `platform_core.pii`
  and opened only by `GET /finance/invoices/{id}/print-details`, which audits
  `supplier_profile.bank_disclosed` every time. Invoices freeze seller/buyer name and address,
  buyer state and delivery address at issue. Lines carry UQC unit, discount (taxable = gross -
  discount) and an inventory item reference (plain UUID + SKU, picked through the Operations API
  in the browser, never joined). Round-off is opt-in per invoice and is inside `total_minor`
  (`total = subtotal + tax + round_off_minor`; IRP payload carries it). New document numbers are
  `INV-2627-00001` (Rule 46(b) caps a number at 16 characters; the old `INV-2026-27-00001` is 17);
  the same counter row continues. GST bands add 1.5 % and 40 % (40 % only from 2025-09-22); 12/28 %
  stay lawful. The issue screen refuses to start until `GET /finance/settings/supplier` is `ready`.
  No new permission codes.
- **Finance customer import (2026-10-15, `feat/finance-customer-import`).** ADR-0022 import/export on
  Finance → Customers. Customers gain a phone (sealed `phone_enc` + `phone_masked`; a bare Indian
  mobile is stored +91 server-side for every org). A live customer's GSTIN may not be repeated (409
  "already exists", which the import reports as skipped). No opening balance: Finance has no GL. No
  Tally mapping: the engine reads `.xlsx` with headers in row 1, which Tally's export does not have.
- **Marketing site.** The owner chose a "Liquid Glass" look with real product screenshots. It lives in
  the separate marketing repository, not here; this entry records the decision, not the code.

- Commit messages and PRs name no AI assistant.
- Tests run on real PostgreSQL, never SQLite.
- Each service is tested and typechecked in its own process (`import app` is ambiguous).
- Only `.claude/settings.json` is tracked under `.claude/`; the rest of the harness stays local.
- PRs always target `main`, never another PR's branch (2026-10-10: a stacked PR merged into a dead
  docs branch, and another auto-closed when its base was deleted).
- Risky behaviour ships dark first: `log_only` modes (`ORDER_INVOICING_MODE`,
  `CRM_CONVERSION_CLEANUP_MODE`) that the owner flips after reading the logs.

## 3. Bugs fixed — and the lesson each one left

Full write-ups for the early ones: [`docs/discoveries.md`](docs/discoveries.md). Every one of them
passed a green test suite; most were contract failures between two components that stubs hid.

| When | Bug | Lesson / guard |
|---|---|---|
| Phase 0 | account-service served none of its internal endpoints | E2E suite boots real services; stubs encode beliefs |
| Phase 0 | Revocation silently did nothing | Permission version bumps on every grant-changing path |
| Phase 0 | `staff_only` parsed and never enforced | Staff routes answer 404; tested at the gateway |
| Phase 0 | RLS inert for three independent reasons; GUC did not survive its transaction | NOBYPASSRLS roles, FORCE RLS, transaction-local GUC |
| Phase 0 | No service loaded its config file | `.env` found by walking up to the root `pyproject.toml` |
| 2026-09-24 | MinIO OOM-looped at 128 MiB, thrashing swap | `mem_limit 256m` + `GOMEMLIMIT`; every container has a memory fuse |
| 2026-09-24 | `compose up` never removes containers whose profile went inactive; health then fails | Stop *and* `docker rm` retired containers |
| 2026-09-24 | Staff console built on fixtures crashed on real API shapes | Build consoles against the real endpoint shape, not a fixture |
| 2026-09-25 | Owner lacked a new admin role after a manifest change | Reconciler now grants new admin roles to owners (#61) |
| 2026-09-26 | Domains page: "No route for /api/v1/domains" | `tests/infra/test_frontend_paths_are_routed.py` — every SPA path needs a prod route |
| 2026-09-26 | `notification` crash-looped in prod: code found `libs/contracts` via `Path(__file__).parents[3]`, which does not exist in the image | Never read repo-relative paths at runtime — the image holds only `services/<name>/`; a green health gate is not a working service (#67) |
| 2026-09-27 | WhatsApp transient send failures never retried; no number `register` call | Fixed in #70; design and audit in `docs/whatsapp/` |
| 2026-10-06 | Admin console published from a stale branch while reported live | `make edge-web` builds the local checkout — check out the deployed commit, then grep the published bundle |
| 2026-10-10 | CRM 360 view called `/deals` instead of `/api/v1/deals` | Fixed in R1.2a (#122) with an internal-client guard |
| 2026-10-11 | Deploy stopped at migrate: two Alembic heads in `notification` | Single-head test per service (#133) |
| 2026-10-12 | IDOR on several routes | Fixed in #136; every endpoint needs a negative-authz test |
| 2026-10-12 | Six orders stuck in `on_accept` | Runbook `docs/runbooks/2026-10-12-unblock-on-accept-orders.md` |
| 2026-10-14 | Engage and Operations screens showed "Request failed with 500" in sample mode | The sample-data fixtures lacked those endpoints, not the API. A sampleMode guard test now fails when a screen calls an endpoint no fixture serves (#149, #153) |
| 2026-10-14 | `AppShell` bottom padding never applied | An unlayered CSS rule beat the layered utility. Spacing the shell owns must not be overridable by an unlayered rule (#150) |
| 2026-10-14 | Money shown ÷100 for JPY and KWD; instants rendered in UTC or an unlabelled host zone | Formatters use the currency's own exponent and the viewer's zone, and name it (#156) |
| 2026-10-14 | `SENTRY_DSN` and OTLP settings were declared, passed to every service and required by preflight, but read by nothing | Removed; `docs/operations.md` says error tracking is not wired. A test pins it (#156) |
| 2026-10-14 | `ci-plan.sh` never planned the worker and internal bundles | Added to the app list; the plan test pins the full list (#156) |
| 2026-10-14 | `ENGAGE_TRACKING_SECRET` set on the box had no effect | Compose never forwarded it to notification; it does now (#156) |
| 2026-10-14 | Sweep failure backoff waited a full poll interval before the first retry | Backoff starts at `min(poll_interval, max_backoff)` (#156) |
| 2026-10-14 | `make boundaries` failed for every service except `operations` | The shared `.importlinter` held operations-only contracts. They moved to `.importlinter.operations`; a `tests/infra` check keeps the shared contracts identical (#166) |
| 2026-10-14 | `product-engage` import cycles | Request shapes moved into leaf `types` modules (#167) |
| 2026-10-14 | Sticky `FormDialog` footer sat 16px above the scroll body's edge, so fields showed beneath it | `-bottom-4` makes it flush (#170) |

## 4. Known sharp edges

- `make secrets` flags quoted hex ids and password-like words in docs — write Alembic revision ids bare.
- Local service tests need a reachable Redis; `.env.example` points at 6380 — set `REDIS_URL` if yours differs.
- Auth tests 401 when the worktree `.env` sets `ADMIN_GATEWAY_HOP_KEY`.
- The dev Postgres container mounts `init-db.sql` from the checkout that created it; deleting that
  worktree breaks the container — recreate it from the main checkout.
- `e2e/edge_*` scripts create organizations in production. Do not run them against prod.
- A SPA fallback returns 200 for any path; `curl` proves nothing about a bundle.
- Never `alembic downgrade` past a migration once data that depends on it exists (put-away captures,
  phone-only contacts, captured leads).

## Log

Add one dated line per architecture change, decision, or fixed bug that a future session needs.

- 2026-10-14 — SPEC.md, MEMORY.md, HANDOVER.md added; `.claude/settings.json` tracked as the project permission policy.
- 2026-10-14 — Finance prints a GST tax invoice and credit note at `/invoices/:id/print` (#151); Operations generates `ITM-000001`-style item codes when the SKU is blank, per organization (#152).
- 2026-10-14 — Mobile-responsive shared UI and the layout sweep (#155, #157, #161, #163, #164); worker app shows real order numbers, bins and items (#159); audit fixes (#156); dead code removed (#158).
- 2026-10-14 — Duplicate helpers consolidated into `platform-core`, `tools/`, `tests/infra` and `e2e` (#165–#169); visual baselines regenerated (#170).
- 2026-10-15 — Production deployed to `main` @ `1dbeebd4` in three batches. Docs refreshed: HANDOVER, MEMORY, SPEC, `docs/operations.md`, `docs/production-readiness.md`; `docs/what-was-built.md`, `docs/theme-guide.md` and `docs/architecture.html` marked historical.
- 2026-10-15 — CRM retest fixes (`fix/crm-retest-2026-10-15`): a lead with no explicit owner falls back to its creator when the round-robin picks nobody (`fallback_owner_id` when in the caller's scope -- an out-of-scope one is ignored, not a 403 -- else employee link, else ORG membership id; `manual` mode still assigns nobody), and the lead page can assign an owner. A self-serve org has `country: null`, so the SPA reads bare mobiles as +91 when the org runs in INR or on Asia/Kolkata. Gateway pool keep-alive (2 s) is now below uvicorn's 5 s and a dropped-connection GET is resent once: equal timers caused transient 500s on first load. `PUT /customers/owners/me` survives two concurrent first calls.
- 2026-10-15 — Edge build resilience (`fix/edge-build-resilience`): the batch 7 deploy of #178 failed three times at build. A full rebuild was one `docker compose build`, i.e. one buildx bake of ~30 images at once (ignores `COMPOSE_PARALLEL_LIMIT`, and on compose v5 `COMPOSE_BAKE=false`), so ~30 concurrent `uv sync` runs, and one wheel always timed out after uv's 30 s x 3. `remote.sh build` now builds one service at a time (`build_each`, `lib/rebuild-decision.sh`, tested); the shared builder stage makes every image after the first a cache hit. Every service Dockerfile and the `tools/new_service.py` template set `UV_HTTP_TIMEOUT=120 UV_HTTP_RETRIES=5 UV_CONCURRENT_DOWNLOADS=4`. Also: #178's `_creator_owner_candidate` read raw `ctx.subject_ref` and broke `tests/infra/test_no_raw_subject_ref.py` (the R2.7 ratchet); it now reads `ctx.own_ref` (same id for lead today). Run `make test-infra` for any CRM service change.
- 2026-10-15 — Session persistence checked after a tester was signed out between browser runs: the tenant refresh cookie is persistent (`Max-Age` 7d, host-only on `app.`, Lax, HttpOnly, Secure in prod) and every SPA boot-refreshes, so tenant apps were fine. The operator console (`opsgrids-internal`) never called `/auth/operator/refresh`: it now boot-refreshes and retries a 401 once. Logout now clears the cookie with the same Domain it was set with.
- 2026-10-15 — Organization audit log (ADR-0044, `feat/org-audit-log`): each service serves its own `audit_logs` at `/api/v1/audit/<service>` through `platform_core.audit_read` (field names, never values); the dashboard merges them at `/admin/audit`; `ORG_ADMIN` gains `core.identity.audit.read`, so live orgs need `make edge-reconcile PRODUCTS=core`.
- 2026-10-16 — HRMS first-test fixes (#187): org owner may approve their own *unassigned* request on an explicit `self_approve` (audited `approval.request.self_approved`, `approval_requests.self_approved`, approval migration `a4e9c2b7d1f6`; its downgrade re-adds the strict check `NOT VALID`, upgrade re-derives the flag). `add_member` adds core MEMBER when the role list has no core role; account migration `b3e8d5a1c7f9` backfills MEMBER for members holding only product roles. Owner-only welcome email. Attendance day rolls over at Asia/Kolkata midnight (one zone for all orgs, `org_timezone`).
