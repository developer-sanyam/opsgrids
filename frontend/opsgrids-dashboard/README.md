# OpsGrids dashboard

The ecosystem dashboard from ADR-0001, built for ADR-0013: what a customer lands on at
`app.opsgrids.com`. A stranger signs up here; an owner signs in, sees every product this
deployment sells, starts one on a 14-day trial, and reads what the organisation pays.

| Route | What it is |
|---|---|
| `/signup` | Create an organisation and become its owner. No password field — the link comes by email |
| `/login` | Sign in (org picker when the person belongs to several) |
| `/forgot-password`, `/reset-password?token=…` | The set-password path, also used by a fresh sign-up |
| `/` | Marketplace: the catalogue, each product's flat monthly price, "Start 14-day trial" |
| `/subscriptions` | What the organisation has started, its state and trial end |
| `/invoices` | Frozen invoices |

Everything talks to `/api/v1` same-origin: Vite proxies it to the gateway in dev, Caddy does in
production. No fixtures mode — the dashboard has no design surface of its own to work offline on.

```bash
pnpm dev          # http://localhost:4500, gateway expected on :8100
pnpm test         # vitest, jsdom
pnpm build        # dist/ — published by `make edge-web APP=dashboard`
```
