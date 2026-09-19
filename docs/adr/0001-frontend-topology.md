# ADR-0001 — Frontend topology: one app per surface

Status: **accepted** · Date: 2026-08-20 · Supersedes the single `product-shell` in the plan

## Decision

Every surface is its own standalone application under `frontend/`, and every product is
standalone on every platform. They share code through packages, not through a monolith.

```
frontend/
├── opsgrids-website        REMOVED 2026-09-19 — see the amendment at the end of this file
├── opsgrids-dashboard      The ecosystem dashboard: what you land on after login   Vite · React
├── opsgrids-admin          Customer administration — users, teams, roles, billing  Vite · React
├── opsgrids-internal       OpsGrids staff console — every organization, overrides     Vite · React
│
├── product-crm           Standalone CRM                                      Vite · React
├── product-hrms          Standalone HRMS                                     Vite · React
├── product-pulseboard    Standalone PulseBoard                               Vite · React
├── product-projects      Standalone Project Management                       Vite · React
├── product-support       Standalone Support / IT                             Vite · React
├── product-marketing     Standalone Marketing                                Vite · React
├── product-finance       Standalone Finance                                  Vite · React
│
├── opsgrids-ui             Design system — the components every app imports    package
├── opsgrids-client         Generated API clients, auth SDK, entitlement hooks  package
└── opsgrids-config         Shared tsconfig / eslint / vite presets             package

android/   one app per product, plus a shared module
ios/       one app per product, plus a shared framework
```

## Why one app per surface rather than one shell

**A customer who bought only CRM should download only CRM.** A single shell bundles every
product's routes, and code-splitting hides that from the metrics without fixing it — the
dependency graph, the build time and the blast radius stay shared.

**Deploys stop being coupled.** A CRM release cannot break HRMS if they are different
artifacts. With three developers and nine products, the alternative is every team waiting on
one pipeline.

**Teams get an ownership boundary that matches the backend.** The services are already split
by product; making the frontend match means one team owns `product-crm` and the CRM services
end to end.

**Marketing has genuinely different requirements.** It is public, SEO-critical, mostly static,
and changes on a marketing cadence rather than a product one. It wants server rendering and
prerendering; a logged-in application wants a fast client-side router and no SSR at all. One
framework serving both serves neither well — which is why the website is Next.js and every
application is Vite.

This follows the reference architecture: separate `website`, `admin`, `internal-dashboard`
and per-surface apps, all consuming one shared component package.

## What it costs, and how each cost is paid

**Session across origins.** Several apps on several subdomains must not each ask for a
password. Sign-in happens once at `auth.opsgrids.com`; the refresh token is an httpOnly cookie
scoped to the parent domain, and each app exchanges it for a short-lived access token whose
`aud` is that product. This is why tokens are per-audience in ADR-0000 §3 — the topology and
the token design were decided together.

**Drift between apps.** Nine apps can grow nine date pickers. `opsgrids-ui` is therefore a real
package with its own tests, not a folder of shared files, and an app may not fork a component
into itself. Where a product genuinely needs something different, it goes into `opsgrids-ui`
with a variant.

**Nine build pipelines.** Mitigated by `opsgrids-config`: one tsconfig preset, one eslint
config, one vite preset. A new product app is a `package.json`, a `vite.config.ts` that spreads
the preset, and a router.

**Nine deployments.** Each app is static output behind a CDN with its own domain or path. The
gateway does not serve frontends, so a frontend deploy never touches the API.

## Routing and domains

| Surface | Domain |
|---|---|
| Marketing | `opsgrids.com` |
| Sign-in | `auth.opsgrids.com` |
| Dashboard | `app.opsgrids.com` |
| Customer admin | `app.opsgrids.com/admin` |
| Staff console | `internal.opsgrids.com` |
| A product | `crm.opsgrids.com`, `hr.opsgrids.com`, … |

Every application talks to `api.opsgrids.com` — `app-gateway` for product traffic,
`admin-gateway` for the staff console. No application talks to a service directly.

## What every product app inherits

It does not re-implement any of this:

- **Auth** — `@opsgrids/client` handles sign-in redirect, silent refresh, org switching and
  logout. The access token lives in memory only; never `localStorage`, which any XSS payload
  can read.
- **Entitlements** — `useEntitlement(feature)` reads the org's snapshot. It hides UI; the
  server refuses the call regardless, because a UI gate is not enforcement.
- **Permissions** — `<RequirePermission perm="crm.pipeline.deal.write">`, mirroring the
  server's scope resolution exactly, including that `read` never implies `read_sensitive`.
- **Design system** — `@opsgrids/ui`, themed per product by a token set rather than a fork.
- **Errors** — problem+json parsed into typed errors with field-level detail for forms.

## Mobile

One app per product per platform, sharing a module (`android/shared`, `ios/OpsGridsKit`) that
carries auth, the API client and the design tokens. The reference architecture ships separate
consumer, admin, delivery and picking apps for exactly this reason: a warehouse app and a
sales app have nothing in common but the account.

---

## Amendment — the marketing site leaves the monorepo (2026-09-19)

`frontend/opsgrids-website` is removed. The marketing site now lives in its own repository,
**developer-sanyam/opsgrids-marketing-website**, and deploys to Vercel on `opsgrids.com`.

**Why it is the one surface that should not be here.** Every other app under `frontend/` is
signed-in, ships behind the gateway, and shares `opsgrids-ui` and `opsgrids-client`. The
marketing site does none of that: it has no session, calls no API, imports neither package, and
its release cadence is a marketing decision rather than a platform one. Keeping it in the
workspace meant a copy change waited on the platform's CI, and the platform's CI carried a
Next.js build that nothing else depended on — it was the step that failed the frontend gate on a
Node version the rest of the workspace does not use.

**What this does not change.** The rule that an app may not fork a component into itself still
holds for every app that remains. The marketing site is outside the design system by nature: it
is a public brochure, not a console surface, and it reads the Console's tokens as *values* — the
same violet, ground and Manrope — rather than importing the package.

**Consequences.**

- `frontend/pnpm-workspace.yaml` no longer lists it, and `make frontend` no longer builds it.
- The CI cache entry for its `.next` directory is gone.
- `docs/existing-theme-features.md` and `docs/subcategory-filter-plan.md` describe the removed
  app and are now historical; neither names anything that still exists.
- The old `src/content/products.ts` is gone with it. It was the marketing catalogue's source of
  truth and it carried claims that have since been corrected — notably a CRM pipeline that is an
  empty scaffold. The new site's copy was rewritten against what is actually built, per product,
  with an explicit "not yet" column.
