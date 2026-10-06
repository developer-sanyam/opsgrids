# List prices 2026-10-06 — INR 30,000 a month, INR 3,00,000 a year

**Status:** owner decision 2026-10-06, built on `feat/list-prices-30k-month-3l-year` (stacked on `main`), not
merged or deployed
**Decision record:** [ADR-0035](../../adr/0035-list-prices-2026-10-06.md)
**Services:** `catalog` (data migration `b88a833020dc`, publish check), `subscription` (customer subscribe,
cadence copy), `console` (MRR by cadence), `frontend/opsgrids-dashboard` (Marketplace trial pin),
`frontend/opsgrids-internal` (cadence guidance); verified, unchanged: `billing`

---

## 1. The decision

> "For total billing of complete product should be 300000rs years and monthly so divided according to that only
> into the all the products." — the owner, 2026-10-06

> "Yearly price would be less that would be 300000 and monthly 30000" — and the split: "Proportional to today's
> prices". — the owner, 2026-10-06, in answer to a direct question

Every product together costs **INR 30,000 a month** or **INR 3,00,000 a year** — the yearly option deliberately
the cheaper one, ten months' price for twelve (two months free). Each total is divided across the eleven products
in proportion to what each one's public flat plan cost until today.

The first sentence fixed only the yearly number; a review round read the monthly one as its twelfth, INR 25,000.
The owner was asked and rejected that reading: the monthly total is INR 30,000 and the year is the discount
(ADR-0035, Context).

## 2. The table

Amounts in rupees; the migration stores integer paise (x100). The old prices were the seeded placeholders
(`e1f2a3b4c5d6`, `10e6b62d9ac7`, `53f1a7a8b99d`), still live in production on 2026-10-06.

| Product | `product_key` / plan `code` | Was / month | Exact share / month | **New / month** | **New / year** |
|---|---|---:|---:|---:|---:|
| Revenue | `crm` / `revenue` | 1,999 | 3,000.15 | **3,000** | **30,000** |
| People | `hrms` / `people` | 1,999 | 3,000.15 | **3,000** | **30,000** |
| IT | `itsm` / `it` | 1,999 | 3,000.15 | **3,000** | **30,000** |
| Finance | `finance` / `finance` | 2,499 | 3,750.56 | **3,750** | **37,500** |
| Inventory | `operations` / `inventory` | 2,499 | 3,750.56 | **3,750** | **37,500** |
| Engage | `engage` / `engage` | 1,499 | 2,249.74 | **2,250** | **22,500** |
| Marketing | `marketing` / `marketing` | 1,499 | 2,249.74 | **2,250** | **22,500** |
| Projects | `projects` / `projects` | 1,499 | 2,249.74 | **2,250** | **22,500** |
| PulseBoard | `pulseboard` / `pulseboard` | 1,499 | 2,249.74 | **2,250** | **22,500** |
| Storefront | `storefront` / `storefront` | 1,499 | 2,249.74 | **2,250** | **22,500** |
| Support | `support` / `support` | 1,499 | 2,249.74 | **2,250** | **22,500** |
| **All eleven** | | **19,989** | **30,000** | **30,000** | **3,00,000** |

"Exact share" is `old x 30,000 / 19,989`. The new monthly prices round each share so the eleven sum to exactly
INR 30,000 (3,000,000 paise); every one is within 0.02% of its share, equal old prices stay equal and the order is
kept. Each yearly price is exactly ten times its monthly one and they sum to exactly INR 3,00,000 (30,000,000
paise). The test `services/catalog/tests/test_list_prices_2026_10_06.py::TestTheNumbers` pins all of this against
the seeds' own amounts.

**In scope:** only these eleven public flat base plans. **Out of scope:** the per-seat Starter/Growth plans, the
suite bundles, every bespoke (`restricted_to_org_id`) version, and every existing subscription.

## 3. How it ships

A catalog data migration, `b88a833020dc` (follows `b7e4d2a9c613`), in the shape of the flat-plan seeds:

