# The CRM: OpsGrids Revenue

This is the one page that says what the CRM is, in the words the rest of the repository should use.
It describes the three CRM services as they are, says which release changes each thing that is
about to change, and ends with how to change it. The decisions behind the CRM overhaul are in
[ADR-0038](adr/0038-crm-programme-decisions.md); the programme contract is
[the programme design](superpowers/specs/2026-10-07-crm-overhaul-programme-design.md); the names a
release must use are in
[the interface registry](superpowers/specs/2026-10-07-crm-overhaul-interface-registry.md).

**How to read it.** What the code or the data shows is written as fact, with a `path:line` that can
be checked; every path is from the repository root. What the programme only proposes is worded
"proposed in ADR-0038 (pending D-n)" and stays a proposal until the owner answers that row: it is
never written as an outcome here. *Inference* marks a statement that no code or test observed.

## 1. What the CRM is

The CRM is one product: the entitlement key `crm`, sold as **OpsGrids Revenue** and labelled "CRM"
in the module switcher (`frontend/opsgrids-client/src/platform/catalogue.ts:46`). Its one front end
is `frontend/product-crm`, which names itself "OpsGrids Revenue"
(`frontend/product-crm/src/App.tsx:79`) and is served from `https://revenue.opsgrids.com`
(`frontend/opsgrids-client/src/platform/catalogue.ts:70`).

Three backend processes serve it. Each has its own Postgres schema and database role, and shares
nothing with the others but the platform libraries and plain UUIDs (ADR-0000 §10, ADR-0017):

- **customer** is the account book: companies, contacts, the rep directory and the Customer 360.
- **deal** is the pipeline: deals and the append-only activity timeline.
- **lead** is intake: leads, the assignment rule, and the conversion of a lead into a company, a
  contact and a deal.

What the CRM is not:

- A tenant is not a CRM company. OpsGrids' own billing customers are tenants
  (`account.Organization`), a different thing from a CRM company that must never be merged with it
  (`P§3` F5).
- ITSM is internal IT. It is deliberately not connected to the CRM for customer data (`P§6`).
- "Sales CRM" is not a product, and `pipeline` and `activity` are not services: ADR-0017 deleted
  them, and `lead` joined `crm` as its third process.
- It is sold on its own: ADR-0008 Q5 says "CRM is sold standalone", so an organization can hold
  `crm` without `hrms`. The Customer 360 treats a section it cannot produce, or whose product the
  organization does not hold, as `null` and lists it in `unavailable`; `channels` is the exception,
  an empty object that is still listed (`services/customer/app/c1_360.py:9-26`; `assemble` puts an
  empty `ChannelsSection` in its place).

## 2. Service map

| Service | Schema and role | Dev port | Dev gateway prefixes | Production upstream | Memory kind | Entrypoints in `bin/` | Consumer | Tables | ADRs |
|---|---|---|---|---|---|---|---|---|---|
| customer | `customer`, `opsgrids_customer` (`services/customer/app/config.py:9-15`) | 8130 | `/api/v1/customers`, `/api/v1/contacts` | `http://customer:8000` | `api_with_worker`, 99 MB (`infrastructure/edge/memory-budget.json:310`) | `api`, `relay`, `worker` | folded into the API when `RUN_CONSUMERS_IN_API` is on (`services/customer/app/config.py:17-23`); one binding, `crm.pipeline.activity.logged` (`services/customer/app/consumers.py:97`) | `owners`, `companies`, `contacts` | ADR-0017, ADR-0008 Q5 |
| deal | `deal`, `opsgrids_deal` | 8131 | `/api/v1/deals` | `http://deal:8000` | `api`, 98 MB (`infrastructure/edge/memory-budget.json:320`) | `api`, `relay`, `worker` | none: `services/deal/app/consumers.py` declares the queue `deal.events` and registers no handler, and no container runs it | `deals`, `activities` | ADR-0017 decision 4, ADR-0020 |
| lead | `lead`, `opsgrids_lead` | 8135 | `/api/v1/leads` | `http://lead:8000` | `api`, 98 MB (`infrastructure/edge/memory-budget.json:321`) | `api`, `relay` (no `worker`) | none: there is no `app/consumers.py` | `leads`, `assignment_rules` | ADR-0017 |

Each schema also holds the platform's `outbox`, `processed_events` and `audit_logs`.

- **Routing.** The gateway carries `product: crm` on each CRM row, so the entitlement gate applies
  before any CRM code runs. Development rows are `services/app-gateway/routes.json:76-79`;
  production rows are `infrastructure/edge/routes.prod.json:106-109`. A request path is matched with
  `startswith(prefix)`, longest prefix first (`libs/platform-core/src/platform_core/gateway.py:233,253-254`).
- **No dead development rows.** The development table once also routed `/api/v1/campaigns` (port
  8132) and `/api/v1/tickets` (port 8133) under product `crm`, to services that never existed.
  R1.2b removed both, and `tests/infra/test_route_table_parity.py` now fails if the development and
  production tables carry different CRM prefixes.
- **Memory.** The three CRM containers cost 295 MB: the budget prices the core alone at 1772 MB and
  core plus the `crm` profile at 2067 MB (commands in §10).
- **One product key.** All three services contribute to the single `crm` permission manifest,
  `libs/contracts/permissions/crm.json` (§9).

## 3. Vocabulary

"Stored" is what is on the wire or in a column today and is frozen where tenant data carries it;
"label" is what users see.

