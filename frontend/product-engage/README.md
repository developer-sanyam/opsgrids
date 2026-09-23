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
| Lists | `GET /engage/lists`; create, rename and delete with `POST`, `PATCH`, `DELETE /engage/lists[/{id}]` |
| List members | `GET /engage/lists/{id}`, `GET .../members`; add and remove with `POST .../members` and `POST .../members/remove` (a CSV import is `POST /engage/contacts/import` followed by the add) |
| Campaigns | `GET /engage/campaigns` (server-side status filter), `GET /engage/lists` |
| Campaign (draft) | `GET/PATCH/DELETE /engage/campaigns/{id}`, `POST /engage/campaigns`, `POST .../send`; the sender pickers read `GET /engage/senders` and `GET /engage/domains` |
| Campaign (sending, sent, paused, cancelled) | `GET /engage/campaigns/{id}` (polled while `sending`), `GET .../recipients`, `POST .../cancel`, `POST .../resume` |

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

Overview and Inbox; Email → Connections, Domains, Senders; WhatsApp → Connection, Templates;
Audience → Contacts, Lists, Suppressions; Campaigns. Lists and Campaigns are hidden from a member
known not to hold their read permission and are behind a `RequireGrant` on the route — a courtesy,
not protection: the service refuses every read regardless, and the copy of the denial says so.

## Campaigns: what the service does and does not do

- **Send is a gate the service owns.** `POST .../send` answers `422` with one unmet precondition in
  a sentence meant to be shown; `src/lib/sendGate.ts` shows it verbatim and adds the step that fixes
  it. `src/lib/sendGate.test.ts` pins every sentence the service can raise, so a reword that breaks
  a shortcut fails a test rather than silently dropping it.
- **Send acts on the saved draft**, so the control is disabled while the form has unsaved changes.
- **There is no scheduling.** `scheduled_at` is stored and nothing acts on it — send is immediate and
  no campaign is ever `scheduled` — so the form does not offer a time.
- **Bodies are sent exactly as written.** There is no merge-field substitution, and an unsubscribe
  link is appended to every message. The HTML preview is a sandboxed `srcdoc` iframe (no scripts,
  no same-origin); nothing in this app uses `dangerouslySetInnerHTML`. **Remote images are blocked
  in it until the person previewing turns them on** (`img-src 'none'`, then `https:`): an image is a
  request from this browser to whoever hosts it, carrying the previewer's IP address and browser.
  The policy cannot cover everything: `prefetch`, `preconnect` and `dns-prefetch` hints in the
  markup are acted on by the browser's own networking, so the preview says it cannot block them.
- **Reference data is read in full, and never assumed complete.** Senders, domains and lists are
  read page by page (up to 1,000 each); past that a picker says "Showing N of TOTAL". A campaign's
  own list is read by id, so it always names the list it is addressed to, and a label says a list
  "no longer exists" only on a real `404`. Each read settles alone, so a role that may read
  campaigns and not lists still sees every row, with neutral labels (`src/lib/refs.ts`).
  **Reads have a call budget**, because the gateway allows 300 requests a minute a user: a draft is
  four calls (campaign, senders, domains, lists), a sent campaign's report three (campaign,
  senders, its own list by id), and a campaigns page reads one list by id per *distinct* list on
  the page — the first alone, the rest four at a time, and none once one is refused. Pages are read
  two at a time and de-duplicated by id, since `OFFSET` pages can repeat a row (the service now
  breaks ties on `id` for senders and domains). `src/routes/CampaignCallBudget.test.tsx` pins it.
- **A poll the service refuses stops and keeps the figures.** On a `401`/`403` `usePolling` stops,
  keeps the last good data and reports `stopped`; the report says "Live updates stopped — reload to
  refresh" rather than reverting the counts.
- **Reporting is the five counts the service folds** (`recipients`, `queued`, `sent`, `failed`,
  `suppressed`). There are no delivered, bounced or unsubscribed counts because a recipient row has
  no link to the delivery ledger or to a later unsubscribe; those land on Suppressions.
- **The test send is the sender's** (`POST /engage/senders/{id}/test`); the service has no
  per-campaign test send.

## Environment

| Variable | Default | What it does |
|---|---|---|
| `VITE_USE_FIXTURES` | unset — **live** | `true` renders the app from `src/api/fixtures.ts` with no gateway and no login. Dev builds only. |

Fixture mode serves the reads and **refuses every write**, naming the flag. Connecting seals a
credential, adding a domain mints a DKIM keypair, verifying resolves DNS and a test send puts mail
on the wire — a browser with no service behind it does none of those, and a fabricated DKIM record
is one a customer could paste and could never verify.
