> **Superseded by [ADR-0017](../adr/0017-crm-lead-service-and-pipeline-scaffold-removal.md).**
> `services/pipeline` has been deleted, not built. `deal` already owns `crm.pipeline.*` — deals
> and the activity timeline, migrated and shipped — and two services claiming the same bounded
> context is exactly the second-source-of-truth risk the ADR walks through. None of the cards
> below should be started.

# Dev B — the pipeline service

**You own `services/pipeline/` and nothing else.**

Five cards, in this order. Each is roughly a day. Do not start a card until the previous one
is merged.

## What you are building, in plain language

A **pipeline** is the set of stages a sales deal moves through — "New", "Qualified",
"Proposal", "Won", "Lost". An organization can have more than one, and one of them is its
default. Your service stores them.

This is one half of the **Sales CRM** product; Dev A is building the other half
(`services/lead`) from the *same reference service*, at the same time. You will be doing
nearly identical work in a different directory. That is deliberate — by the end of the
fortnight either of you can read the other's code.

**Your reference for every card is `services/standup/`** — the smallest complete service in
this repository. Open it first, every time. You are not designing anything; you are copying a
working service and changing the names.

Read these once before card B1:

- `CLAUDE.md` — "Rules that are not style preferences" and the "Service anatomy" table
- `docs/onboarding/never-do.md`
- `docs/onboarding/git-workflow.md`

> **One coordination point, on card B4 only.** You and Dev A both declare the same two role
> names for the `salescrm` product. The build hard-fails if you disagree by even one
> character. B4 gives you the exact block; do not retype it from memory, and do not start B4
> until Dev A's card A4 is merged.

---

# CARD B1 — the tables

**BRANCH** `feat/b1-pipeline-model`

**GOAL** Give the pipeline service its two database tables.

### REFERENCE

`services/standup/app/models.py` — read the whole file including the comments. Lines 40–66 are
a complete table on this platform. You are writing two tables in that same shape.

### FILES YOU WILL TOUCH

```
services/pipeline/app/models.py
services/pipeline/migrations/versions/<new file>    ← created by a command; you do not write it
```

### THE CONTRACT

`TenantBase` already gives both classes `id`, `organization_id`, `created_at`, `updated_at` and
`deleted_at`. **Do not declare any of those.**

**Table one** — `class Pipeline(Base, TenantBase)`, `__tablename__ = "pipelines"`:

| Column | Type | Null? | Note |
|---|---|---|---|
| `name` | `String(120)` | not null | |
| `is_default` | `Boolean` | not null | `default=False` |

```python
__table_args__ = (
    UniqueConstraint("organization_id", "name", name="uq_pipelines_org_name"),
    Index("ix_pipelines_org_default", "organization_id", "is_default"),
)
```

**Table two** — `class PipelineStage(Base, TenantBase)`, `__tablename__ = "pipeline_stages"`:

| Column | Type | Null? | Note |
|---|---|---|---|
| `pipeline_id` | `PGUUID(as_uuid=True)` | not null | plain UUID, **not** a foreign key |
| `name` | `String(80)` | not null | |
| `position` | `Integer` | not null | ordering within its pipeline, from 1 |
| `is_won` | `Boolean` | not null | `default=False` |
| `is_lost` | `Boolean` | not null | `default=False` |

```python
__table_args__ = (
    UniqueConstraint(
        "organization_id", "pipeline_id", "position", name="uq_pipeline_stages_org_pipeline_pos"
    ),
    Index("ix_pipeline_stages_org_pipeline", "organization_id", "pipeline_id"),
)
```

`pipeline_id` is a plain UUID column even though it points at the table above it. Foreign keys
that cross a schema boundary are forbidden platform-wide (`CLAUDE.md`), and the rule is applied
uniformly rather than case by case.

Both indexes on both tables **must** start with `organization_id`.

### STEPS

1. Open `services/standup/app/models.py` and read it.
2. Open `services/pipeline/app/models.py`. Leave the three generated classes — `OutboxEvent`,
   `ProcessedEvent`, `AuditLog` — exactly as they are.
3. Add `Pipeline`, then `PipelineStage`, **above** `OutboxEvent`.
4. Copy the imports you need from the top of the standup file. Add nothing you did not use.
5. Give each class a short docstring in the style of the reference.
6. From the repository root: `make makemigration SERVICE=pipeline M="pipeline domain tables"`
7. Open the generated file and **read it**. It should create two tables. If it drops anything,
   stop and comment.
