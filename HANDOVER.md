# HANDOVER.md — session continuity

Ephemeral. **Read this first when a session starts; rewrite it before a session ends.** It holds
only what the next session needs to pick up the work: what is active, what is blocked, and the
next concrete step. Anything that should outlive the week moves to [`MEMORY.md`](MEMORY.md).

Keep it under ~80 lines. Replace sections; do not append a diary.

---

**Last updated:** 2026-10-15 · **Production:** `main` @ `1dbeebd4` (PRs #148–#170) · **Open PRs:** none
(before the docs-refresh PR that carries this file)

Deployed 2026-10-15 in three batches. Pre-deploy backups, **on the box only**:
`opsgrids-20261014T055541Z`, `opsgrids-20261014T083700Z`, `opsgrids-20261014T090950Z`.

## Active

Nothing in flight. The docs refresh (this change, branch `docs/refresh-2026-10-15`) is the only
open work and waits for owner review.

## Blocked on the owner

- **Every production action** — deploy, migrate, restart, `.env` edit, reconcile `APPLY=1`, prod DB
  read — needs explicit approval of *that* action. "Ship it" is not approval.
- **Off-box backup bucket.** `BACKUP_S3_*` is empty on the box. `backup.sh` still writes the dump to
  the box's own disk (newest 7 kept) and then exits non-zero. A disk loss loses the database and
  every backup with it. Fix: owner supplies a bucket, then `make edge-backup` and a restore drill.
- **Memory budget.** 3945 MB planned against MemTotal 3862 MB; inside the accepted ceiling
  (`EDGE_BUDGET_ACCEPTED_MB`), not inside the box. Insights (+99 MB) and any new container wait for
  an owner answer on the ceiling.
- **CI.** GitHub Actions billing is blocked; no PR gets green CI. Run checks locally and paste results.
- **Payments** are in test mode. Live Razorpay/Cashfree keys are the owner's.
- **GST portal is not connected.** Tax invoices print without IRN/QR until it is.
- **Owner-run checks still owed:** CRM O-9/O-13 (prod smoke creating a throwaway org); the ADR-0023
  post-deploy audit query from 2026-09-27; D-3 / D-10 read-only CRM counts before R2.6.
- **Dark-launched switches:** `CRM_CONVERSION_CLEANUP_MODE=log_only` and `ORDER_INVOICING_MODE=log_only`.
  Owner reads the logs, then flips.
- **Meta (WhatsApp) dashboard** steps; **Google Maps key** for put-away; **marketing site** repo PRs
  #9 and #10 (a separate repository).

## Known red

- `libs/platform-core` integration tests that need RabbitMQ or MailHog fail without a local broker.
  Run `make infra` first. They are environment-red, not code-red.
- Storefront console `index.html` still loads Google Fonts (follow-up from #139).
- `make secrets` is red on `main` (re-run 2026-10-15) on unbaselined hits, previously found at
  `libs/platform-core/tests/test_gst.py:27` and `tests/infra/test_backup_offbox.py:95`.

## Next

1. **CRM R2.5** — stage editing, permission `crm.pipeline.stage.write`, ORG floor (D-6). One PR
   against `main`, branch `feat/crm-r2.5-<slug>`. Spec: programme §10, ADR-0038.
2. Owner soak and flip of the two `log_only` switches above.
3. Frontend follow-ups from the 2026-10-14 mobile work: run the layout sweep per app after any
   `opsgrids-ui` change (`frontend/VISUAL-TESTING.md`, "The layout sweep"); the sweep is not in CI.
4. Linux visual baselines do not exist (`baselines/darwin/` only); regenerate on darwin after UI changes.
5. Remaining dedupe candidates that #165–#169 deliberately left alone (account's and product's
   `actor` variants, product's and console's envelope-time parsers).

## How to resume

```bash
git fetch origin && git log --oneline -5 origin/main   # confirm main still matches "Production" above
gh pr list --state open                                 # anything new since this file was written?
make infra && make migrate                              # local stack (Docker required)
make check                                              # baseline before changing anything
```

For a CRM release: read the programme spec §10 card, open ADR-0038 for its decision ids, branch
from `origin/main`, build, run that service's tests in its own process, open one PR against `main`,
and report with the six-part completion report (CLAUDE.md).
