> **Superseded in part by [ADR-0017](../adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md).**
> `lead` is real now. Cards A1–A3 and A5 held up; card A4 did not — there is no `salescrm`
> product. `lead` joins the existing `crm` product alongside `customer` and `deal`. Read the ADR
> before touching permissions here.

# Dev A — the lead service

**You own `services/lead/` and nothing else.**

Five cards, in this order. Each is roughly a day. Do not start a card until the previous one
is merged.

## What you are building, in plain language

OpsGrids sells several products. `lead` is the third service of the CRM product, **`crm`**
("OpsGrids Revenue"), beside `customer` (the account book) and `deal` (the pipeline). When these
cards were written, the gateway already routed `/api/v1/leads` to a service and the database
already had a login role called `opsgrids_lead`, but the service itself was an empty shell that the
code generator produced and nobody filled in. The five cards fill it in; the service exists now,
and card A4 below is rewritten to describe what was built.

**Your reference for every single card is `services/standup/`.** It is the smallest complete
service in this repository — one table, two endpoints — and it is exactly the shape yours must
end up in. Open it first, every time. You are not designing anything. You are copying a
working service and changing the names.

Read these once before card A1:

- `CLAUDE.md` — the "Rules that are not style preferences" section and the "Service anatomy" table
- `docs/onboarding/never-do.md`
- `docs/onboarding/git-workflow.md`

---

# CARD A1 — the table

**BRANCH** `feat/a1-lead-model`

**GOAL** Give the lead service its one database table.

### REFERENCE

`services/standup/app/models.py` — read the whole file including the comments. Lines 40–66 are
a complete table on this platform.

### FILES YOU WILL TOUCH

```
services/lead/app/models.py
services/lead/migrations/versions/<new file>    ← created by a command; you do not write it
```

### THE CONTRACT

Class `Lead`, declared exactly as `class Lead(Base, TenantBase):`, `__tablename__ = "leads"`.

`TenantBase` already gives you `id`, `organization_id`, `created_at`, `updated_at` and
`deleted_at`. **Do not declare any of those.** Write only these columns, in this order:

| Column | Type | Null? |
|---|---|---|
| `full_name` | `String(200)` | not null |
| `company` | `String(200)` | nullable |
| `email` | `String(320)` | nullable |
| `phone` | `String(32)` | nullable |
| `source` | `String(40)` | not null |
| `status` | `String(40)` | not null |
| `owner_id` | `PGUUID(as_uuid=True)` | nullable |

`owner_id` is a plain UUID column, **not** a foreign key. Cross-schema foreign keys are
forbidden (`CLAUDE.md`).

Table arguments, copied in shape from `StandupEntry.__table_args__`:

```python
__table_args__ = (
    UniqueConstraint("organization_id", "email", name="uq_leads_org_email"),
    Index("ix_leads_org_status", "organization_id", "status"),
    Index("ix_leads_org_owner", "organization_id", "owner_id"),
)
```

Both indexes **must** start with `organization_id`. That is not stylistic — every query is
filtered by tenant first, and an index that does not lead with it will not be used.

### STEPS

1. Open `services/standup/app/models.py` and read it.
2. Open `services/lead/app/models.py`. You will see three generated classes — `OutboxEvent`,
   `ProcessedEvent`, `AuditLog`. Leave them exactly as they are.
3. Add the `Lead` class **above** `OutboxEvent`, in the same position `StandupEntry` occupies
   in the reference file.
4. Copy the imports you need from the top of the standup file. Do not add an import for
   anything you did not use.
5. Add a short docstring saying what a lead is, in the style of the reference.
6. From the repository root: `make makemigration SERVICE=lead M="lead domain tables"`
7. Open the file this created under `services/lead/migrations/versions/`. **Read it.** It
   should create one table and its indexes. If it tries to *drop* anything, stop and comment.
8. Add `# pragma: allowlist secret` to the end of its `revision` and `down_revision` lines.

### DONE WHEN

```bash
cd services/lead && uv run alembic upgrade head
cd services/lead && uv run alembic check         # silence means model and migration agree
cd services/lead && uv run mypy app
cd services/lead && uv run pytest tests -q       # the 3 generated tests still pass
uv run ruff check services/lead && uv run ruff format --check services/lead
```