8. Add `# pragma: allowlist secret` to the end of its `revision` and `down_revision` lines.

### DONE WHEN

```bash
cd services/pipeline && uv run alembic upgrade head
cd services/pipeline && uv run alembic check
cd services/pipeline && uv run mypy app
cd services/pipeline && uv run pytest tests -q
uv run ruff check services/pipeline && uv run ruff format --check services/pipeline
```

### DO NOT TOUCH

`tools/dev-bootstrap/init-db.sql` — the `opsgrids_pipeline` role is already there. Any other
service. Anything under `libs/`.

### IF YOU GET STUCK

`alembic check` failing means model and migration disagree. Delete the migration, fix the
model, generate again. **Never hand-edit a migration to make the check pass.**

---

# CARD B2 — row-level security

**BRANCH** `feat/b2-pipeline-rls`

**GOAL** Make the database itself refuse to return one customer's pipelines to another.

### REFERENCE

- `services/standup/migrations/versions/a1d94e57c380_enable_row_level_security.py`
- `services/standup/app/config.py` — the three role lines

### FILES YOU WILL TOUCH

```
services/pipeline/app/config.py
services/pipeline/migrations/versions/<new file>
```

### THE CONTRACT

In `app/config.py`, add to the `Settings` class, keeping the reference's comment style:

```python
db_app_role: str | None = "opsgrids_pipeline"
db_admin_role: str | None = "opsgrids_admin"
```

Do not change `service_name`, `db_schema`, or the existing `db_outbox_role`.

The migration enables row-level security on **four** tables:

```python
TENANT_TABLES = ("pipelines", "pipeline_stages", "audit_logs", "outbox")
```

`processed_events` is deliberately excluded — the reference explains why in a comment. Keep it.

### STEPS

1. Read the reference migration end to end. You are copying nearly all of it.
2. Edit `app/config.py` first.
3. From inside `services/pipeline`: `uv run alembic revision -m "enable row level security"`.
   By hand this time — this migration is raw SQL that autogenerate cannot produce.
4. Copy the reference's `_enable()` helper and its `upgrade()` / `downgrade()` bodies,
   changing only the schema name and `TENANT_TABLES`.
5. Add the `# pragma: allowlist secret` markers.

### DONE WHEN

```bash
cd services/pipeline && uv run alembic upgrade head
cd services/pipeline && uv run alembic downgrade base
cd services/pipeline && uv run alembic upgrade head
cd services/pipeline && uv run pytest tests -q
uv run ruff check services/pipeline && uv run ruff format --check services/pipeline
```

The up-down-up sequence is the point of this card: it proves your `downgrade()` really undoes
your `upgrade()`. CI runs it on every service, every time.

---

# CARD B3 — the endpoints

**BRANCH** `feat/b3-pipeline-endpoints`

**GOAL** Let a signed-in user list their organization's pipelines and open one with its stages.

### REFERENCE

| For | Read |
|---|---|
| `app/scope.py` | `services/standup/app/scope.py` — the whole file |
| `app/schemas.py` | `services/standup/app/schemas.py` lines 22–43 |
| `app/selectors.py` | `services/standup/app/selectors.py` lines 29–58 and 72–80 |
| `app/api/routes.py` | `services/standup/app/api/routes.py` lines 34–77 |

### FILES YOU WILL TOUCH

```
services/pipeline/app/scope.py        ← new file
services/pipeline/app/schemas.py
services/pipeline/app/selectors.py
services/pipeline/app/api/routes.py
```

### THE CONTRACT

**`app/scope.py`** — `PRODUCT = "salescrm"`. Class `PipelineScopeResolver`. All three methods —
`owner_column()`, `team_column()`, `location_column()` — return `None`.

That is not laziness: a pipeline is a piece of organization-wide configuration, not somebody's
record. There is no "my pipeline". A caller granted `OWN` scope on a pipeline is therefore
refused rather than silently shown everything. Say that in the module docstring, in the style
of the reference.

**`app/schemas.py`** — two models:

```
StageOut:     id: UUID · name: str · position: int · is_won: bool · is_lost: bool
PipelineOut:  id: UUID · name: str · is_default: bool · stages: list[StageOut]
```