1. Find each plan by `(product_key, code)`. Skip, with a warning, a plan that does not exist, is retired, or has
   no published public version (withdrawn on purpose — publishing would put it back on sale).
2. The source is the plan's newest **published public** version. Refuse (abort the whole migration) unless its
   sheet is exactly one flat INR price with only free seat classes; warn (and apply) when that price is not the
   seeded amount the split was computed from.
3. Refuse (abort, naming each) if any plan it re-prices has an **open public draft**: the new versions would be
   numbered above it, so the draft, published later, would never be offered or shown as the price. Steps 1–3 run
   for every plan before anything is inserted.
4. Insert the **yearly** version (`version_no` = highest + 1), then the **monthly** one (+ 2), each as a draft:
   one flat INR price at the new amount, the source's features copied value-for-value in SQL, its free seat
   classes copied; then publish the draft (`draft -> published`, `published_at = now()`). No published row is
   updated; since `b7e4d2a9c613` a child row cannot be inserted under a published version, which is why the
   draft step exists.
5. Ids are `uuid5` of the migration's own namespace; a version whose id exists is skipped, so a re-run is a
   no-op.

Why yearly first: `get_public_flat_plan`, `all_public_flat_plans` and `current_public_versions` take the newest
public flat version as "the price" without reading cadence; with the monthly version newest they keep meaning the
monthly price.

**Downgrade** refuses while any of the 22 versions exists unless `OPSGRIDS_ALLOW_LIST_PRICE_DOWNGRADE=1`
(catalog cannot see subscription's pins); with it, it removes exactly those versions and their rows.
`tools/roundtrip.sh` passes that override — never the shared `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE`, which guards
operations' real lots and rolls — to its `downgrade base` only.

**Dashboard:** the Marketplace's `trialOffer` now pins a new trial to the newest public monthly version (it took
the oldest published one, so every new trial would have started on the old price), else the newest flat one.

**Alongside the migration** (ADR-0035 Decisions 9–12):

* **Subscription** — the customer subscribe write enforces catalog's offerable set (`422 not_offerable`), so the
  old version, still published for its subscribers, cannot be started on by id. The cadence refusal no longer
  tells anyone to cancel and resubscribe.
* **Console** — MRR counts a month's worth: a yearly flat price / 12 (integer paise, half up), `one_time` 0.
* **Catalog** — publishing refuses a draft a newer published version of the same audience, cadence and currency
  already supersedes (`superseded`).

## 4. What each surface does afterwards (verified by tests)

| Surface | Before | After |
|---|---|---|
| Catalog offerable-versions (staff and customer pickers, both change-plan writes) | v1 month | v3 month (new) and v2 year (new); v1 superseded |
| Customer `GET /plans` | v1 | v1, v2, v3 — no amounts, as before |
| Dashboard Start trial | pins v1 | pins v3 (new monthly) |
| Customer `POST /subscriptions` naming v1 (a stale tab, or by id) | starts on v1 | 422 `not_offerable`; nothing created |
| Staff Add product | v1 | v3 or v2 — both takeable |
| Console MRR, yearly subscriber on v2 (INR 30,000) | — | INR 2,500 a month (a monthly one on v3: INR 3,000) |
| Customer Change plan, monthly subscription | — | v3 takeable; v2 blocked `cadence`; current v1 listed first, "no longer offered" |
| Change plan to v2 (customer write, preview, staff write) | — | 422 `cadence`; nothing changes |
| Existing subscriptions | pinned v1 | still pinned v1, same price, until staff switch them |
| Billing run, yearly subscription | — | one period (and one invoice) every 365 days, priced at the full yearly amount |
| Console pricing baseline, Plans page "current", bespoke clone source | v1 | v3 (new monthly) |

## 5. Follow-ups (not built)

1. Moving an existing monthly customer to yearly needs cancel-and-resubscribe, and a cancelled subscription
   cannot be replaced yet.
2. The dashboard has no cadence choice, so a customer cannot self-serve a yearly start; staff Add product can.
3. Make the three cadence-blind "public flat plan" reads prefer `month` instead of relying on version order.