| Term | Means | Stored or on the wire today | Not to be used for |
|---|---|---|---|
| tenant | An OpsGrids customer organization (`account.Organization`); every row carries its `organization_id` | `organization_id`, claim `act_org` | the CRM's company; new copy never says "customer" for a tenant |
| lead | A pre-qualification intake record that may become a company, a contact and a deal; table `lead.leads` | status `new, working, qualified, disqualified, converted` and source `form, import, manual` (`services/lead/app/schemas.py:26-27`); `/api/v1/leads`; events `crm.lead.*` | a deal stage, a company status or a contact status as a *label*. The stored value `lead` stays on `company.status`, `contact.status` and as the first `deals.stage` key (frozen: events and tenant rules carry it); a later release relabels it in the UI and never rewrites the value |
| company | The **party**: a business or a one-person buyer; table `customer.companies` | status `customer, prospect, lead, churned` (`services/customer/app/schemas.py:38`); URL noun `customers` today (proposed in ADR-0038, pending D-22: `companies`, with the old prefix kept); events `crm.book.company.*` | "customer"; finance's billed party |
| contact | A **person** at a company; table `customer.contacts`. Today `email` is required and unique among live rows (`services/customer/app/models.py:171,222-228`); the phone is sealed (`phone_enc`), masked (`phone`) and blind-indexed (`phone_index`). Phone-only contacts are proposed in ADR-0038 (pending D-14) | status `customer, qualified, lead, dormant` (`services/customer/app/schemas.py:39`); `/api/v1/contacts`; events `crm.book.contact.*` | a lead |
| deal | An opportunity with a value in integer minor units and an ISO currency, a stage, a probability and an expected close date; table `deal.deals` | `/api/v1/deals`; events `crm.pipeline.deal.*` | a lead |
| stage | One step of a deal's pipeline. Today six fixed keys `lead, qualified, proposal, negotiation, closed_won, closed_lost` (`services/deal/app/domain.py:21-28`). Proposed in ADR-0038 (pending D-5): an immutable key with an editable label, order, tone and default probability and an **outcome** (`open`, `won`, `lost`); consumers would test the outcome, never the key | `deals.stage` String(20) | a company or contact status |
| activity | A logged past interaction (call, email, meeting, note, task). Append-only: a database trigger refuses UPDATE and DELETE. Table `deal.activities`; `last_activity_at` on a contact and on a deal is derived from it | `/api/v1/deals/activities` (proposed in ADR-0038, pending D-22: `/api/v1/activities` as a second path to the same handlers, the old path kept); event `crm.pipeline.activity.logged` | a to-do; the insights feed (to be called "feed") |
| follow-up | A dated future task for a rep. Not an activity: activities are past-only and append-only. Proposed by the programme (R3.6), not built | none today; a table `deal.follow_ups` is proposed | — |
| owner, rep | The member responsible for a record. **Today** an `owner_id` is a `customer.owners.id` (a generated uuid the public route cannot choose) or, on deals and activities, the caller's `subject_ref`, an HR employee id: a mix (`P§3` F2). Proposed in ADR-0038 (pending D-7): the account membership id | `owner_id` on companies, contacts, deals, activities and leads; `assignment_rules.owner_ids` | an HR employee; the platform `ORG_OWNER` role |
| roster | `customer.owners`: the CRM-local rep directory (`/api/v1/customers/owners`); proposed to become a thin view of members after C15 (pending D-7) | — | — |
| membership | A user's per-organization link (`account.memberships`), carried as the `mem` claim (`Principal.membership_id`); the proposed rep identity (pending D-7) | `mem`, `X-Membership-Id` | — |
| employee link | `memberships.employee_id`, carried as `sref` (`Principal.subject_ref`). Only HRMS-shaped products key OWN on it. CRM compares `owner_id` with it today, which is why OWN is empty in a tenant without HRMS (`P§3` F1) | `sref`, `X-Subject-Ref` | CRM rep identity |
| customer | Reserved for the *relationship status* of a company. For historical reasons it is also the service and schema name, the URL noun for companies, the name of the 360 route and the dashboard "Customers" KPI (`countCrmCustomers`, `frontend/opsgrids-dashboard/src/api/index.ts:381`) | status value `customer`; `/api/v1/customers` | the table name for companies; a tenant |
| billed party | Finance's invoicing profile of a company (`finance.customers`; its `crm_contact_id` bridge is unwired, `P§3` F5) | `finance.customers` | a CRM company |
| customer 360 | The per-contact aggregate `GET /api/v1/customers/{contact_id}/360` (proposed in ADR-0038, pending D-22: `/api/v1/contacts/{id}/360`): sections `customer, crm, orders, finance, support, channels` and a timeline; a section that cannot be produced is `null` (`channels`: an empty object) and named in `unavailable` | — | an account-level view (a company 360 is proposed for R4.3) |
| book | The module word of `crm.book.*`: customer-service's slice (companies, contacts, owners) | permission codes | a product |
| pipeline | The module word of `crm.pipeline.*`: deal-service's slice (deals, activities). Not a service: `services/pipeline` was deleted (ADR-0017) | permission codes, event types | a service |
| party, person, rep | The identity model (`P§4.2`): company is the party, contact the person, membership the rep. Other products link by `crm_company_id` and `crm_contact_id`, never `customer_id` (proposed in ADR-0038, pending D-14) | none today; the two column names are proposed | — |
| scope | The ladder OWN, TEAM, LOCATION, ORG. CRM implements OWN and ORG; a TEAM or LOCATION grant on a CRM code is accepted by account and refused at runtime (`libs/platform-core/src/platform_core/auth.py:344,353`); TEAM is proposed in ADR-0038 (pending D-24) | grants `code:SCOPE` | — |
| ahead of the producer | A contract term: a committed event schema whose required fields the producer does not emit yet. Five CRM schemas are (§8) | the schema's `$comment` | — |
| OpsGrids Revenue, CRM | The product: entitlement key `crm`, marketplace and SPA name "OpsGrids Revenue", navigation label "CRM", host `revenue.opsgrids.com` | claim `ent` contains `crm` | — |

## 4. Identity and scope today

Three different ids are called "the owner" in this repository, and the CRM mixes them.

- **The roster id.** `customer.owners.id` is a uuid the model generates; the public route cannot
  choose it, because `OwnerIn` takes a name, an email and a role only
  (`services/customer/app/schemas.py:50-58`). It is the value of `owner_id` on companies, contacts
  and leads, and on deals and activities when a caller names one.
- **The membership id.** A user's per-organization link, carried as the `mem` claim and the
  `X-Membership-Id` header (`services/auth/app/tokens.py:108-109`,
  `libs/platform-core/src/platform_core/gateway.py:665-672`) and named `Principal.membership_id`
  (`libs/platform-core/src/platform_core/auth.py:68`). The CRM's scope does not use it.
- **The employee id.** `memberships.employee_id`, carried as `sref` and named
  `Principal.subject_ref` (`services/auth/app/services.py:378,462`,
  `libs/platform-core/src/platform_core/auth.py:70`). Only an HR link ever sets it.

