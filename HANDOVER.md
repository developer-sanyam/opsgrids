# HANDOVER.md — session continuity

Ephemeral. **Read this first when a session starts; rewrite it before a session ends.** It holds
only what the next session needs to pick up the work: what is active, what is blocked, and the
next concrete step. Anything that should outlive the week moves to [`MEMORY.md`](MEMORY.md).

Keep it under ~80 lines. Replace sections; do not append a diary.

---

**Last updated:** 2026-10-14 · **Production:** `main` @ `d73bb9e4` · **Open PRs:** none (before this docs PR)

## Active

| Work | Where | State | Next step |
|---|---|---|---|
| CRM overhaul programme | `docs/superpowers/specs/2026-10-07-crm-*`, ADR-0038 | R2.4 live in `log_only` | Build **R2.5** (stage editing, permission `crm.pipeline.stage.write`, ORG floor; D-6) as one PR against `main` |
| CRM R2.4 cleanup | deal + customer consumers | `CRM_CONVERSION_CLEANUP_MODE=log_only` in prod | Owner soaks, reads the log lines, then flips to `on` |
| Order invoicing (#146) | operations → finance | `ORDER_INVOICING_MODE=log_only`, per-org opt-in | Owner decides which orgs opt in |
| GST tax invoices (#145) | billing | Inert until supplier settings exist | Owner supplies supplier GST facts |
| Agent docs (this change) | `CLAUDE.md`, `SPEC.md`, `MEMORY.md`, `HANDOVER.md`, `.claude/settings.json` | On branch `worktree-docs-agent-guides` | Owner reviews and merges |

## Blocked on the owner

- **Every production action** — deploy, migrate, restart, `.env` edit, reconcile `APPLY=1`, prod DB
  read — needs explicit approval of *that* action. "Ship it" is not approval.
- **Memory ceiling:** insights (+99 MB, `sprint/insights*`) and any new container wait for an owner
  answer on `EDGE_BUDGET_ACCEPTED_MB`. Each later CRM consumer fold raises it by one (3946 at R3.8).
- **CI:** GitHub Actions billing is blocked; no PR gets green CI. Run checks locally and paste results.
- **Owner-run checks still owed:** CRM O-9/O-13 (prod smoke creating a throwaway org); the ADR-0023
  post-deploy audit query from 2026-09-27; D-3 / D-10 read-only CRM counts before R2.6.
- **Meta (WhatsApp) dashboard:** Tech Provider, business verification, Live mode, privacy and
  data-deletion URLs.
- **Google Maps key** for put-away is not on the box; `product-operations` builds without the map.
- **Marketing site** repo PRs #9 (SEO fixes, consent banner) and #10 (legal drafts, needs facts).

## Known red on `main` (not caused by current work)

- `make secrets` fails on two unbaselined hits: `libs/platform-core/tests/test_gst.py:27` and
  `tests/infra/test_backup_offbox.py:95` (verified 2026-10-14; `make test-infra` is green, 1158 passed).
- Storefront console `index.html` still loads Google Fonts (follow-up from #139).

## How to resume

```bash
git fetch origin && git log --oneline -5 origin/main   # confirm main still matches "Production" above
gh pr list --state open                                 # anything new since this file was written?
make infra && make migrate                              # local stack (Docker required)
make check                                              # baseline before changing anything
```

For a CRM release: read the programme spec §10 card for the release, open ADR-0038 for its decision
ids, branch `feat/crm-r<id>-<slug>` from `origin/main`, build, run that service's tests in its own
process, open one PR against `main`, and report with the six-part completion report (CLAUDE.md).
