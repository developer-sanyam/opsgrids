# @opsgrids/opsgrids-internal — operator console

The vendor's cross-tenant control panel (ADR-0001 `opsgrids-internal`, ADR-0003 §2). It talks **only**
to `admin-gateway` on the **operator** audience — never a product SPA — and reads commercial data
across every customer while driving the customer lifecycle.

It is built from `@opsgrids/ui` (components and tokens) and the request, session and async primitives
of `@opsgrids/client`, like every other app. What is its own is what only an operator has: the
sign-in, the token slot, and the control-panel pieces (money, lifecycle status, the MRR trend).

## Screens

| Route | Screen | Who (ADR-0003 role matrix) |
|---|---|---|
| `/login` | Sign-in: password, then a code, or enrolment | anyone |
| `/` | Overview — MRR, active accounts, trials, conversion, churn, locked/suspended | any operator |
| `/accounts` | Cross-tenant directory with status, MRR and drill-down | any operator |
| `/accounts/:orgId` | Subscriptions, entitlements, usage, invoices, credit ledger, and the actions: suspend / reactivate, comp grant, bespoke price, plan migration | any operator |
| `/subscriptions` | Every subscription across tenants, with lifecycle state and next renewal | owner, billing_ops |
| `/plans` | The immutable catalog, and creating a bespoke price for one organization | owner, billing_ops |
| `/grants` | The credit ledger: who is on comp access and when it lapses; grant and reverse | owner, billing_ops |
| `/audit` | The operator action log | owner, support |

A screen a role may not open renders a "you do not have access" state that says why, not an empty
page. That is presentation: every endpoint behind a screen refuses the call on its own, and a `403`
that still arrives is rendered where the read failed.

Every screen that reads has its four states — loading (a skeleton that holds the layout), empty (says
what to do next), error (says what failed, offers a retry, carries the request reference) and
populated. Every action dialog disables itself while its request is out, keeps what was typed when it
fails, and puts a field's error on the field.

## Signing in

The flow is the operator contract in `libs/contracts/openapi/auth.json`; `src/auth/contract.test.ts`
holds the code to it.

1. `POST /auth/operator/login` with email and password. A correct password **never** returns a
   session: the answer is `mfa_required` or `mfa_enrollment_required`, each with a short-lived
   pre-auth token. Anything else is refused as a protocol violation rather than treated as success.
2. **`mfa_required`** — the six-digit code from the authenticator, or a recovery code (`POST
   /auth/operator/mfa/verify`).
3. **`mfa_enrollment_required`** — a first sign-in. The operator enters the single-use enrolment code
   an owner issued out of band (30-minute life), scans the QR code or types the secret into an
   authenticator, confirms with a code, and is shown their **recovery codes once**. The session
   starts only after they say they have saved them, and the token the enrolment earned is held until
   then.
4. Failures are told apart by status and problem type, never by the sentence, and the server's
   sentence is shown as it was written: a wrong code is a `401` and can be retried; a lockout is a
   `403` that ends the sign-in (the pre-auth session is revoked with it, so there is nothing to retry
   against); **`503` "re-seal pending"** (problem type `…/mfa-reseal-pending`, a factor the deployment
   cannot read yet) is not the operator's mistake and is not counted toward the lockout, so the
   screen says so and tells them to use a recovery code in the same field.

The pre-auth token and the token an enrolment earns live in refs — never in state, never in storage.
The access token is mirrored to `sessionStorage` under `opsgrids.operator.access_token` so a reload
within the tab stays signed in, and never to `localStorage`.

## Kept apart from the tenant apps

The operator audience is the security boundary (ADR-0003 §2: `aud: "operator"`, `stf: true`, MFA in
`amr`). The console enforces its side of it four ways, each pinned by a test:

- **Runtime.** `src/auth/operatorRequest.ts` is the only door to the network. It refuses any path
  that is not `/admin/…` or `/auth/operator/…` (including `..`, encoded dots, backslashes and
  protocol-relative paths), and it will not send a token that is not an operator's: a tenant token in
  the slot is treated as no session and the request is never made. A sign-in that returns a token for
  another audience is refused before it is stored. (`operatorRequest.test.ts`)
- **Storage.** Its own slots — `opsgrids.operator.access_token`, `opsgrids.operator.theme` — so no
  tenant app's session can be this one's. (`tools/frontend_manifest.py`)
- **Imports.** `eslint.config.js` bans the tenant half of `@opsgrids/client` (the tenant sign-in,
  session provider and grants hooks). The audience-neutral primitives stay open.
  (`src/isolation.test.ts`)
- **The reverse.** No tenant app may name `/auth/operator` or the operator storage slot; the same
  workspace tool fails on it.

None of this is authorization. The server checks the audience, the `stf` claim, MFA and the role on
every request; the console's checks make the wrong call impossible to send by accident and the
right screen the only one shown.

## Fixtures-first

`VITE_USE_FIXTURES` defaults on in a dev build, so the whole console renders from `src/api/fixtures/`
with no gateway and no sign-in while the console backend is being built (a stand-in operator is
supplied). Set `VITE_USE_FIXTURES=false` to call the documented `/api/v1/admin/*` routes. A
production build is always live — `USE_FIXTURES` is `import.meta.env.DEV && …`, so the fixtures are
not reachable from a build and are dead-code eliminated from it.

The live routes do not yet answer everything the screens draw. `src/api/contract.test.ts` holds each
live call to the committed OpenAPI documents and names, with its reason, every gap; the list is in
`docs/backlog.md` (org names and filters, the catalog read, override kinds beyond suspend and comp
grant, audit actors, invoices, metric history). Where the API answers less than a screen needs, the
screen says so instead of drawing an invented number.

## Commands

```bash
pnpm --filter @opsgrids/opsgrids-internal dev        # http://localhost:4330 (strict: it will not fall to another port)
pnpm --filter @opsgrids/opsgrids-internal preview    # http://localhost:4330 after a build
pnpm --filter @opsgrids/opsgrids-internal typecheck
pnpm --filter @opsgrids/opsgrids-internal lint       # includes the design-token rules
pnpm --filter @opsgrids/opsgrids-internal test
pnpm --filter @opsgrids/opsgrids-internal build
```

## Conventions specific to this app

- **No raw values.** `@opsgrids/config/eslint-tokens` refuses hex and colour functions, Tailwind
  arbitrary values (`w-[…]`), raw lengths and inline styles that set a real property, in this app's
  `src/`. A computed number goes in a custom property (`w-(--share)` with `style={{ "--share": … }}`).
  The mono face (IBM Plex Mono) is self-hosted from `@fontsource`; Manrope comes from
  `@opsgrids/ui/fonts`. Nothing is requested from a third party to render a page.
- **No string becomes script or markup, and no browser prompt stands in for a dialog.** Lint refuses
  `eval`, `new Function`, a string handed to a timer, `dangerouslySetInnerHTML`, `innerHTML` and its
  kin, `console.*`, and `alert` / `confirm` / `prompt` in this app's `src/` (tests included). A
  confirmation is a dialog on the page. `src/hygiene.test.ts` runs each of them through the real
  config, and pins that the token rules above still fire beside them.
- **Money is integer minor units** with an ISO currency, formatted in `src/lib/money.ts`; an amount
  typed into a dialog is parsed to minor units there and never held as a float.
- **A UI gate is not a gate.** `can(capability)` and `RequireCapability` hide controls the role
  cannot use; the gateway refuses the call regardless.