OWN scope compares a row's `owner_id` with `subject_ref`: `ScopeContext.apply` filters on
`owner_col == subject_ref` and `assert_can_touch` compares the same two values
(`libs/platform-core/src/platform_core/auth.py:341,373`). With no `subject_ref`, an OWN reader
matches nothing (`:337-340`) and an OWN write or detail read is refused (`:372-381`). Deals and
activities default `owner_id` to the caller's `subject_ref` when the caller names none
(`services/deal/app/api/routes.py:112,241-243`).

**The consequence for a tenant without HRMS** (CRM sold on its own): no member has an employee
link, so `subject_ref` is null for everyone. Default reps read at ORG and write, convert and reveal
at OWN (`services/customer/app/catalogue.py:168-174`, `services/deal/app/catalogue.py:150-155`,
`services/lead/app/catalogue.py:172-177`), so their lists stay visible and their OWN actions answer
403. The owner values already on rows are a mix of roster ids and employee ids (`P§3` F2), so a
mapping from one space to another has no automatic basis: the only evidence that ties a roster row
to a person is its email.

The scope-resolver registry does not help. `register_scope_resolver` stores each product's resolver
in a dictionary that nothing reads (`libs/platform-core/src/platform_core/auth.py:297-301`), so the
resolvers registered by `services/customer/app/scope.py`, `services/deal/app/scope.py` and
`services/lead/app/scope.py` are never consulted; every selector passes its own owner column to
`ctx.apply`, which is the real enforcement point.

**What changes, proposed.** Proposed in ADR-0038 (pending D-7, D-8, D-9): R2.2 makes the scope
subject a per-product choice with the employee link as the default, so no other product changes;
R2.6 adds `membership_id` to the roster and an owner-run, human-reviewed mapping plan; R2.7 flips
CRM's scope onto the membership, with the data applied first. Until those ship, OWN in a CRM-only
tenant is empty.

## 5. Data model

Every business table carries `organization_id`, has `ENABLE` and `FORCE ROW LEVEL SECURITY` with a
`tenant_isolation` policy on the transaction-local tenant GUC, and no table holds a foreign key into
another service's schema: references across services are plain UUIDs (ADR-0000 §10).
`processed_events` is deliberately unpolicied because it has no organization column, and a policy
would silently defeat consumer idempotency. The policies are in
`services/customer/migrations/versions/d17e94b06c3a_enable_row_level_security.py:35-50`,
`services/deal/migrations/versions/e94d7a2c5f18_enable_row_level_security.py:41-86` and
`services/lead/migrations/versions/f2c8a41d9e33_enable_row_level_security.py:35-46`.

**customer** (`services/customer/app/models.py`)

- `owners` (`:65-92`): the rep directory, unique on `(organization_id, email)`. `is_active` goes
  false when a rep leaves; a rep is never deleted, because every row that carries the id must keep
  resolving to a name.
- `companies` (`:95-154`): `status` is `customer`, `prospect`, `lead` or `churned`; money is
  `annual_revenue_minor` with an ISO `currency`. The name is unique among live rows
  (`uq_companies_org_name`, case-sensitive); a case-insensitive duplicate is refused by the
  application under an advisory lock, not by the database (ADR-0017 Known limitations).
- `contacts` (`:157-235`): `email` is required, plaintext and unique among live rows. The phone is
  **sealed** (`phone_enc`), **masked** (`phone`) and **blind-indexed** (`phone_index`).
  `company_id` is a real foreign key inside the schema (`ON DELETE CASCADE`).
  `last_activity_at` is advanced from the pipeline's activity events.

**deal** (`services/deal/app/models.py`)

- `deals` (`:50-96`): `value_minor` (a big integer of minor units) with an ISO `currency`, `stage`
  (a string the service layer constrains; it is not an enum column), `probability` 0 to 100,
  `close_date`, `source` and `last_activity_at`, the staleness clock that `log_activity` advances.
  `company_id` and `primary_contact_id` are plain UUIDs into customer.
- `activities` (`:99-138`): **append-only**. The trigger `trg_activities_append_only` refuses UPDATE
  and DELETE (`services/deal/migrations/versions/e94d7a2c5f18_enable_row_level_security.py:88-107`).
  `activity_type` is `call`, `email`, `meeting`, `note` or `task` (the wire field is `type`);
  `direction` is `inbound` or `outbound` for calls and emails and null otherwise; `deal_id` is an
  in-schema foreign key (`ON DELETE CASCADE`) and may be null; `company_id` and `contact_id` are
  plain nullable UUIDs into customer.

**lead** (`services/lead/app/models.py`)

- `leads` (`:65-157`): the email is sealed, masked and blind-indexed (`email_enc`, `email_masked`,
  `email_index`) and the phone is sealed and masked (`phone_enc`, `phone_masked`); `source` and `status`
  hold the vocabularies of §3; `owner_id` is a plain UUID; `converted_company_id`,
  `converted_contact_id`, `converted_deal_id` and `converted_at` are set once, by `:convert`, and
  never change. `conversion_claim_id`, `conversion_claimed_at` and `conversion_generation` make the
  conversion idempotent. The email is unique among live rows that have one (`uq_leads_org_email_index`).
  `phone_index` is dormant: since R1.2b `create_lead` no longer computes it and the column is mapped
  `deferred`, so no statement the service issues names it; no selector uses it and no index covers
  it. It stays for phone-based duplicate detection (R3.7).
- `assignment_rules` (`:160-187`): one row per organization (`uq_assignment_rules_org`) holding the
  mode (`round_robin` or `manual`), the ordered `owner_ids` and the `cursor`.

`owner_id` on every table above means what §4 says: a roster id or an employee id, never reliably
one or the other.

## 6. Public API

The contract is the three committed OpenAPI documents, `libs/contracts/openapi/customer.json`,
`libs/contracts/openapi/deal.json` and `libs/contracts/openapi/lead.json`. This file lists no
endpoint, because a table here would go stale at the next route. `make openapi` regenerates the
documents and the permission manifests from the code, and `make openapi-check` fails when one is
behind.