### DO NOT TOUCH

`tools/dev-bootstrap/init-db.sql` — the `opsgrids_lead` role is already there. Any other
service. Anything under `libs/`.

### IF YOU GET STUCK

`alembic check` failing means your model and your migration disagree. Delete the migration
file, fix the model, and generate it again. **Never hand-edit a migration to make the check
pass.**

---

# CARD A2 — row-level security

**BRANCH** `feat/a2-lead-rls`

**GOAL** Make the database itself refuse to return one customer's leads to another customer.

### REFERENCE

- `services/standup/migrations/versions/a1d94e57c380_enable_row_level_security.py` — the whole file
- `services/standup/app/config.py` — the three role lines

### FILES YOU WILL TOUCH

```
services/lead/app/config.py
services/lead/migrations/versions/<new file>
```

### THE CONTRACT

In `app/config.py`, add these two lines to the `Settings` class, keeping the comment style of
the reference:

```python
db_app_role: str | None = "opsgrids_lead"
db_admin_role: str | None = "opsgrids_admin"
```

`db_outbox_role` is already there. Do not change `service_name` or `db_schema`.

The migration turns row-level security on for these three tables:

```python
TENANT_TABLES = ("leads", "audit_logs", "outbox")
```

`processed_events` is deliberately **not** in that list — check the reference file's comment
for why, and keep that comment.

### STEPS

1. Read the reference migration end to end. It is about 86 lines and you are copying nearly
   all of it.
2. Edit `services/lead/app/config.py` first.
3. Create the migration by hand this time — `alembic revision -m "enable row level security"`
   from inside `services/lead` — because this one contains raw SQL that autogenerate cannot
   produce.
4. Copy the reference's `_enable()` helper and its `upgrade()` / `downgrade()` bodies,
   changing only the schema name and `TENANT_TABLES`.
5. Add the `# pragma: allowlist secret` markers as in A1.

### DONE WHEN

```bash
cd services/lead && uv run alembic upgrade head
cd services/lead && uv run alembic downgrade base
cd services/lead && uv run alembic upgrade head
cd services/lead && uv run pytest tests -q
uv run ruff check services/lead && uv run ruff format --check services/lead
```

That up-down-up sequence is the point of this card: it proves your `downgrade()` actually
undoes what your `upgrade()` did. CI runs it on every service.

### DO NOT TOUCH

`init-db.sql`. Any migration you did not create.

---

# CARD A3 — the endpoints

**BRANCH** `feat/a3-lead-endpoints`

**GOAL** Let a signed-in user list their organization's leads and open one.

### REFERENCE

| For | Read |
|---|---|
| `app/scope.py` | `services/standup/app/scope.py` — the whole 40-line file |
| `app/schemas.py` | `services/standup/app/schemas.py` lines 22–43 |
| `app/selectors.py` | `services/standup/app/selectors.py` lines 29–58 and 72–80 |
| `app/api/routes.py` | `services/standup/app/api/routes.py` lines 34–77 |
| the detail-route re-check | `services/deal/app/api/routes.py` lines 143–147 |

### FILES YOU WILL TOUCH

```
services/lead/app/scope.py        ← new file
services/lead/app/schemas.py
services/lead/app/selectors.py
services/lead/app/api/routes.py
```

### THE CONTRACT

**`app/scope.py`** — `PRODUCT = "salescrm"`. The class is `LeadScopeResolver`. `owner_column()`
returns `Lead.owner_id`; `team_column()` and `location_column()` both return `None`, for the
same reason standup's do — those rungs have no meaning for a lead list, and a caller granted
them is refused rather than silently widened.

**`app/schemas.py`** — add `LeadOut` with exactly these fields:

```
id: UUID · full_name: str · company: str | None · email: str | None
phone: str | None · source: str · status: str · owner_id: UUID | None
created_at: datetime
```

**`app/selectors.py`** — two functions, mirroring the reference:

- `leads_stmt(ctx, *, status: str | None = None) -> Select` — filters on
  `Lead.organization_id == ctx.organization_id` **and** `Lead.deleted_at.is_(None)`, then calls
  `ctx.apply(stmt, owner_col=Lead.owner_id)`, then the optional status filter, then
  `.order_by(Lead.created_at.desc())`.
- `get_lead(session, org_id, lead_id)` — explicit `organization_id ==` and `deleted_at.is_(None)`.
- `lead_out(lead) -> LeadOut` — the projection.

**`app/api/routes.py`** — two endpoints:

| Method | Path | Permission | Returns |
|---|---|---|---|
| `GET` | `/leads` | `salescrm.leads.lead.read` | `Page[LeadOut]`, via `paginate` |
| `GET` | `/leads/{lead_id}` | `salescrm.leads.lead.read` | `LeadOut` |

The detail route **must** end with the three-line object-level re-check from the deal
reference: fetch, `raise NotFoundProblem(...)` if it is `None`, then
`ctx.assert_can_touch(owner_id=lead.owner_id)`. A list filter is not enough on a detail route.

Declare `/leads` **before** `/leads/{lead_id}` in the file — routes match in declaration order.

### STEPS

1. Read all five reference locations before writing anything.
2. `app/scope.py`, then `app/schemas.py`, then `app/selectors.py`, then `app/api/routes.py`.
   In that order — each one uses the last.
3. Call `scope.register()` in the routes module exactly where standup calls it.

### DONE WHEN

```bash
cd services/lead && uv run mypy app
cd services/lead && PYTHONPATH=. uv run lint-imports --config ../../.importlinter --no-cache
cd services/lead && uv run pytest tests -q
uv run ruff check services/lead && uv run ruff format --check services/lead
```

`lint-imports` is the one that catches the most common mistake on this codebase — a router
importing `models` directly instead of going through `selectors`. If it fails, it prints the
exact import that broke the rule.

### DO NOT TOUCH

`services/app-gateway/routes.json` — `/api/v1/leads` is already routed to port 8135.

---

# CARD A4 — permissions

**BRANCH** `feat/a4-lead-catalogue`

> **This card was rewritten on 2026-10-07 to describe what was built.** It originally specified a
> separate `salescrm` product; [ADR-0017](../adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)
> rejected that, and `lead` joined the existing `crm` product. Cards A3 and A5, which are not
> rewritten, still show the `salescrm.leads.lead.read` code and `PRODUCT = "salescrm"` of the
> original plan in their endpoint, scope and test tables: read them as the `crm.lead.lead.*` codes
> and `PRODUCT = "crm"` of this card.

**GOAL** Declare what a person is allowed to do with leads, as the third slice of the `crm`
product's catalogue. No new product manifest is created: `lead` contributes to the one
`crm` manifest, beside `customer` (`crm.book.*`) and `deal` (`crm.pipeline.*`).

### REFERENCE

`services/lead/app/catalogue.py` is the result, and its docstring says it mirrors the catalogues of
`customer` and `deal` (`services/customer/app/catalogue.py`, `services/deal/app/catalogue.py`). The
guard, `validate()` and the `ProductRegistration` dataclass are **copied verbatim** — the same
block appears in every product service on purpose. Only the middle section changes.

### FILES YOU WILL TOUCH

```
services/lead/app/catalogue.py            ← new file
libs/contracts/openapi/lead.json          ← regenerated by a command, then committed
libs/contracts/permissions/crm.json       ← regenerated by that same command (lead's slice joins it), then committed
```

Those last two are the one exception to "never touch `libs/contracts/`" — you are not editing
them, you are running `make openapi` and committing what it produced.

### THE CONTRACT

`PRODUCT = "crm"`.

Four permissions, all in module `lead`, resource `lead`, with a default scope of OWN and a maximum
of ORG:

| Code | What it allows | Dangerous |
|---|---|---|
| `crm.lead.lead.read` | See leads | no |
| `crm.lead.lead.write` | Create and update leads | no |
| `crm.lead.lead.convert` | Convert a qualified lead into a company and deal | no |
| `crm.lead.lead.read_sensitive` | Reveal a lead's real phone number or email, audited | yes |

Two role templates, built from this slice alone and never from "all permissions":