**`app/selectors.py`** — four functions:

- `pipelines_stmt(ctx) -> Select` — filters `organization_id == ctx.organization_id` **and**
  `deleted_at.is_(None)`, then `.order_by(Pipeline.is_default.desc(), Pipeline.name)`
- `get_pipeline(session, org_id, pipeline_id)` — explicit `organization_id ==` and
  `deleted_at.is_(None)`
- `stages_for(session, org_id, pipeline_id)` — ordered by `position`
- `pipeline_out(pipeline, stages) -> PipelineOut` — the projection

**`app/api/routes.py`** — two endpoints:

| Method | Path | Permission | Returns |
|---|---|---|---|
| `GET` | `/pipelines` | `salescrm.pipelines.pipeline.read` | `Page[PipelineOut]`, via `paginate` |
| `GET` | `/pipelines/{pipeline_id}` | `salescrm.pipelines.pipeline.read` | `PipelineOut` |

On the detail route: fetch, and if it is `None` raise `NotFoundProblem` with a message like
*"That pipeline is not part of this organization"*. Because no scope column exists here, there
is no `assert_can_touch` call on this service — the tenant filter is the whole check.

Declare `/pipelines` **before** `/pipelines/{pipeline_id}` — routes match in declaration order.

For the list endpoint, load each pipeline's stages in one query rather than one query per
pipeline. If you cannot see how, comment on the card — do not guess.

### STEPS

1. Read all four reference locations before writing anything.
2. `app/scope.py`, then `schemas.py`, then `selectors.py`, then `api/routes.py`. In that order.
3. Call `scope.register()` in the routes module exactly where standup calls it.

### DONE WHEN

```bash
cd services/pipeline && uv run mypy app
cd services/pipeline && PYTHONPATH=. uv run lint-imports --config ../../.importlinter --no-cache
cd services/pipeline && uv run pytest tests -q
uv run ruff check services/pipeline && uv run ruff format --check services/pipeline
```

### DO NOT TOUCH

`services/app-gateway/routes.json` — `/api/v1/pipelines` is already routed to port 8136.

---

# CARD B4 — permissions

**BRANCH** `feat/b4-pipeline-catalogue`

**GOAL** Declare what a person is allowed to do with pipelines, and add it to the `salescrm`
manifest Dev A created.

> **Do not start this card until card A4 is merged.** The two of you write into the same
> product manifest, and it is generated from both services at once.

### REFERENCE

`services/standup/app/catalogue.py`, and — more usefully — `services/lead/app/catalogue.py`
once A4 has merged, which is the same thing already adapted to this product.

### FILES YOU WILL TOUCH

```
services/pipeline/app/catalogue.py           ← new file
libs/contracts/openapi/pipeline.json         ← regenerated by a command, then committed
libs/contracts/permissions/salescrm.json     ← regenerated by that command, then committed
```

Those last two are the one exception to "never touch `libs/contracts/`" — you are not editing
them, you are running `make openapi` and committing what it produced.

### THE CONTRACT

`PRODUCT = "salescrm"`.

Two permissions. Note the module is `pipelines`, not `leads` — a permission code must have
exactly one owning service, and the build fails outright if you and Dev A declare the same one:

```python
PermissionSpec("pipelines", "pipeline", "read", "See the organization's sales pipelines", "ORG")
PermissionSpec("pipelines", "pipeline", "write", "Create and change sales pipelines", "ORG")
```

Both default to `ORG`, not `OWN` — matching card B3, where a pipeline has no owner.

Two role templates. **Copy `code`, `name` and `is_default` character for character from
`services/lead/app/catalogue.py`.** If your `name` or `is_default` differs from Dev A's by one
character, `make openapi` stops with an error naming the field you disagreed on:

```python
SALESCRM_PIPELINES_ROLE_TEMPLATES: list[RoleTemplate] = [
    RoleTemplate(
        code="SALESCRM_ADMIN",
        name="Sales administrator",
        product=PRODUCT,
        description=(
            "Administers the sales pipeline across the organization. Built from the "
            "salescrm catalogue alone."
        ),
        grants=SALESCRM_PIPELINES.all("ORG"),
    ),
    RoleTemplate(
        code="SALESCRM_MEMBER",
        name="Sales-team member",
        product=PRODUCT,
        description="Every person with a Sales CRM seat. Reads the book and works their own leads.",
        is_default=True,
        grants=SALESCRM_PIPELINES.pick(
            ("salescrm.pipelines.pipeline.read", "ORG"),
        ),
    ),
]
```