- **Prefixes.** Production routes four CRM prefixes: `/api/v1/customers` and `/api/v1/contacts` to
  customer, `/api/v1/deals` to deal and `/api/v1/leads` to lead
  (`infrastructure/edge/routes.prod.json:106-109`). The development table has the same four
  (`services/app-gateway/routes.json:76-79`), and `tests/infra/test_route_table_parity.py` fails if
  the two tables differ. Every public router is mounted
  under `/api/v1` by `create_app` (`libs/platform-core/src/platform_core/app.py:362-363`), and a new
  public path must start with a routed prefix or rows are added to both tables.
- **Entitlement.** Every route except the three `info` routes below requires the `crm` entitlement,
  through `require_product("crm")` declared once in each router module
  (`services/customer/app/api/routes.py:61`, `services/customer/app/api/c1_customer360.py:37`,
  `services/deal/app/api/routes.py:57`, `services/lead/app/api/routes.py:65`) and attached to each
  route, and a permission with its scope (§9).
- **Lists** answer `Page[T]`: `items`, `total`, `page`, `size` and `pages`. `page` is at least 1 and
  `size` is 1 to 200, 25 by default (`libs/platform-core/src/platform_core/pagination.py:23-52`).
- **Actions on one record** use a colon verb: `POST /api/v1/leads/{lead_id}:convert`,
  `POST /api/v1/leads/{lead_id}:reveal` and `POST /api/v1/contacts/{contact_id}/phone:reveal`. A
  static path such as `GET /api/v1/deals/summary` or `/api/v1/deals/activities` is declared before
  the `{id}` route so that it is never read as an id (`services/deal/app/api/routes.py:73-75`).
- **Nouns today.** The company URL is `/customers`, the Customer 360 is
  `GET /api/v1/customers/{contact_id}/360` (`services/customer/app/api/c1_customer360.py:41-46`) and
  the timeline lives at `/api/v1/deals/activities`. Proposed in ADR-0038 (pending D-22):
  `/companies` canonical with `/customers` aliased, the 360 under `/contacts/{id}/360`, and
  `/activities` aliased.
- **The `info` routes.** `GET /api/v1/customer/info`, `/api/v1/deal/info` and `/api/v1/lead/info`
  are in the documents but unreachable through the gateway, because `/api/v1/customer/info` does not
  start with the prefix `/api/v1/customers` (`services/customer/app/api/routes.py:125`,
  `services/deal/app/api/routes.py:66`, `services/lead/app/api/routes.py:123`). They are one
  instance of a scaffold that 32 services carry (32 `ServiceInfo` schemas and 32 `/info` routes): a
  platform item, not the CRM's to remove.

## 7. Internal API

Service-to-service calls carry a short-lived service token addressed to the callee by name. They
reach `/internal/v1/...` routes that are mounted at the root, excluded from the OpenAPI documents
(`include_in_schema=False`) and guarded by `require_service_token`
(`services/customer/app/api/internal.py:69-74`, `services/deal/app/api/internal.py:45-52`). Two
more checks exist on some routes: `_require_caller`, an allow-list of calling services
(`services/customer/app/api/internal.py:95-107`), and `_require_org_claim`, which demands that the
token's `org` claim names the organization in the path (`:110-121`).

**The routes the CRM serves**, all under `/internal/v1/organizations/{organization_id}`:

| Route | Served by | Caller allow-list | Org-claim check | Called by |
|---|---|---|---|---|
| `GET /contacts/{contact_id}/messaging-target` (`services/customer/app/api/internal.py:138`) | customer | none | none | notification (A7) |
| `POST /contacts:resolve` (`services/customer/app/api/internal.py:197`) | customer | none | none | notification (A8) |
| `GET /contacts/{contact_id}` (`services/customer/app/api/internal.py:244`) | customer | `finance` | yes | finance (A6) |
| `POST /leads:convert` (`services/customer/app/api/internal.py:290`) | customer | `lead` | yes | lead (A3) |
| `POST /owners:validate` (`services/customer/app/api/internal.py:351`) | customer | `lead` | yes | lead (A5) |
| `POST /leads:convert` (`services/deal/app/api/internal.py:94`) | deal | `lead` | yes | lead (A4) |

The first two are the only CRM routes with neither check: any validly signed service token reaches a
contact's sealed number through them. Hardening both is registered for R3.1. The organization claim
it needs is already on notification's tokens (R1.2a, registry `J13`), so the check can be turned on
without locking notification out.

**Every place a CRM service, finance or notification calls another service** (found with
`grep "_internal_url\|_service_url" services/{customer,deal,lead,finance,notification}/app`,
leaving out `config.py`; `deal` builds none). `tests/infra/test_internal_client_paths.py` registers
these nine calls, makes each with the real client, sends the recorded request to the real callee app
and decodes the service token it carried.

| # | Caller | Calls | Client code | Verdict |
|---|---|---|---|---|
| A1 | customer's 360 | deal `GET /api/v1/deals`, forwarding the caller's identity | `services/customer/app/c1_360.py:172-176` builds `{deal_internal_url}{DEAL_LIST_PATH}`, where `DEAL_LIST_PATH` is `/api/v1/deals` | was a path defect (it asked for `/deals`, which deal answers 404); fixed in R1.2a, and the 360's own test and the guard now check the path against deal's real route table |
| A2 | customer's 360 | notification `GET /internal/v1/contacts/{contact_id}/messaging-summary` | `services/customer/app/c1_360.py:206-209`; route `services/notification/app/api/c1_internal.py:81-86` | correct |
| A3 | lead | customer `POST …/leads:convert` | `services/lead/app/clients.py:104-105` | correct |
| A4 | lead | deal `POST …/leads:convert` | `services/lead/app/clients.py:184-185` | correct |
| A5 | lead | customer `POST …/owners:validate` | `services/lead/app/clients.py:249-250` | correct |
| A6 | finance | customer `GET …/contacts/{contact_id}` | `services/finance/app/clients.py:66-67` | path correct; **unwired**: finance has no `CUSTOMER_INTERNAL_URL` in compose, so it falls to `http://localhost:8130` (`services/finance/app/config.py:52`); latent, because nothing calls it (`services/operations/app/orders/services.py:871-892` returns `None`) |
| A7 | notification | customer `GET …/contacts/{contact_id}/messaging-target` | `services/notification/app/targets.py:73-76`; its token carries the `org` claim (R1.2a) | correct |
| A8 | notification | customer `POST …/contacts:resolve` | `services/notification/app/contacts.py:74-77`; its token carries the `org` claim (R1.2a) | correct |
| A9 | notification | account `GET …/memberships/{membership_id}` | `services/notification/app/memberships.py:48-51`; its token carries the `org` claim (R1.2a); route `services/account/app/api/internal.py:304-346` | path correct; **unwired** (inference): compose sets no `ACCOUNT_INTERNAL_URL` (it sets `ACCOUNT_SERVICE_URL`, `infrastructure/edge/compose.prod.yml:103`) and the default is `http://localhost:8111` (`services/notification/app/config.py:91`), and `membership_is_active` answers `False` on a connection error (`services/notification/app/memberships.py:68-70`), so in production an Engage conversation assignment would be refused; wiring the URL is outside this programme (registry `L3`) |

