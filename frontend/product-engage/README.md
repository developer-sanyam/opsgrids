# @opsgrids/product-engage

Engage, Phase 1: an organization's own **sending identity** — the provider it sends through, the
domains it has proven, and the named senders mail may go out as.

```bash
pnpm dev          # http://localhost:4420
pnpm typecheck && pnpm lint && pnpm test && pnpm build
```

## What it talks to

`services/notification`'s engage router, which the gateway mounts at `/api/v1/engage` behind the
`engage` entitlement (`services/app-gateway/routes.json`). It is the same process that serves the
platform's transactional notifications and a different **product**: every route is gated on
`engage`, never on `notification`.

| Screen | Calls |
|---|---|
| Overview | all three reads, to say which setup steps are done |
| Connections | `GET /engage/connection` |
| Domains | `GET /engage/domains` |
| Senders | `GET /engage/senders`, `GET /engage/domains` (to name each sender's domain) |

`src/api/client.ts` also carries the four writes the service publishes — `POST /engage/connection`,
`POST /engage/domains`, `POST /engage/domains/{id}/verify`, `POST /engage/senders` and
`POST /engage/senders/{id}/test` — typed and pinned by `client.test.ts`, ready for the screens that
will use them.

## The credential never reaches a screen

`ConnectionIn.secret` is write-only: it is sealed on the way in and has no counterpart on the way
out, and the DKIM private half has no field anywhere in the service. **Nothing in this app may
render it, mask it, count it or reconstruct a hint from anything else** — a masked secret is a
secret with a hint attached. `src/api/fixtures.test.ts` pins `ConnectionOut`'s key set so a field
added later is a red build rather than a leak nobody notices.

## Navigation

Overview, and Email → Connections, Domains, Senders. Campaigns, templates, lists and reporting
belong to later phases and have no nav entry until they have a screen: a rail entry that lands on
a "coming soon" panel teaches people the rail lies.

## Environment

| Variable | Default | What it does |
|---|---|---|
| `VITE_USE_FIXTURES` | unset — **live** | `true` renders the app from `src/api/fixtures.ts` with no gateway and no login. Dev builds only. |

Fixture mode serves the reads and **refuses every write**, naming the flag. Connecting seals a
credential, adding a domain mints a DKIM keypair, verifying resolves DNS and a test send puts mail
on the wire — a browser with no service behind it does none of those, and a fabricated DKIM record
is one a customer could paste and could never verify.