A team member reads pipelines but does not change them, so `SALESCRM_MEMBER` gets the read
grant only.

### STEPS

1. Copy `services/lead/app/catalogue.py` to `services/pipeline/app/catalogue.py`.
2. Change the docstring to describe pipelines.
3. Rename `SALESCRM_LEADS` → `SALESCRM_PIPELINES` and `SALESCRM_LEADS_REGISTRATION` →
   `SALESCRM_PIPELINES_REGISTRATION` throughout.
4. Replace the permissions and grants with the contract above. Leave `code`, `name`,
   `is_default` and `description` on the role templates alone.
5. Leave the `validate()` function and the `ProductRegistration` dataclass untouched.
6. From the repository root: `make openapi`
7. Open `libs/contracts/permissions/salescrm.json`. Its `sources` array should now list
   **both** `lead` and `pipeline`. If it lists only one, stop and comment.

### DONE WHEN

```bash
cd services/pipeline && uv run mypy app
cd services/pipeline && uv run pytest tests -q
make openapi && make openapi-check
grep -c pipeline libs/contracts/permissions/salescrm.json     # non-zero
uv run ruff check services/pipeline && uv run ruff format --check services/pipeline
```

### IF YOU GET STUCK

An error reading *"role template 'SALESCRM_MEMBER' of product 'salescrm' disagrees on 'name'
across services"* means exactly what it says: your text and Dev A's differ. Copy theirs.

---

# CARD B5 — the tests

**BRANCH** `feat/b5-pipeline-tests`

**GOAL** Prove the endpoints refuse what they should refuse.

### REFERENCE

`services/standup/tests/` — every file. And `services/lead/tests/` once A5 has merged, which is
the same thing already adapted to this product.

| Your file | Copy from | How much changes |
|---|---|---|
| `tests/helpers.py` | the reference's (52 lines) | the product name in `entitlements` |
| `tests/conftest.py` | the reference's (143 lines) | `TENANT_TABLES`, and the `data` fixture body |
| `tests/test_catalogue.py` | the reference's (51 lines) | near-verbatim — names only |
| `tests/test_authz.py` | the reference's (260 lines) | permission strings and URLs |
| `tests/test_cross_tenant_isolation.py` | the reference's (172 lines) | table and schema names |

### FILES YOU WILL TOUCH

Only files under `services/pipeline/tests/`.

### THE CONTRACT

`TENANT_TABLES` in `conftest.py` is `("pipeline_stages", "pipelines", "audit_logs", "outbox")` —
stages before pipelines, so the truncation order does not fight the data.

`test_authz.py` must contain **all four** of these for each endpoint:

| Test | Expects |
|---|---|
| no principal at all | **401** |
| a principal without `salescrm.pipelines.pipeline.read` | **403**, `content-type` starting `application/problem+json` |
| a principal with no `salescrm` entitlement | **402** |
| a reader calling a write route | **403** |

Plus the positive controls the reference shows.

There is no out-of-scope-write test on this service, because pipelines have no owner — say so
in a comment where the reference has that test, so the next reader knows it was a decision
rather than an omission.

`conftest.py`'s `data` fixture must seed through the **real** write path, never raw SQL. The
reference does this at lines 95–130.

### STEPS

1. Copy all five files across unchanged first. Run the suite; it will fail loudly.
2. Fix them one file at a time. `helpers.py` first, `test_authz.py` last.
3. Run the suite after each file.

### DONE WHEN

```bash
cd services/pipeline && uv run pytest tests -q      # all green, nothing skipped
cd services/pipeline && uv run mypy app
make hygiene
uv run ruff check services/pipeline && uv run ruff format --check services/pipeline
```

`make hygiene` fails on a skipped test, a stray `print()`, or a bare `# noqa`. A skipped test
is a test that does not run, and a green build that does not say so.

---

## After B5

Sales CRM has both of its halves. The next card in this lane adds an event: when a lead is
created, the pipeline service learns about it. That one involves the outbox, and it will come
with a much longer card.