Seventeen further files in nine other services build internal URLs from the same settings; nobody
has audited them. They are `UNAUDITED_BASELINE` in `tests/infra/test_internal_client_paths.py`: the
list may only shrink, by registering a file's calls, and the next client in any service has to be
registered or named there on purpose.

## 8. Events

Fifteen event types are published (`services/customer/app/services.py:119-124`,
`services/deal/app/services.py:43-47`, `services/lead/app/services.py:71-77`).
All fifteen have a committed schema, `libs/contracts/events/<type>/v1.schema.json`; five of them
are **ahead of the producer**: their own `$comment` names the required fields the producer does not
emit yet. In the table, *Emitted today* is the payload the producer builds, *State* compares it
with the schema's required list (`conforms`, or `ahead` with the missing fields), and *Subscribable*
says whether notification's rule engine lists the key.

| Event | Producer | Emitted today | Schema | State | Consumers today | Subscribable |
|---|---|---|---|---|---|---|
| `crm.book.company.created` | customer | `organization_id, company_id, name, status, owner_id` (`services/customer/app/services.py:482-488`) | yes | ahead: lacks `created_at` | notification | yes |
| `crm.book.company.updated` | customer | `organization_id, company_id, changed` (`services/customer/app/services.py:552-556`) | yes | ahead: lacks `name, status, owner_id` | none | no |
| `crm.book.company.deleted` | customer | `organization_id, company_id` (`services/customer/app/services.py:603`) | yes | conforms | none | no |
| `crm.book.contact.created` | customer | `organization_id, contact_id, company_id, full_name, status` (`services/customer/app/services.py:695-701`) | yes | conforms; tested against its producer | notification | yes |
| `crm.book.contact.updated` | customer | `organization_id, contact_id, changed` (`services/customer/app/services.py:763-767`) | yes | conforms; tested against its producer | none | no |
| `crm.book.contact.deleted` | customer | `organization_id, contact_id` (`services/customer/app/services.py:806`) | yes | conforms; tested against its producer | none | no |
| `crm.pipeline.deal.created` | deal | `organization_id, deal_id, name, stage, stage_outcome, value_minor, currency, owner_id, company_id, close_date, probability, closed_at, created_at` (`_created_payload`, `services/deal/app/services.py`) | yes | conforms; tested against its producer in every pipeline state (`services/deal/tests/test_deal_events_contract.py`) | notification | yes |
| `crm.pipeline.deal.updated` | deal | `organization_id, deal_id, changed, name, stage, stage_outcome, value_minor, currency, owner_id, company_id, close_date, probability, closed_at` (`_updated_payload`, `services/deal/app/services.py`) | yes | conforms; tested against its producer in every pipeline state (`services/deal/tests/test_deal_events_contract.py`) | none | no |
| `crm.pipeline.deal.deleted` | deal | `organization_id, deal_id` (`services/deal/app/services.py:291`) | yes | conforms | none | no |
| `crm.pipeline.deal.stage_changed` | deal | `organization_id, deal_id, from, to, from_outcome, to_outcome, to_label, name, value_minor, currency, owner_id, probability, closed_at` (`_stage_changed_payload`, `services/deal/app/services.py`) | yes | conforms; tested against its producer in every pipeline state (`services/deal/tests/test_deal_events_contract.py`) | notification | yes |
| `crm.pipeline.activity.logged` | deal | `organization_id, activity_id, type, deal_id, contact_id, occurred_at` (`services/deal/app/services.py:435-440`) | yes | conforms; tested against its producer | customer, notification | yes |
| `crm.lead.created` | lead | `organization_id, lead_id, source, owner_id` (`services/lead/app/services.py:539-542`) | yes | ahead: lacks `status` | notification | yes |
| `crm.lead.updated` | lead | `organization_id, lead_id, changed` (`services/lead/app/services.py:648-650`) | yes | ahead: lacks `status, source, owner_id, converted_at` | notification | yes |
| `crm.lead.converted` | lead | `organization_id, lead_id, company_id, contact_id, deal_id` (`services/lead/app/services.py:1251-1255`) | yes | ahead: lacks `owner_id, converted_at` | notification | yes |
| `crm.lead.conversion_aborted` | lead | `organization_id, lead_id, reason, generation, claim_id, company_id, contact_id, deal_id`; `reason` is `status_changed`, `owner_changed` or `record_deleted` (`services/lead/app/services.py`, `_stage_conversion_aborted`) | yes | conforms; tested against its producer (`services/lead/tests/test_lead_lifecycle_races.py`, `TestAnAbortedConversionLeavesATrail`; `services/lead/tests/test_convert.py`, `TestGenerationsAndDealNames`) | notification | yes |

- **Publishing.** A producer stages the event in its `outbox` in the same transaction as the change,
  and the relay publishes it after commit. An event with no bound queue is silently dropped while its
  outbox row is marked published (`libs/platform-core/src/platform_core/events.py:250-262,400`), and
  a queue exists only once its consumer has run (`:453-471`). Every `crm.lead.*` event emitted before
  R1.2b's deploy was therefore dropped by the broker, and none is replayed; notification now binds
  the four lead keys when it starts, so a lead event reaches its queue from that deploy on.
- **Notification.** It binds every key of `SUBSCRIBABLE_EVENTS` unconditionally when it starts
  (`services/notification/app/consumers.py:324-325`), and a rule may name only a key of that list
  (`services/notification/app/domain.py:371`). The CRM group is nine keys, the four lead keys among
  them (`services/notification/app/domain.py:146-157`).