- `CRM_LEADS_ADMIN` ("CRM administrator (leads)"): every `crm.lead.*` code at ORG.
- `CRM_LEAD_REP` ("Sales representative", the default): `read` at ORG, and `write`, `convert` and
  `read_sensitive` at OWN.

The older rep templates, `CRM_SALES_REP` (customer) and `CRM_DEAL_REP` (deal), hold no `crm.lead.*`
grant. The exact declarations are `services/lead/app/catalogue.py:150-179`.

### STEPS

1. Copy `services/deal/app/catalogue.py` to `services/lead/app/catalogue.py`.
2. Change the module docstring to describe leads, and replace the catalogue constant, the
   permissions and the role templates with the contract above.
3. Leave the `validate()` function and the `ProductRegistration` dataclass exactly as they are.
4. From the repository root: `make openapi`
5. `git status` — you should see `libs/contracts/openapi/lead.json` and
   `libs/contracts/permissions/crm.json` changed (the `lead` slice added to the one manifest). No
   other manifest is created: if one appears, your `PRODUCT` is wrong; stop and comment.

### DONE WHEN

```bash
cd services/lead && uv run mypy app
cd services/lead && uv run pytest tests -q
make openapi && make openapi-check
uv run ruff check services/lead && uv run ruff format --check services/lead
```

### DO NOT TOUCH

Any other service's `catalogue.py`. Any manifest other than the two `make openapi` regenerates.

---

# CARD A5 — the tests

**BRANCH** `feat/a5-lead-tests`

**GOAL** Prove the endpoints refuse what they should refuse. This is the card that makes the
other four trustworthy.

### REFERENCE

`services/standup/tests/` — every file in it.

| Your file | Copy from | How much changes |
|---|---|---|
| `tests/helpers.py` | `standup/tests/helpers.py` (52 lines) | the product name in `entitlements` |
| `tests/conftest.py` | `standup/tests/conftest.py` (143 lines) | `TENANT_TABLES`, and the `data` fixture body |
| `tests/test_catalogue.py` | `standup/tests/test_catalogue.py` (51 lines) | near-verbatim — names only |
| `tests/test_authz.py` | `standup/tests/test_authz.py` (260 lines) | permission strings and URLs |
| `tests/test_cross_tenant_isolation.py` | `standup/tests/test_cross_tenant_isolation.py` (172 lines) | table and schema names |

### FILES YOU WILL TOUCH

Only files under `services/lead/tests/`.

### THE CONTRACT

`test_authz.py` must contain **all five** of these, for each endpoint. This list is not
negotiable — `CLAUDE.md` requires a negative-authz test on every endpoint:

| Test | Expects |
|---|---|
| called with no principal at all | **401** |
| called by someone without `salescrm.leads.lead.read` | **403**, `content-type` starting `application/problem+json` |
| called by someone with no `salescrm` entitlement | **402** |
| a reader trying to write | **403** |
| an `OWN`-scope caller reading a lead owned by someone else | **403** on the detail route |

Plus the positive controls the reference shows, so a passing suite means the endpoint works
*and* refuses.

`conftest.py`'s `data` fixture must seed through the **real** code path — call your selector
and service functions, never raw SQL inserts. The reference does this at lines 95–130.

### STEPS

1. Copy all five files across first, unchanged. Run the suite; it will fail loudly.
2. Fix them one file at a time, in the table's order. `helpers.py` first, `test_authz.py` last.
3. Run the suite after each file.

### DONE WHEN

```bash
cd services/lead && uv run pytest tests -q          # all green, nothing skipped
cd services/lead && uv run mypy app
make hygiene                                        # fails on a skipped test, a print(), a bare noqa
uv run ruff check services/lead && uv run ruff format --check services/lead
```

`make hygiene` will fail if you left a `@pytest.mark.skip` anywhere. A skipped test is a test
that does not run, and a green build that does not say so.

### DO NOT TOUCH

Any test outside `services/lead/tests/`.

---

## After A5

The lead service is done, as the third process of the `crm` product. `services/activity` was
deleted rather than built ([ADR-0017](../adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md)),
so this lane has no next card.
