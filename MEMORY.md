# MEMORY.md — state and architecture log

The durable record of **where the system stands, what was decided, and what broke before**.
Append; do not rewrite history. A wrong entry gets a dated correction underneath, not a deletion.
What is in flight this week belongs in [`HANDOVER.md`](HANDOVER.md); the target design belongs in
[`SPEC.md`](SPEC.md).

Never record secrets, `.env` values, IP addresses, ssh key paths or customer data here.

---

## 1. Current architecture (as of 2026-10-14)

- **Production = `main` @ `d73bb9e4`** (deployed 2026-10-12, PRs #136–#147). No open PRs.
- 34 service directories, 2 of them gateways; 3 are still skeletons (`config`, `eventbridge`,
  `quotation`). 13 permission manifests, 34 OpenAPI documents.
- One Azure VM runs everything (ADR-0012/0013): ~36 containers, Compose profiles per product,
  Caddy, one shared `edge-relay`, consumers folded into API processes. The box is RAM-bound and at
  its owner-accepted ceiling (`EDGE_BUDGET_ACCEPTED_MB=3945`); **no new container without an owner
  decision on the ceiling.**
- GitHub Actions is unavailable (account billing) — CI and the Deploy workflow do not run. Deploys
  are manual `make deploy` by, or with explicit approval of, the owner.
- 15 web bundles published from `main`: dashboard, internal, buyer, worker, and one per product app.

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