- **Contracts.** Schemas are additive within `v1` and use `additionalProperties: false`;
  `platform_core.events.validate_event_schema` is the validator
  (`libs/platform-core/src/platform_core/events.py:203-224`). Customer, deal and lead each build
  their events through the real writers and check the payload against its schema
  (`services/<service>/tests/test_event_contracts.py`, the five ahead-of-producer schemas pinned
  there), and the static contracts test requires a schema for every `crm.*` event a service declares
  and every `crm.*` key notification lists (`libs/contracts/tests/test_event_schemas.py`).
- **Variables.** In a checkout, Engage's automation builder lists a trigger's variables from the
  committed schema's `properties` (`services/notification/app/a1_selectors.py:260-284`), so an
  "ahead" schema offers variables the producer does not send. On the box it lists none for any
  trigger: `libs/contracts` is excluded from every image (`.dockerignore:29-32`), so
  `find_contracts_dir` finds nothing (`services/notification/app/a1_selectors.py:208-223`). The
  committed schemas document and test the contract; they do not feed the production builder.
  Rendering is strict: a placeholder that resolves to nothing raises, the rule logs
  `notification.rule.render_failed` and stages nothing (`services/notification/app/domain.py:282-307`,
  `services/notification/app/services.py:841-850`).
- **Frozen.** The event types and, on `crm.book.contact.created`, the legacy `full_name`: a tenant
  automation may already render it, so it is documented as frozen and a new consumer reads
  `contact_id` and asks customer for anything else.
- **Stages.** `stage` on `created` and `updated`, and `from` and `to` on `stage_changed`, are stage **keys**: an immutable slug of the
  organization's pipeline that never changes meaning. What a key means is its **outcome** (`open`, `won` or `lost`), carried as
  `stage_outcome`, `from_outcome` and `to_outcome`; a consumer tests the outcome and never compares a key with a literal. `to_label` is
  the target stage's label for display only.

## 9. Permissions and roles

Seventeen permission codes, declared in each service's catalogue
(`services/customer/app/catalogue.py:104-143`, `services/deal/app/catalogue.py:104-126`,
`services/lead/app/catalogue.py:113-147`) and merged into the one committed manifest
`libs/contracts/permissions/crm.json`. The default scope is the scope the dashboard role editor
pre-selects when an administrator ticks the permission
(`frontend/opsgrids-dashboard/src/routes/admin/Roles.tsx:376,384`); role templates name their scopes
explicitly. The maximum is a ladder ceiling, not an allowed set, so a grant below it is accepted
(ADR-0000 §5, note added 2026-09-17; `services/account/app/services.py:1272`).

| Code | Slice | Service | Default scope | Maximum scope | Dangerous | Description |
|---|---|---|---|---|---|---|
| `crm.book.company.read` | book | customer | OWN | ORG | no | See companies and the rep directory |
| `crm.book.company.write` | book | customer | OWN | ORG | no | Create and update companies |
| `crm.book.company.delete` | book | customer | OWN | ORG | yes | Delete a company |
| `crm.book.contact.read` | book | customer | OWN | ORG | no | See contacts |
| `crm.book.contact.write` | book | customer | OWN | ORG | no | Create and update contacts |
| `crm.book.contact.delete` | book | customer | OWN | ORG | yes | Delete a contact |
| `crm.book.contact.read_sensitive` | book | customer | OWN | ORG | yes | Reveal a contact's real phone number, audited |
| `crm.book.owner.write` | book | customer | ORG | ORG | no | Add a rep to the directory |
| `crm.pipeline.deal.read` | pipeline | deal | OWN | ORG | no | See deals and the pipeline summary |
| `crm.pipeline.deal.write` | pipeline | deal | OWN | ORG | no | Create deals and move them through stages |
| `crm.pipeline.deal.delete` | pipeline | deal | OWN | ORG | yes | Delete a deal |
| `crm.pipeline.activity.read` | pipeline | deal | OWN | ORG | no | See the activity timeline |
| `crm.pipeline.activity.write` | pipeline | deal | OWN | ORG | no | Log calls, emails, meetings and notes |
| `crm.lead.lead.read` | lead | lead | OWN | ORG | no | See leads |
| `crm.lead.lead.write` | lead | lead | OWN | ORG | no | Create and update leads |
| `crm.lead.lead.convert` | lead | lead | OWN | ORG | no | Convert a qualified lead into a company and deal |
| `crm.lead.lead.read_sensitive` | lead | lead | OWN | ORG | yes | Reveal a lead's real phone number or email, audited |

- `crm.book.owner.write` is floored at ORG on its route: an OWN grant is refused outright
  (`services/customer/app/api/routes.py:68`). Configuring the lead assignment rule is refused unless
  the caller's scope is ORG (`services/lead/app/services.py:181-182`).
- The two `read_sensitive` codes open a sealed value only on a stepped-up request: the route demands
  both the `otp` method and a step-up marker (`services/customer/app/api/routes.py:80`,
  `services/lead/app/api/routes.py:78`), writes an `entity.sensitive_read` audit row naming the field
  and never its value, and is bounded by a per-user reveal budget that customer and lead share
  (`services/customer/app/config.py:34-43`). `is_dangerous` is metadata, not a control: what
  constrains a reveal is the step-up, the scope applied to the one record, and the audit row
  (ADR-0000 §5, note added 2026-09-17).
- Every code is frozen: codes are customer data once tenants author roles, there is no rename
  mechanism, and the removal tool ADR-0007 specifies (`tools/revoke_permission.py`, open-questions
  register row 14) does not exist. A new code reaches live tenants admin-first through the
  reconcile of ADR-0007 (`make edge-reconcile`, dry run first), and only then does a UI show its
  control.

**Role templates.** Each service declares its own slice, and an administrator template is built from
that slice alone, never from every permission that exists (ADR-0000 §5);
`validate()` refuses a template that holds another product's code
(`services/customer/app/catalogue.py:182-201`).

