# The never-do list

Print this. Keep it next to you. Every rule here exists because breaking it has a cost that
is not obvious from the code in front of you.

---

### 1. Never guess.

If the card does not say it, do not decide it. Comment on the card and wait.

Every other rule on this page is a special case of this one. Inventing a column, forking a
component, adding a permission, picking a name — all of it is guessing, and guessing is the
only thing that breaks alignment across four people who cannot each hold the system in their
heads.

### 2. Never edit a generated file by hand.

Everything under `libs/contracts/` is produced by `make openapi`. If it looks wrong, the
*code* is wrong. Fix the code, run the command again, commit what it produced.

CI runs `make openapi-check` and will fail if a committed contract does not match the code —
including if you "helpfully" corrected it.

### 3. Never run `pytest` or `mypy` from the repository root across `services/`.

Every service exposes a top-level package called `app`. Only one can be importable in a single
process. From the root you get import errors that look like missing dependencies and are not.

Always:
```bash
cd services/lead && uv run pytest tests -q
cd services/lead && uv run mypy app
```

### 4. Never write a query that does not filter on `organization_id`.

Every read lives in `app/selectors.py`, filters on `organization_id` explicitly, and calls
`ctx.apply(...)`. The database has row-level security underneath as a safety net — that is a
net, not permission to skip the filter.

A query that forgets it is how one customer sees another customer's data.

### 5. Never copy a component out of `@atmora/ui` into an app.

Nine apps growing nine slightly different date pickers is exactly the failure this rule
prevents. If a product needs a variant, the variant becomes a **prop on the shared
component** — and that is a card of its own, assigned to one person at a time.

### 6. Never touch another developer's service or app.

You own what your card names. If your change seems to need a file in someone else's lane,
that is a signal the card is wrong. Comment.

### 7. Never touch these, for any reason:

```
libs/platform-core/**                the shared kernel — a change here changes all 33 services
libs/contracts/**                    generated (see rule 2)
tools/dev-bootstrap/init-db.sql      database roles; runs once, on a fresh volume only
docs/adr/**                          decisions that are expensive or impossible to reverse
services/*-gateway/routes.json       what maps a URL to a service
.github/**  .importlinter  Makefile  pyproject.toml  uv.lock
frontend/pnpm-workspace.yaml  frontend/pnpm-lock.yaml
```

GitHub will ask for the reviewer's approval automatically if you do. That is a backstop, not
an invitation.

### 8. Never push to `development` or `main`.

You branch from `development`, you open a pull request into `development`. That is the only
way your code gets there.

### 9. Never open a pull request before the card's DONE WHEN commands pass.

And never mark a card done without pasting their real output into the pull request. Not a
summary. The actual output.

### 10. Never use `git push --force`, and never resolve a merge conflict.

Both are reviewer work on this team. Comment and wait.

### 11. Never add a dependency.

Not `pip install`, not `pnpm add`, not an import of something that is not already used
somewhere in the same directory. Every dependency is an install-time cost, an image-size cost
and a security-advisory surface. If a card seems to need one, the card is wrong.

---

## Two things that will fail CI and confuse you

**A new database migration needs a marker.** The secret scanner reads Alembic revision ids as
high-entropy strings and flags them as leaked credentials. Every new migration file needs this
on its `revision` and `down_revision` lines:

```python
revision = "a1b2c3d4e5f6"  # pragma: allowlist secret
down_revision = "f6e5d4c3b2a1"  # pragma: allowlist secret
```

**`make secrets` only looks at files git knows about.** Run `git add -A` *first*, then run it.
Otherwise your brand-new file is skipped locally, passes, and fails in CI.

---

## When you are stuck

Comment on the card with:

1. What you were doing — the step number from the card.
2. The **exact** error text, copied and pasted. Not "it didn't work".
3. What you tried.

Then stop and pick up nothing else until you have an answer. An idle hour is cheaper than a
guess.