| Code | Name | Service | Admin | Default | Grants | Planned mapping (proposed in ADR-0038, pending D-20) |
|---|---|---|---|---|---|---|
| `CRM_BOOK_ADMIN` | CRM administrator (book) | customer | yes | no | all eight `crm.book.*` codes at ORG (`services/customer/app/catalogue.py:156`) | `CRM_ADMIN` |
| `CRM_SALES_REP` | Sales representative | customer | no | yes | company.read ORG, company.write OWN, contact.read ORG, contact.write OWN, contact.read_sensitive OWN | `CRM_REP` |
| `CRM_PIPELINE_ADMIN` | CRM administrator (pipeline) | deal | yes | no | all five `crm.pipeline.*` codes at ORG (`services/deal/app/catalogue.py:139`) | `CRM_ADMIN` |
| `CRM_DEAL_REP` | Sales representative | deal | no | yes | deal.read ORG, deal.write OWN, activity.read ORG, activity.write OWN | `CRM_REP` |
| `CRM_LEADS_ADMIN` | CRM administrator (leads) | lead | yes | no | all four `crm.lead.lead.*` codes at ORG (`services/lead/app/catalogue.py:160`) | `CRM_ADMIN` |
| `CRM_LEAD_REP` | Sales representative | lead | no | yes | lead.read ORG, lead.write OWN, lead.convert OWN, lead.read_sensitive OWN | `CRM_REP` |

The three representative templates read the shared book at ORG and write, convert and reveal at OWN,
which is why OWN matters (§4). The three administrator templates are `is_admin`, so a new
activation hands each to the organization's owners.

**Proposed in ADR-0038 (pending D-20)**, the legacy templates stay and four templates are added by
same-code merging across the three catalogues:

- `CRM_ADMIN`: everything in the catalogue at ORG, and the only template that configures anything.
- `CRM_MANAGER`: works the shared book and edits what their team owns (TEAM scope is proposed
  separately, pending D-24).
- `CRM_REP`: works the shared book and edits what they own; the default for a new member.
- `CRM_VIEWER`: read-only, with no writes, no reveal and no money.

The six legacy role codes and the 17 permission codes stay frozen. The three legacy representative
templates would stop being the default for new activations only; a live tenant's existing roles are
not rewritten, because a reconcile creates missing roles and widens grants and never narrows
(ADR-0007).

## 10. Runtime and operations

- **Containers.** The `crm` compose profile runs three containers, `customer`, `deal` and `lead`
  (`infrastructure/edge/compose.prod.yml:1424-1484`). `customer-worker` exists only behind the
  `split-workers` profile, as the rollback for the consumer fold
  (`infrastructure/edge/compose.prod.yml:1449-1454`).
- **The consumer fold.** `RUN_CONSUMERS_IN_API` defaults to true in compose
  (`infrastructure/edge/compose.prod.yml:94`) and to false in the service
  (`services/customer/app/config.py:23`), so on the box customer's one consumer runs as a supervised
  task inside its API process. `deal` and `lead` have no such setting and ignore the variable. The
  rollback is `RUN_CONSUMERS_IN_API=false` with `split-workers` added to `EDGE_PROFILES`
  (`infrastructure/edge/RUNBOOK.md:3020-3021`).
- **Outbox relays.** The shared `edge-relay` drains the CRM outboxes: its `EDGE_RELAY_SCHEMAS`
  default has 29 schemas, `customer`, `deal` and `lead` among them
  (`infrastructure/edge/compose.prod.yml:791`). ops-monitor reuses the same list for its outbox-lag
  check and, since R1.2b, its default is the same 29 names, `lead` included
  (`infrastructure/edge/compose.prod.yml:851`); `tests/infra/test_ops_monitor_compose_env.py` fails
  if the two differ.
- **Dead-letter queues.** For the `crm` profile ops-monitor watches `customer.events.dlq` and no
  other CRM queue (`libs/platform-core/src/platform_core/ops_monitor/config.py:102`); the comment
  above that table, which says `crm` runs no consumer, is stale (`:96-100`). A queue is declared only
  when its consumer first runs, and `tests/infra/test_ops_monitor_dlq_queue_sync.py` fails when a
  `./bin/worker` service has no queue in the table or the reverse (`:83-129`), so each queue is added
  in the release that adds its consumer. The schedule the registry proposes (registry `F5`), none of
  which is built:

  | Queue | Profile key | Added by | Because |
  |---|---|---|---|
  | `deal.events` | `crm` | R2.4 | deal's consumer fold and its rollback container |
  | `lead.events` | `crm` | R3.8 | lead's consumer fold |
  | `scheduler.events` | moves from `finance` to the core list | R3.6 | the scheduler serves CRM follow-ups |
  | `operations.events` | new profile key `inventory` | R4.3 | operations already consumes inside its API but is unwatched; the entry arrives with an `operations-worker` rollback container |
  | `support.events`, `marketing.events`, `storefront.events` | new profile keys | R4.4, R4.6, R4.7 | their consumer folds |
  | `approval.events` | moves from `hrms` to the core list | R4.5 | approval moves to the core profile |

- **Memory.** The numbers are the repo's own arithmetic:
  `python3 infrastructure/edge/lib/memory_budget_totals.py infrastructure/edge/memory-budget.json --profiles <csv>`.
  The core alone is 1772 MB (`--profiles ''`) and core plus the `crm` profile is 2067 MB
  (`--profiles crm`), so the three CRM containers cost 295 MB. The ten sellable profiles
  (`storefront,inventory,hrms,crm,pulseboard,projects,support,marketing,finance,itsm`) total 3747 MB,
  97.0% of the 3862 MB reference, and 3944 MB, 102.1%, with `admin` added. The box's accepted ceiling
  is the owner's setting, `EDGE_BUDGET_ACCEPTED_MB`, which is 3944 MB by the owner's note; the
  repository cannot verify that, and `make edge-preflight` prints the verdict. The "94.4%" figure in
  ADR-0017 predates the re-pricing.
- **Folding `lead` into `customer` (D-11).** The costing is in
  [ADR-0038, "Costing of D-11"](adr/0038-crm-programme-decisions.md#costing-of-d-11): removing the
  `lead` container would take the ten profiles plus `admin` from 3944 MB to 3846 MB, but it
  supersedes ADR-0017 decision 1, moves live tenant data between schemas and cannot be rolled back
  by code alone. The recommendation is to keep three processes for this programme, proposed in
  ADR-0038 (pending D-11); the decision first matters at R2.4, and only if D-13 holds the ceiling.
  The default proposed for D-13 is the other answer: the owner raises `EDGE_BUDGET_ACCEPTED_MB` by
  1 MB for each consumer fold, because `api_with_worker` is 99 MB against 98 for `api` (proposed in
  ADR-0038, pending D-13).
- **The RUNBOOK** (`infrastructure/edge/RUNBOOK.md`): "Deploying" (`:524`), "Roll back a bad deploy"
  (`:2615`), "The memory budget" (`:2941`), "Removing test organisations" (`:2204`; every end-to-end
  and smoke run leaves one behind, and the owner removes them with `tools/purge_test_orgs.py`),
  "Development databases (not the box)" (`:3444`), and the DNS row for `revenue.opsgrids.com`
  (`:103`).
- **Commands the owner runs.** `make deploy HOST=… CONFIRM=… [REF=…]` (`Makefile:569`),
  `make edge-status` (`:600`), `make edge-rollback` (`:578`; the only rollback is the whole ref, for
  every service), `make edge-reconcile PRODUCTS=crm` (`:585`) and `make edge-web APP=crm` (`:615`).
  Merges do not deploy: a deploy is manual and names a ref, and production reads, `.env` edits and
  data operations are the owner's (`P§3` F11).

## 11. Known limitations

| Limitation | Evidence | Fixed by |
|---|---|---|
| OWN scope is empty for a tenant without HRMS: OWN compares `owner_id` with an HR employee id that such a tenant never has | §4; `libs/platform-core/src/platform_core/auth.py:337-341,372-381` | proposed in ADR-0038 (pending D-7, D-8, D-9): R2.2, R2.6, R2.7 |
| An aborted lead conversion leaves an orphaned company, contact and deal; nothing consumes `crm.lead.conversion_aborted`, and the generation bump has no event for a deleted record | ADR-0017 Known limitations; `services/lead/app/services.py:1122-1129,1223-1277` | proposed in ADR-0038 (pending D-1, D-2, D-3, D-4): R2.1, R2.4 |
| `customer.contacts.email` is plaintext (a lead's email is sealed, so conversion narrows the protection) | `services/customer/app/models.py:171`; ADR-0017 Known limitations | no release scheduled; optional sealing proposed as R2.9 Part C (pending D-26) |
| A lead with a phone and no email converts to a company with no contact: the person and the number are dropped | `services/customer/app/services.py:1182-1185`, `services/lead/app/clients.py:103` | proposed in ADR-0038 (pending D-14): R3.1 |
| Deal summaries add `value_minor` across currencies, though a deal's currency is free | `services/deal/app/domain.py:67-74,90-92,101-103`, `services/deal/app/schemas.py:68` | planned: R3.2 (registry `C9`: per-currency splitting, additive) |
| Five committed event schemas are ahead of their producers (§8) | the `$comment` of each schema | planned, each release waiting on the open decisions ADR-0038 lists for it: R3.7 for lead (pending D-27, needed before R3.7b), R4.1 for company (pending D-14, D-27, D-33, D-35) |
| Fixed in #123: the page-only CSV export on Companies used to quote a cell without neutralising a first character of `=`, `+`, `-` or `@`, so a spreadsheet could read a company name as a formula | `frontend/product-crm/src/lib/csv.ts:7` now uses `csvCell` (`frontend/opsgrids-client/src/csv.ts:9-12`), which prefixes `'` to such a cell | fixed; R3.4 (delete the export, pending D-23) no longer has to deploy before R3.7 |

## 12. How to change this

Change the code and this page in the same commit, and run the check that names the thing you changed.

- **An event.** Change the payload and its committed schema, `libs/contracts/events/<type>/v1.schema.json`,
  in one commit, and prove it through the real write path with
  `platform_core.events.validate_event_schema`; `make contracts-check` runs the contracts tests.
  Update the row in §8. A new subscribable key is a one-line edit to `SUBSCRIBABLE_EVENTS` in
  `services/notification/app/domain.py`, in the same commit as its schema. The five
  ahead-of-producer schemas are pinned (`PINNED_AHEAD_OF_PRODUCER` in
  `libs/contracts/tests/test_event_schemas.py` and each service's `AHEAD_OF_PRODUCER` in its
  `tests/test_event_contracts.py`): the release that closes one deletes its pin rows and the
  schema's `$comment` in the same commit.
- **A permission or a role.** Add the `PermissionSpec` to the owning service's `catalogue.py`, run
  `make openapi` (it regenerates `libs/contracts/permissions/crm.json`), deploy the enforcing
  service, then reconcile with `make edge-reconcile PRODUCTS=crm` (the dry run first, then
  `APPLY=1`), admin roles first and the UI last (ADR-0007). Update §9.
- **An internal client.** Register its calls in `tests/infra/test_internal_client_paths.py` in the
  same commit and extend the table in §7. The guard fails when a service file builds an internal URL
  that no registered call or baseline entry names, sends each registered request to the real callee
  app, and decodes the token it carried: put the `org` claim on every token for a path under
  `/internal/v1/organizations/{org}/`.
- **A route prefix or a port.** Add the row to both `services/app-gateway/routes.json` and
  `infrastructure/edge/routes.prod.json`, and update §2 and §6.
- **A decision.** The owner edits the State cell of its row in ADR-0038. The same commit replaces
  the `(pending D-n)` clause in every note that cites it; a clause that cites several decisions
  loses only the answered id. For D-9, a clause that reads `pending D-9` and one that reads
  `pending D-7, D-8, D-9` are both found by
  `grep -rnE 'pending[^)]*D-9([^0-9]|$)' docs services --include='*.md' --exclude-dir=superpowers`,
  which also reaches the service READMEs and leaves out `docs/superpowers`, whose specs and plans are
  the design record and not notes.
- **This page.** `tests/infra/test_crm_doc_matches_contracts.py` fails when an event type, a
  permission code, a role code or a gateway port in §2, §8 or §9 differs from `libs/contracts` and
  `services/app-gateway/routes.json`.
- **The checks.** Tests and mypy run per service, because `import app` is ambiguous across services:
  `cd services/customer && uv run pytest tests -q -p no:cacheprovider && uv run mypy app`, and the
  same for `deal` and `lead`. `make check` is lint, typecheck and test; `make gate` is the whole
  gate CI runs, and with Actions billing blocked it is the only full verification.
- **Where the programme lives.** Its specs are under `docs/superpowers/specs/` (their names start
  `2026-10-07-crm-`) and its plans under `docs/superpowers/plans/`.
