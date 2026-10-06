# Edge environment reference

> **Superseded in part (2026-09-24).** Use [`ENV_SOURCE_MATRIX.md`](ENV_SOURCE_MATRIX.md) for the per-variable facts and
> [`ENV_SETUP_CHECKLIST.md`](ENV_SETUP_CHECKLIST.md) for the order to do things in. This file was written against older
> heads (its section 0) and is kept for its history. Where the two disagree, the matrix is right; it re-reads the heads
> (integration `5320848`, which contains every slice named below) and resolves every citation against it. What changed:
>
> 1. **Memory-headroom is merged** into integration (`ce35c77`, tree identical to the slice's `0ff2954`) and
>    so is io-outside-txn (`5320848`), which adds no key. "Pending: MH, OC, BR, IO, ECU" below is out of date: nothing is pending.
> 2. **The two preflight defects are fixed** by that merge: the Caddyfile catch-all row (so "no sensible `.env` makes
>    preflight pass" no longer holds) and the exit 127 on an unquoted space. They remain on `193b529` and earlier. The `.env`
>    pre-check now also refuses an unquoted shell metacharacter, text after a closing quote and CRLF; preflight and
>    `remote.sh` now both take the last assignment of a key (`remote.sh` still takes the first for `OPSGRIDS_DB_PASSWORD`
>    and `OPS_MONITOR_PORT`); an undefined profile name is a named FAIL.
> 3. **The ten-profile price is 4581 MB** (118.6% of 3862 MB, cap 4827), not 4112 MB. The RUNBOOK now also gives a
>    rule for reading the real box (`docker stats`, `vmstat`, scale `4581 x measured / 4044`) before writing a ceiling.
> 4. **The MFA reseal is fail-closed** (a count it cannot read aborts the deploy), runs once more after `health`, and during
>    the reseal-to-`up` window recovery codes fail too, not only authenticator codes.
> 5. **`COMPOSE_PROFILES=*` is valid** (expanded by preflight and `remote.sh`).
> 6. **Only preflight guards `RAZORPAY_WEBHOOK_SECRET`**: the payment service itself does not refuse the public dev default.
> 7. **`BILLING_TAX_JURISDICTION` is optional** and matched exactly (empty matches only NULL-jurisdiction rows), not "both or none".
> 8. **Inert settings:** `SENTRY_DSN` and `JWT_KID` are read by nothing that acts on them. (`RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` were on this list while the payment wire was a stub; since ADR-0032 they are used when `PAYMENT_WIRE=live`, and still do nothing by themselves: keys never switch a network call on.)
> 9. **The provider webhooks probably do not reach their services** through the edge (matrix section 9.1); settle it with the two
>    unauthenticated probes before configuring a callback URL.
> 10. **The RUNBOOK's key counts** ("19 new keys", "20 (18)") were corrected by `0ff2954` to 22 new keys, 20 of which must be present.
> 11. **Process-only variables** the deploy tooling reads (for example `FORCE_PULL`, `PLACEHOLDER`, `OPS_MONITOR_PORT`)
>     are listed in the matrix's own table; `document-cron`, `storefront-cron` and `operations-cron` no longer exist.
> 12. **The example's required secrets are empty, not `CHANGE_ME_...`.** The "placeholder" cells and the "placeholders in the example" note below describe a draft that carried `CHANGE_ME_generate_...` values. Those were removed:
>     the services accept them in production (measured: matrix section 11, item 20, run Q9), while an empty value fails closed in preflight and in the services.
> 13. **Other points the matrix corrects:** a value with `$`, a backtick or a backslash needs SINGLE quotes (double quotes do not protect them); `ENGAGE_PUBLIC_API_BASE` must end in `/api/v1`; `STORE_SLUG_CACHE_SECONDS` is the slug-lookup TTL,
>     not the custom-domain takedown window; `RAZORPAY_WEBHOOK_SECRET` is in the production ref's example (category D on this box); `OPS_ALERT_EMAIL` is empty in the example and is set by the checklist after the SMTP probe printed OK;
>     `EDGE_WEB_ROOT` and `PUBLIC_ASSET_BASE_URL` must be written bare (publish-web.sh reads them raw); `export`ed or indented profile lines are invisible to remote.sh. See matrix sections 3, 6.4 and 11.

What every variable the edge deploy reads is, where its value comes from, how to make it, and
what is still undecided. It describes the **final release**: `release/backlog-complete` plus the
slices that are still pending. Nothing in it is a guess; where the repository does not say, the
row says "unknown" and section 10 lists it.

**Regenerate this file when the slice branches move** (section 11). It was read at the heads below,
while several of the branches moved under the audit (integration advanced three times and
memory-headroom was pushed).

- No production value of any kind is in this repository, in this file, or in `.env.example`.
- Nothing here changes a production secret store or a deployment credential. The commands in section
  7 are for the operator to run on the box; the audit itself never touched the VM.
- The paired file is `infrastructure/edge/.env.example`, annotated key by key from the same data.

## 0. Provenance

Snapshot: 2026-09-23 23:10 UTC (2026-09-24 IST), after `git fetch origin`. Abbreviations are used in
the citations below (`branch:file:line`).

| Abbrev | Ref | Head read | Role |
|---|---|---|---|
| INT | `origin/release/backlog-complete` | `17061356e565dc1b75838f8e13a27880e1e19c75` | integration; already contains ops-monitor (`50756f7`), domain-verification, crm-leads (`21328c9` and its ops-monitor follow-up `6c3196f`) and the ADR decisions merge |
| MH | `origin/slice/memory-headroom` | `c159a3ec65905a630bc502ed9ce32e1691885e80` | `EDGE_PROFILES`, `EDGE_BUDGET_ACCEPTED_MB`, post-deploy memory pricing; pushed during the audit |
| OC | `origin/slice/operator-console-backend` | `94e412b0b1a32911534fe8b01900365e60fa5451` | `ADMIN_GATEWAY_HOP_KEY`, the deploy-time MFA reseal (`lib/mfa-reseal.sh`), preflight secret-strength check, auth merge migration `f8e432a62a84`; first read at `5f89e27`, which had a manual reseal step and a compose `:?` that this head replaced |
| BR | `origin/slice/billing-run` | `07ea4e219026f4f829d80772c8f98dca1c2b4177` | `BILLING_TAX_*`, `BILLING_RUN_ENABLED`, `BILLING_RUN_NOT_BEFORE`; the last was an unpushed local commit (`db0397b`) until late in the audit |
| IO | `origin/slice/io-outside-txn` | `210857348f7db2d732eeab8243a60c8e01535a07` | no env change |
| ECU | `origin/slice/engage-campaigns-ui` | `c47f976627552cb17b3f38b94a6ecec2e1c10deb` | no env change |
| OPM | `origin/slice/ops-monitor` | `9bb0eff3f03b67ad2c809c26b7e824f3ffa6c9b2` | `OPS_*`, `SMTP_USE_SSL`; merged into INT |
| DV | `origin/slice/domain-verification` | `213ebdb93210631ee6e6cd5ff0ec287a83b3a00d` | no env change; merged into INT |
| main | `origin/main` | `e3c525ab9024738f49cd84c8e12b9088168284cb` | baseline: 46 keys in the edge example |

Pending (not yet in INT): MH, OC, BR, IO, ECU. Every branch above is on origin; no local-only head that
matters remained at the end of the audit.

How the final release was read: one scratch tree outside the repository (never committed or pushed) =
INT + the origin heads of MH, OC, BR, IO and ECU. Textual conflicts were resolved by taking the union of the
infrastructure files and INT's side of service code, which only matters for the test artefact flagged in
section 9. Every preflight run in section 8 is against that tree. Because the branches kept moving, the tree
was rebuilt several times; every number below is from the last build.

Key count: main 46, INT 61, final release **66** (`ADMIN_GATEWAY_HOP_KEY`, `BILLING_TAX_CODE`,
`BILLING_TAX_JURISDICTION`, `EDGE_PROFILES`, `EDGE_BUDGET_ACCEPTED_MB` added on top of INT). From main
the eleven document/MinIO keys (ADR-0009) are new as well: `ASSET_PURGE_RETENTION_DAYS`,
`DOCUMENT_ORPHAN_SWEEP_DRY_RUN`, `DOCUMENT_ORPHAN_SWEEP_ENABLED`, `DOCUMENT_S3_ACCESS_KEY`,
`DOCUMENT_S3_ORPHAN_ACCESS_KEY`, `DOCUMENT_S3_ORPHAN_SECRET_KEY`, `DOCUMENT_S3_SECRET_KEY`,
`MINIO_ROOT_PASSWORD`, `MINIO_ROOT_USER`, `PUBLIC_ASSET_BASE_URL`, `S3_PUBLIC_BUCKET`.

## 1. The short version

1. **How "required" is decided.** Every uncommented `KEY=` line in `.env.example` must be present in
   the box's `.env`, non-empty unless the key is in preflight's `optional_empty` list, and not a
   placeholder (`INT:infrastructure/edge/preflight.sh:37,42-64,73-101`). There are 66 such keys; 26
   may be empty, 40 must carry a value.
2. **No sensible `.env` makes preflight pass today.** One check fails on every branch that contains
   commit `ffaa682` whatever the values are (section 3). It needs a one-token code fix.
3. **New secrets to generate for this release:** `ADMIN_GATEWAY_HOP_KEY`, `MINIO_ROOT_USER`,
   `MINIO_ROOT_PASSWORD`, `DOCUMENT_S3_ACCESS_KEY`, `DOCUMENT_S3_SECRET_KEY`, and (recommended)
   `DOCUMENT_S3_ORPHAN_ACCESS_KEY` / `DOCUMENT_S3_ORPHAN_SECRET_KEY`. All with `openssl rand`.
4. **Values only an outside party can give you:** the SMTP app password, the off-box backup bucket and
   its credentials, Razorpay keys, Meta (WhatsApp) values, the box's real reserved IP.
5. **`OPS_ALERT_EMAIL`** takes two addresses as `info@opsgrids.com,zionna@opsgrids.com` (comma, no
   space). The literal `... and ...` breaks preflight outright (section 5.4).
6. **`MFA_ACCEPT_LEGACY_PLAINTEXT` is not an `.env` variable.** Secure value: unset. On the current OC head the
   deploy itself sets it on one one-off container, between `migrate` and `up`; there is nothing to run by hand
   (section 5.2).
7. **`EDGE_BUDGET_ACCEPTED_MB` is a decision, not a value to look up.** Declaring all ten profiles
   prices at **4112 MB against a 3862 MB box** on the final release (106%), which preflight refuses
   unless the owner sets the ceiling (section 5.3, section 6 E).

## 2. How preflight decides

Source: `INT:infrastructure/edge/preflight.sh` (slice additions cited where they apply). The script
takes no arguments, has no dry-run mode, `cd`s to `infrastructure/edge/` and reads:

- `.env.example` for the **key names** (`:37`), and `.env` for the values, by **sourcing it as bash**
  (`set -a; . ./.env; set +a`, `:66-69`, under `set -euo pipefail`, `:9`). Consequences, all
  reproduced in section 8: a value with an unquoted space (`EDGE_PROFILES=inventory, storefront`,
  `OPS_ALERT_EMAIL=a and b`) runs its tail as a command, exits 127 with `command not found` and
  **no `preflight: FAILED` footer**; `$`, `;`, `&` and `#` need quoting; docker compose parses the
  same file with a different parser.
- Presence uses the shell variable after sourcing (`${!key-__unset__}`, `:74`), so a variable exported
  in the calling shell masks a missing line. Over ssh nothing is forwarded, so on the box it cannot.

Per key, in order: **unset** (line absent) is *missing*, even for an optional-empty key (`:74-78`);
**present but empty** is *missing* unless the key is in `optional_empty` (`:79-87`); a **non-empty
value** matching `replace-me* | CHANGE_ME* | dev-only-* | ci-only-* | *example.com*` is a *placeholder*
(`:89`, glob, anywhere in the string for `*example.com*`). Failures name keys, never values
(`:99,101`). Two keys may also be **absent** entirely: `optional_absent="EDGE_PROFILES
EDGE_BUDGET_ACCEPTED_MB"` (`MH:preflight.sh:70,108`).

The 26 keys allowed to be empty in the final release (`INT:42-64` including the ops-monitor additions,
`MH:preflight.sh:59,62`, the billing tax keys from `BR`): `BACKUP_S3_ACCESS_KEY_ID BACKUP_S3_ENDPOINT BACKUP_S3_SECRET_ACCESS_KEY
BILLING_TAX_CODE BILLING_TAX_JURISDICTION COMPOSE_PROFILES DOCUMENT_S3_ORPHAN_ACCESS_KEY
DOCUMENT_S3_ORPHAN_SECRET_KEY EDGE_BUDGET_ACCEPTED_MB EDGE_PROFILES EDGE_RESERVED_IP OPS_ALERT_EMAIL
OPS_MONITOR_API_CONTAINERS OPS_MONITOR_DLQ_QUEUES PII_DECRYPT_KEYS RAZORPAY_KEY_ID RAZORPAY_KEY_SECRET
SENTRY_DSN SMTP_HOST SMTP_PASSWORD SMTP_USERNAME SMTP_USE_SSL WHATSAPP_ACCESS_TOKEN
WHATSAPP_APP_SECRET WHATSAPP_PHONE_NUMBER_ID WHATSAPP_VERIFY_TOKEN`.

What else it checks (FAIL = exit 1 at the end, warn/info never affect the exit status):

| Check | Severity | Where |
|---|---|---|
| `.env` exists, mode 600 or 400 | FAIL | `INT:23-33` |
| both `BACKUP_S3_*` credentials empty / exactly one set | WARN / FAIL | `INT:118-123` |
| `MINIO_ROOT_PASSWORD`, `DOCUMENT_S3_SECRET_KEY` shorter than 8 chars | FAIL | `INT:132-139` |
| `PUBLIC_ASSET_BASE_URL` != `https://$EDGE_HOST/$S3_PUBLIC_BUCKET` | FAIL | `INT:148-152` |
| signing key file missing or mode not 400/440/600/640 | FAIL | `INT:155-162` |
| free disk < 5 GB; inode use >= 90%; docker daemon not responding | FAIL | `INT:167-179` |
| `EDGE_RESERVED_IP` in an RFC 5737 range | WARN | `INT:109-116` |
| memory: post-deploy price vs box `MemTotal` (>= hard line, no ceiling) | FAIL | `MH:preflight.sh:244-300` |
| `EDGE_BUDGET_ACCEPTED_MB` not a whole number | FAIL | `MH:preflight.sh:261` |
| `EMAIL_TRANSPORT` unset/`log`; both payment wires on the stub (no provider is called); `BILLING_RUN_ENABLED` not `true` | WARN | `INT:223-`, `BR` |
| `BILLING_RUN_ENABLED=true` with `BILLING_RUN_NOT_BEFORE` empty / not ISO-with-offset | FAIL | `BR:preflight.sh` (07ea4e2) |
| `OPS_ALERT_EMAIL` or `SMTP_HOST` empty | WARN | `INT:239-244` |
| `ENGAGE_PUBLIC_API_BASE` not https, or containing `localhost` / `127.0.0.1` | FAIL | `INT:273-` |
| `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY` or `ADMIN_GATEWAY_HOP_KEY` shorter than 32 **bytes**; hop key equal to the internal token | FAIL | `OC:preflight.sh:98-100`, `OC:lib/secret-strength.sh` |
| every `https://<name> {` Caddyfile site listed in `TLS_PLATFORM_HOSTS` | FAIL | `INT:290` (see section 3) |
| upstreams in `routes.prod.json` / Caddyfile not run by the declared profiles | WARN | `INT:295-` |
| catalogue / activation agreement with the declared profiles (only if Postgres is running) | FAIL / WARN | `MH:preflight.sh` |

**Not validated by preflight anywhere:** entropy of any secret, or the length of any secret other than the two
MinIO values and (on OC) the three above (the *services* enforce >= 32 bytes and refuse `dev-only-`/`ci-only-`
in production: `OC:libs/platform-core/src/platform_core/config.py:240-284`), the value of `EMAIL_TRANSPORT`, `SMTP_PORT`,
the `SMTP_USE_TLS` / `SMTP_USE_SSL` pair, `OPS_ALERT_EMAIL`'s shape, profile spelling, `JWT_KID`,
`LOG_LEVEL`, `CORS_ORIGINS`, `APP_BASE_URL`.

Two behaviours worth knowing before you debug a run:

- A missing docker CLI, or a compose file that cannot render with the current `.env`, aborts preflight
  **silently** at the `docker compose ... config --services` line (`INT:295`, under `set -e` and
  `pipefail`): the checks above it print, everything after it never runs. OC's first head made an empty
  `ADMIN_GATEWAY_HOP_KEY` do exactly that through a compose `:?`; `94e412b` relaxed it to `:-` so an
  empty key is now an ordinary preflight FAIL (reproduced, section 8).
- `remote.sh` does not source `.env`; it reads single keys with `sed` and strips quotes and spaces
  (`MH:remote.sh:55-56`). preflight and remote.sh can therefore disagree about the same line.

## 3. A defect no environment value can fix

`INT:preflight.sh:290` is `done < <(sed -n 's#^https://\([a-z0-9.-]*\) {$#\1#p' caddy/Caddyfile)`.
`caddy/Caddyfile` contains a catch-all site block `https:// {` (line 429). The pattern's
`[a-z0-9.-]*` matches the empty string, so the loop reads an **empty site name**, and
`case ",$TLS_PLATFORM_HOSTS," in *",,"*` never matches: every run prints
`FAIL the Caddyfile serves  but TLS_PLATFORM_HOSTS does not list it` and exits 1.

- Introduced by commit `ffaa682` (2026-09-23, "Pin the backup's aws-cli image and clear every
  shellcheck ... finding"), which turned `for site in $(sed ...)` (word-splitting dropped the empty
  line; still the code on `main`, `main:preflight.sh:152`) into `while read -r site`.
- Reproduced on INT and on the final union with a fully valid synthetic `.env` (section 8): it is the
  only env-independent FAIL. Fixed in a scratch copy only by changing `*` to `\{1,\}` in the pattern
  (`[a-z0-9.-]\{1,\}`, works in GNU and BSD sed): the row disappears and the run is then clean apart
  from this laptop's disk and inode figures.
- Effect: `make deploy` refuses at its first step on any box running INT's preflight, and the Deploy
  workflow would fail the same way. **This audit did not change `preflight.sh`** (it is edited by
  several pending slices); it needs a code change by the owner of that file.

## 4. Every variable

Columns: required/optional, secret, exact format, source, whether it must be generated, accepted
values, default, consumer, whether a production value exists somewhere, dependencies. **"Exists in
repo / deploy config" only ever says**: *no - placeholder only*, *committed non-secret default*,
*dev-only value in untracked .env (not printed)*, or *generated on the box per RUNBOOK section X* -
never a value. The box's own `.env` was not read: the VM was not touched. `since` (in the consumer
column) is the slice or release that introduced the key.

### 4.1 Identity and TLS
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `EDGE_HOST` | required | no | bare hostname, no scheme or path. `make deploy CONFIRM=<this>` must equal it exactly (remote.sh assert-host). PUBLIC_ASSET_BASE_URL below must be built from it. | CHOOSE (this box's DNS name; committed default is fine) | no | any hostname; must equal `make deploy CONFIRM=` | `edge.opsgrids.com` | caddy, storefront, ops-monitor (certificate probe SNI), preflight.sh, remote.sh (since: main) | committed non-secret default | PUBLIC_ASSET_BASE_URL, TLS_PLATFORM_HOSTS, DNS |
| `EDGE_RESERVED_IP` | optional-empty | no | IPv4 dotted quad, or empty. An RFC 5737 documentation address (192.0.2.*, 198.51.100.*, 203.0.113.*) only WARNS in preflight. | EXTERNAL (the VM's real reserved/floating IPv4 from the cloud provider) | no | empty or a real IPv4 (RFC 5737 ranges warn) | empty | storefront (edge_reserved_ip) (since: main) | no - placeholder only | merchant DNS records |
| `EDGE_WEB_ROOT` | required | no | absolute host path. Buyer bundle releases live in `<path>/releases/<utc-ts>/` with a `current` symlink swapped by `make edge-web`. | CHOOSE (host directory; default is fine) | no | any absolute path | `/srv/opsgrids/web` | caddy (bind mount), publish-web.sh, preflight.sh (bundle warning) (since: main) | committed non-secret default | `make edge-web` / publish-web.sh |
| `ACME_EMAIL` | required, and must NOT contain `example.com` (preflight placeholder rule) | no | email address. The committed value ops@example.com is a placeholder that preflight REFUSES; replace it. | CHOOSE (a real inbox a human reads) | no | any address not containing `example.com` | `ops@example.com` | caddy (ACME account contact) (since: main) | committed non-secret default | none |
| `TLS_PLATFORM_HOSTS` | required | no | comma-separated exact lowercase hostnames, no spaces, no wildcard. preflight FAILs for any `https://<name> {` site block in caddy/Caddyfile that is not listed here. | CHOOSE (every platform name in DNS; committed default) | no | must list app, ops, people, revenue, pulse, projects, support, marketing, finance, it, store, engage `.opsgrids.com` (the 12 named Caddyfile sites); the committed default also lists edge and api | `edge.opsgrids.com,api.opsgrids.com,app.opsgrids.com,ops.opsgrids.com,people.opsgrids.com,revenue.opsgrids.com,pulse.opsgrids.com,projects.opsgrids.com,support.opsgrids.com,marketing.opsgrids.com,finance.opsgrids.com,it.opsgrids.com,store.opsgrids.com,engage.opsgrids.com` | storefront (tls-authorize issuance gate), preflight.sh (since: main) | committed non-secret default | caddy/Caddyfile site list; storefront tls-authorize |


### 4.2 Database, auth and broker
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `POSTGRES_PASSWORD` | required | YES | any string; hex recommended. preflight checks presence and placeholders only. | GENERATE on the box | YES: `openssl rand -hex 32` | any non-placeholder string | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__postgres_password` in the example) | postgres (superuser `opsgrids`; backup.sh and runbook psql sessions) (since: main) | generated on the box per RUNBOOK 'Secrets' (step 5) | postgres |
| `OPSGRIDS_DB_PASSWORD` | required | YES | hex or alphanumerics ONLY: it is substituted into a database URL, so `@ / : ?` break it. | GENERATE on the box | YES: `openssl rand -hex 32` | hex / alphanumerics | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__opsgrids_db_password` in the example) | every service (x-service-env -> DATABASE_URL), postgres-init role passwords (since: main) | generated on the box per RUNBOOK 'Secrets' (step 5) | postgres-init role passwords |
| `INTERNAL_SERVICE_TOKEN` | required | YES | >= 32 UTF-8 bytes; must not start `dev-only-` or `ci-only-`; must differ from ADMIN_GATEWAY_HOP_KEY (platform_core/config.py, services/auth/app/config.py). Every service refuses to start in production otherwise; preflight checks the 32 bytes and the inequality first (lib/secret-strength.sh). | GENERATE on the box | YES: `openssl rand -hex 32` | >= 32 bytes, no dev-only-/ci-only- prefix | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__internal_service_token` in the example) | every service (x-service-env) (since: main) | generated on the box per RUNBOOK 'Secrets' (step 5); dev-only value in untracked .env (not printed) | must differ from ADMIN_GATEWAY_HOP_KEY |
| `ADMIN_GATEWAY_HOP_KEY` | required (preflight refuses it empty; auth refuses to start without it in production) | YES | >= 32 UTF-8 bytes; must not start `dev-only-` or `ci-only-`; must NOT equal INTERNAL_SERVICE_TOKEN. Preflight enforces presence, >= 32 bytes and inequality (lib/secret-strength.sh); compose passes it as `${...:-}` on purpose. Used verbatim as the HMAC-SHA256 key (not hex/base64-decoded), so 64 hex chars = 256 bits is the right shape. | GENERATE on the box | YES: `openssl rand -hex 32 (a separate invocation from INTERNAL_SERVICE_TOKEN)` | >= 32 bytes, no dev-only-/ci-only- prefix, != INTERNAL_SERVICE_TOKEN | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__admin_gateway_hop_key` in the example) | auth ONLY (compose.prod.yml auth block; admin-gateway, the signer, is not deployed here) (since: slice/operator-console-backend) | generated on the box per RUNBOOK 'Secrets' (step 5) on slice/operator-console-backend | INTERNAL_SERVICE_TOKEN (must differ); ENVIRONMENT=production (compose-fixed); preflight is the gate (the compose default is empty on purpose) |
| `PII_ENCRYPTION_KEY` | required | YES | passphrase, >= 32 UTF-8 bytes (checked by preflight), no `dev-only-`/`ci-only-` prefix; HKDF-SHA256 turns it into an AES-256-GCM key. Back it up OFF the box. NEVER regenerate on a box that already holds sealed data (rotate through PII_DECRYPT_KEYS instead). | GENERATE on the box, ONCE, before any data exists | YES: `openssl rand -hex 32` | >= 32 bytes, no dev-only-/ci-only- prefix | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__pii_encryption_key` in the example) | every service (x-service-env); auth also seals MFA TOTP secrets with it (since: main) | generated on the box per RUNBOOK 'Secrets' (step 5); dev-only value in untracked .env (not printed) | PII_DECRYPT_KEYS on rotation; the deploy-time MFA reseal seals under it |
| `PII_DECRYPT_KEYS` | optional-empty | YES | comma-separated old passphrases; an entry must not contain a comma (hex never does). Empty = no rotation in progress. | DERIVED (the previous PII_ENCRYPTION_KEY value(s), only while rotating) | no | empty, or comma-separated old passphrases | empty | every service (x-service-env) (since: main) | no - placeholder only; dev-only value in untracked .env (not printed) | PII_ENCRYPTION_KEY |
| `JWT_PRIVATE_KEY_HOST_PATH` | required; the FILE must exist | path: no / FILE: YES | absolute host path to an UNENCRYPTED PKCS8 Ed25519 PEM. preflight FAILs unless the file exists with mode 400, 440, 600 or 640. The auth container's uid must be able to read it (RUNBOOK 'Secrets'). | GENERATE the file on the box | YES: `openssl genpkey -algorithm ed25519 -out /srv/opsgrids/secrets/jwt-ed25519.pem && chmod 600 /srv/opsgrids/secrets/jwt-ed25519.pem` | path to an Ed25519 PKCS8 PEM, mode 400/440/600/640 | `/srv/opsgrids/secrets/jwt-ed25519.pem` | auth ONLY (read-only bind mount) - the only private signing key in the platform (since: main) | generated on the box per RUNBOOK 'Secrets' (step 5) | JWT_ISSUER; auth container uid must read the file |
| `JWT_KID` | required (present and non-empty for preflight/compose) | no | free string. NOTE: nothing reads it - auth derives the real `kid` from the public key's thumbprint (services/auth/app/keys.py), so this value is inert today. | CHOOSE | no | any string | `prod-1` | interpolated into every service's environment (x-service-env); no code path reads it (since: main) | committed non-secret default; dev-only value in untracked .env (not printed) | none (inert) |
| `JWT_ISSUER` | required | no | free string used as the JWT `iss` claim (an https URL by convention); identical for every service. Changing it invalidates every live token. | CHOOSE | no | any string; keep stable | `https://auth.opsgrids.com` | every service (x-service-env); auth stamps `iss` (since: main) | committed non-secret default; dev-only value in untracked .env (not printed) | same value in every service |
| `RABBITMQ_PASSWORD` | required | YES | any string; hex recommended (it is embedded in the broker URL). | GENERATE on the box | YES: `openssl rand -hex 32` | any non-placeholder string | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__rabbitmq_password` in the example) | rabbitmq, every service's broker URL (x-service-env), ops-monitor DLQ probe (since: main) | no - placeholder only | rabbitmq volume; ops-monitor DLQ probe |


### 4.3 Products and memory
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `COMPOSE_PROFILES` | optional-empty (empty = core only) | no | comma-separated lowercase compose profile names, NO spaces, no `*`: inventory, storefront, hrms, crm, pulseboard, projects, support, marketing, finance, itsm. A non-empty EDGE_PROFILES REPLACES this (it does not add to it). Unknown names are silently ignored. | CHOOSE (which products this box sells) | no | empty, or any subset of: inventory, storefront, hrms, crm, pulseboard, projects, support, marketing, finance, itsm | empty | docker compose (every call), preflight.sh, remote.sh, ops-monitor (derives its check lists) (since: main (default became EMPTY in slice/memory-headroom; integration shipped all ten)) | committed default is empty; ADR-0013 records that the running box's `.env` lists all ten profiles (read-only look during review; not re-verified here) | EDGE_PROFILES (overrides); catalogue rows; memory-budget.json; ops-monitor derivation |
| `EDGE_PROFILES` | optional-empty, and the line may be ABSENT entirely (preflight optional_absent) | no | same syntax as COMPOSE_PROFILES (comma-separated, no spaces). Empty = use COMPOSE_PROFILES. No validator exists: a typo is silently ignored. | CHOOSE (an explicit override) | no | empty, or the same ten names as COMPOSE_PROFILES | empty | preflight.sh and remote.sh ONLY (exported to compose as COMPOSE_PROFILES); no container reads it (since: slice/memory-headroom (origin c159a3e; not yet merged into release/backlog-complete)) | no - placeholder only; ADR-0013 records that the running box has no EDGE_PROFILES | COMPOSE_PROFILES; postgres running (for the catalogue checks); memory-budget.json |
| `EDGE_BUDGET_ACCEPTED_MB` | optional (empty/absent = no ceiling accepted) | no | whole number of megabytes, digits only (`^[0-9]+$`): no unit, no decimal, no comma. Same unit as preflight's `<n>M` (sum of memory-budget.json expected_rss_mb vs MemTotal/1024). Anything else FAILs preflight. Only ever rescues an over-hard-line `refuse` verdict. | OWNER DECISION, read off preflight's own printed projection | no | empty, or a non-negative integer | empty | preflight.sh ONLY; no container reads it (since: slice/memory-headroom (origin c159a3e; not yet merged into release/backlog-complete)) | no - placeholder only (the key did not exist at ADR-0013's read-only look, which found no edge-budget.last-ok on the box) | declared profiles, running containers, memory-budget.json, the box's MemTotal |


### 4.4 Email
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `EMAIL_TRANSPORT` | required | no | exactly `log` or `smtp` (case-sensitive). Any other value crash-loops notification-worker; preflight does NOT validate it (it only warns on `log`). | CHOOSE (`smtp` once SMTP credentials exist) | no | `log` \| `smtp` | `log` | notification, notification-worker (since: main) | committed non-secret default | SMTP_* when `smtp` |
| `EMAIL_FROM` | required | no | email address, used as the From header of platform mail; no format validation in code. | CHOOSE (an address the SMTP account may send as) | no | any address the SMTP account may send as | `info@opsgrids.com` | notification, notification-worker, ops-monitor (since: main) | committed non-secret default | SMTP_USERNAME/SMTP_HOST |
| `SMTP_HOST` | optional-empty (but see format) | no | hostname. An EMPTY value is treated as unset and notification silently falls back to `localhost` - never leave it empty when EMAIL_TRANSPORT=smtp. | EXTERNAL (the mail provider) | no | any hostname | `smtp.gmail.com` | notification, notification-worker, ops-monitor (since: main) | committed non-secret default; dev-only value in untracked .env (not printed) | EMAIL_TRANSPORT; OPS_ALERT_EMAIL (ops-monitor needs it) |
| `SMTP_PORT` | required | no | integer. 587 with SMTP_USE_TLS=true (STARTTLS). 465 (implicit TLS) works for ops-monitor only, with SMTP_USE_SSL=true and SMTP_USE_TLS=false. | EXTERNAL (the provider's submission port) | no | integer | `587` | notification, notification-worker, ops-monitor (since: main) | committed non-secret default; dev-only value in untracked .env (not printed) | SMTP_USE_TLS / SMTP_USE_SSL |
| `SMTP_USERNAME` | optional-empty (needed by any relay that authenticates, Gmail does) | no | string, normally the full mailbox address. | EXTERNAL (the mailbox that owns the app password) | no | any string | `info@opsgrids.com` | notification, notification-worker, ops-monitor (since: main) | committed non-secret default | SMTP_PASSWORD; an encrypted connection |
| `SMTP_PASSWORD` | optional-empty (needed with SMTP_USERNAME) | YES | opaque string; wrap it in double quotes if it contains spaces - preflight `source`s this file as bash. With a username set and no password every ops-monitor alert send fails. | EXTERNAL (Google Workspace app password for that mailbox; not generated here) | no | any string (quote if it has spaces) | empty | notification, notification-worker, ops-monitor (since: main) | no - placeholder only; dev-only value in untracked .env (not printed) | SMTP_USERNAME; SMTP_USE_TLS or SMTP_USE_SSL |
| `SMTP_USE_TLS` | required | no | boolean (true/false; pydantic also accepts 1/0/yes/no/on/off). STARTTLS on the plain port. Mutually exclusive with SMTP_USE_SSL. One shared key for notification AND ops-monitor. | CHOOSE (dictated by the provider's port) | no | true \| false (pydantic bool) | `true` | notification, notification-worker, ops-monitor (since: main) | committed non-secret default; dev-only value in untracked .env (not printed) | SMTP_USE_SSL must then be false |
| `SMTP_USE_SSL` | optional-empty (line must exist) | no | boolean. Implicit TLS (conventionally port 465). Never true together with SMTP_USE_TLS=true: the mailer raises at SEND time (not at startup, not in preflight). | CHOOSE (true only for an SMTPS-only provider) | no | true \| false (pydantic bool) | `false` | ops-monitor ONLY (notification has no implicit-TLS path) (since: release/backlog-complete (ops-monitor, merged at 50756f7)) | committed non-secret default | SMTP_USE_TLS must be false when true; SMTP_PORT |


### 4.5 Payments and billing
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `RAZORPAY_KEY_ID` | optional-empty (empty = the stub; used only when `PAYMENT_WIRE=live`) | no | a Razorpay key id: `rzp_test_...` or `rzp_live_...`; both-or-neither with the secret. | EXTERNAL (Razorpay dashboard) | no | any string or empty | empty | payment, payment-worker (since: main) | no - placeholder only | RAZORPAY_KEY_SECRET |
| `RAZORPAY_KEY_SECRET` | optional-empty (set together with the key id; used only when `PAYMENT_WIRE=live`) | YES | Razorpay key secret string (a `SecretStr`; never logged). | EXTERNAL (Razorpay dashboard) | no | any string or empty | empty | payment, payment-worker (since: main) | no - placeholder only | RAZORPAY_KEY_ID |
| `RAZORPAY_WEBHOOK_SECRET` | required | YES | any string; production refuses the development default. | GENERATE on the box, then paste the same value into Razorpay's webhook settings | YES: `openssl rand -hex 32` | any non-placeholder string | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__razorpay_webhook_secret` in the example) | payment, payment-worker (since: main) | no - placeholder only | Razorpay webhook settings |
| `PAYMENT_PROVIDER` | optional; COMMENTED in the example (not a required key) | no | `razorpay` or `cashfree`; an unknown value stops `payment` starting. The provider NEW platform charges and checkouts go to; existing rows keep theirs. | OWNER DECISION | no | `razorpay`, `cashfree` | `razorpay` | payment, payment-worker (since: ADR-0032) | no - placeholder only | `PAYMENT_WIRE`, `CASHFREE_ENV` |
| `PAYMENT_WIRE` | optional; COMMENTED in the example | no | `stub` or `live`. `live` = customer-present checkout against Razorpay; needs both Razorpay keys; `rzp_test_` keys are refused on this production box unless `PAYMENT_ALLOW_TEST_KEYS=true` (staging only). | OWNER DECISION | no | `stub`, `live` | `stub` | payment, payment-worker (since: ADR-0032) | no - placeholder only | `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` |
| `PAYMENT_ALLOW_LIVE_KEYS` | optional; COMMENTED in the example | no | `true` or `false`. `true` lets `rzp_live_` keys and `CASHFREE_ENV=production` start. | OWNER DECISION | no | `true`, `false` | `false` | payment, payment-worker (since: ADR-0032) | no - placeholder only | `PAYMENT_WIRE`, `CASHFREE_ENV` |
| `PAYMENT_ALLOW_TEST_KEYS` | optional, never required; COMMENTED in the example. **Staging boxes only** | no | `true` or `false` (a pydantic boolean). `true` lets `rzp_test_` keys with `PAYMENT_WIRE=live` and `CASHFREE_ENV=sandbox` start on this production-labelled box; refused beside `PAYMENT_ALLOW_LIVE_KEYS=true`, any live-shaped key or `CASHFREE_ENV=production`. While on, `payment` logs a start-up WARNING, preflight warns and checkouts answer `test_mode: true`. | OWNER DECISION (2026-10-06: the edge box has no real paying customers) | no | `true`, `false` | `false` | payment, payment-worker (since: ADR-0032, staging test mode) | no - placeholder only | `PAYMENT_WIRE`, `CASHFREE_ENV`, `PAYMENT_ALLOW_LIVE_KEYS` |
| `CASHFREE_ENV` | optional; COMMENTED in the example | no | `stub`, `sandbox` or `production`. `stub` = in-process fake, no network, whatever keys are present. `sandbox` is refused on this production box unless `PAYMENT_ALLOW_TEST_KEYS=true` (staging only). | OWNER DECISION | no | `stub`, `sandbox`, `production` | `stub` | payment, payment-worker (since: ADR-0032) | no - placeholder only | `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY` |
| `CASHFREE_APP_ID` | optional-empty; COMMENTED in the example | YES (held as a SecretStr) | Cashfree's App ID (`x-client-id`); both-or-neither with the secret. A sandbox App ID starts with `TEST`. | EXTERNAL (Cashfree dashboard) | no | any string or empty | empty | payment, payment-worker (since: ADR-0032) | no - placeholder only | `CASHFREE_SECRET_KEY` |
| `CASHFREE_SECRET_KEY` | optional-empty; COMMENTED in the example | YES | Cashfree's Secret Key. It also SIGNS Cashfree's webhooks (no separate webhook secret): rotating it is a flag day for the API calls and the deliveries together. | EXTERNAL (Cashfree dashboard) | no | any string or empty | empty | payment, payment-worker (since: ADR-0032) | no - placeholder only | `CASHFREE_APP_ID` |
| `BILLING_TAX_CODE` | optional-empty (line must exist) | no | the `code` of an existing billing.tax_rates row, e.g. gst. Set without a rate row in force and the run refuses to invoice. Empty = no tax line. | CHOOSE (only once the deployment is tax-registered) | no | empty or an existing billing.tax_rates code | empty | billing (since: slice/billing-run) | no - placeholder only | BILLING_TAX_JURISDICTION; a tax_rates row in Postgres |
| `BILLING_TAX_JURISDICTION` | optional-empty (line must exist) | no | free string, e.g. IN. Both empty or both set. | CHOOSE (set together with BILLING_TAX_CODE) | no | empty or a jurisdiction string | empty | billing (since: slice/billing-run) | no - placeholder only | BILLING_TAX_CODE |


### 4.6 WhatsApp and Engage
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `WHATSAPP_TRANSPORT` | required | no | exactly `log` or `whatsapp_cloud`. `whatsapp_cloud` without PHONE_NUMBER_ID and ACCESS_TOKEN refuses to start notification-worker. | CHOOSE (`log` until a Meta app exists) | no | `log` \| `whatsapp_cloud` | `log` | notification-worker (since: main) | committed non-secret default | WHATSAPP_PHONE_NUMBER_ID + WHATSAPP_ACCESS_TOKEN when `whatsapp_cloud` |
| `WHATSAPP_APP_SECRET` | optional-empty (needed to accept inbound webhooks) | YES | string; HMAC key over the raw webhook body. | EXTERNAL (the platform's Meta app) | no | any string or empty | empty | notification, notification-worker (since: main) | no - placeholder only | a Meta app |
| `WHATSAPP_VERIFY_TOKEN` | optional-empty (needed for Meta's subscription handshake) | YES | non-empty ASCII string (compared with hmac.compare_digest as str). | GENERATE (you choose it, then paste it into the Meta app's webhook settings) | YES: `openssl rand -hex 32` | non-empty ASCII string or empty | empty | notification, notification-worker (since: main) | no - placeholder only | the Meta app's webhook settings |
| `WHATSAPP_PHONE_NUMBER_ID` | optional-empty (required iff WHATSAPP_TRANSPORT=whatsapp_cloud) | no | string id; no validation. | EXTERNAL (Meta WhatsApp Business) | no | any string or empty | empty | notification, notification-worker (since: main) | no - placeholder only | WHATSAPP_ACCESS_TOKEN |
| `WHATSAPP_ACCESS_TOKEN` | optional-empty (required iff WHATSAPP_TRANSPORT=whatsapp_cloud) | YES | string; no validation. | EXTERNAL (Meta) | no | any string or empty | empty | notification, notification-worker (since: main) | no - placeholder only | WHATSAPP_PHONE_NUMBER_ID |
| `ENGAGE_UNSUBSCRIBE_SECRET` | required | YES | any string not starting `dev-only-`/`ci-only-` (HMAC-SHA256 key). NO length floor in code; 64 hex chars is the recommendation. In production the placeholder disables campaign sending at the feature level rather than stopping the process. Rotating invalidates every unsubscribe link already sent. | GENERATE on the box | YES: `openssl rand -hex 32` | any string not starting dev-only-/ci-only- | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__engage_unsubscribe_secret` in the example) | notification, notification-worker (campaign send gate, link minting, unsubscribe routes) (since: main (Engage, #34)) | no - placeholder only | ENGAGE_PUBLIC_API_BASE; EMAIL_TRANSPORT=smtp for a real send |
| `ENGAGE_PUBLIC_API_BASE` | required | no | absolute https URL, no `localhost`/`127.0.0.1` (preflight FAILs otherwise; the send gate refuses http://). Trailing slash is stripped. | CHOOSE (the public origin serving /api/v1) | no | https URL without localhost/127.0.0.1 | `https://app.opsgrids.com/api/v1` | notification, notification-worker (since: main (Engage, #34)) | committed non-secret default | DNS + certificate for the host; app-gateway and notification running; ENGAGE_UNSUBSCRIBE_SECRET |


### 4.7 Platform
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `DB_POOL_SIZE` | required | no | integer, connections per process (~50 processes share max_connections=400). | CHOOSE (default is fine) | no | integer | `5` | every service (x-service-env) (since: main) | committed non-secret default | postgres max_connections=400 (compose) |
| `DB_MAX_OVERFLOW` | required | no | integer, per process. | CHOOSE (default is fine) | no | integer | `2` | every service (x-service-env) (since: main) | committed non-secret default | postgres max_connections=400 (compose) |
| `APP_BASE_URL` | required | no | absolute URL used in emails and links; no validator. | CHOOSE (the merchant-facing app origin) | no | absolute URL | `https://app.opsgrids.com` | every service (x-service-env) (since: main) | committed non-secret default; dev-only value in untracked .env (not printed) | none |
| `CORS_ORIGINS` | required | no | comma-separated origins (parsed to a list); must stay a fixed set - custom domains are never added here. | CHOOSE | no | comma-separated origins | `https://app.opsgrids.com` | every service (x-service-env) (since: main) | committed non-secret default; dev-only value in untracked .env (not printed) | none |
| `LOG_LEVEL` | required | no | a logging level name such as INFO; the setting is an unconstrained string. | CHOOSE (default is fine) | no | a logging level name (unconstrained string) | `INFO` | every service (x-service-env) (since: main) | committed non-secret default; dev-only value in untracked .env (not printed) | none |
| `STORE_SLUG_CACHE_SECONDS` | required | no | integer seconds; bounds how long a removed domain may still serve. | CHOOSE (default is fine) | no | integer seconds | `300` | storefront (since: main) | committed non-secret default | redis |


### 4.8 Backups
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `BACKUP_S3_BUCKET` | required | no | existing bucket name at the provider. NOT the box-local MinIO. | EXTERNAL (an off-box S3-compatible bucket the owner provides) | no | an existing bucket name | `opsgrids-backups` | backup.sh (host cron / `make edge-backup`) (since: main) | committed non-secret default | BACKUP_S3_ENDPOINT/REGION and the credential pair |
| `BACKUP_S3_PREFIX` | required | no | key prefix inside the bucket. | CHOOSE | no | any prefix | `edge/postgres` | backup.sh (since: main) | committed non-secret default | BACKUP_S3_BUCKET |
| `BACKUP_S3_ENDPOINT` | optional-empty (empty = AWS S3) | no | URL passed verbatim as `aws s3 --endpoint-url`. | EXTERNAL (the provider's S3 endpoint URL) | no | empty (AWS) or an endpoint URL | empty | backup.sh (since: main) | no - placeholder only | the provider |
| `BACKUP_S3_REGION` | required | no | region string, exported as AWS_DEFAULT_REGION. | EXTERNAL (the provider's region) | no | provider region string | `us-east-1` | backup.sh (since: main) | committed non-secret default | the provider |
| `BACKUP_S3_ACCESS_KEY_ID` | optional-empty TOGETHER with the secret (both empty = WARN, exactly one set = FAIL) | YES | whatever the provider issues; scope it to write-only on the bucket. | EXTERNAL (issued by the backup provider) | no | empty together with the secret, or both set | empty | backup.sh (since: main) | no - placeholder only | BACKUP_S3_SECRET_ACCESS_KEY |
| `BACKUP_S3_SECRET_ACCESS_KEY` | optional-empty TOGETHER with the key id | YES | whatever the provider issues. | EXTERNAL (issued with the key id; `openssl rand -hex 32` only if your provider lets you choose) | no | empty together with the key id, or both set | empty | backup.sh (since: main) | no - placeholder only | BACKUP_S3_ACCESS_KEY_ID |
| `BACKUP_LOCAL_KEEP` | required | no | integer, nightly dumps kept on the box. | CHOOSE (default is fine) | no | integer | `7` | backup.sh (since: main) | committed non-secret default | none |


### 4.9 Object storage (MinIO and the document service)
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `MINIO_ROOT_USER` | required (even if the storefront profile is off) | YES | no repo-enforced format, but compose embeds it (as the user part) in the `MC_HOST_og` connection URL, so it must be URL-userinfo-safe: hex is. | GENERATE on the box | YES: `openssl rand -hex 16` | URL-userinfo-safe string (hex) | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_16__minio_root_user` in the example) | minio, minio-init (since: release/backlog-complete (document service + MinIO, ADR-0009)) | generated on the box per RUNBOOK 'Secrets' (step 5) and 'Document and MinIO' | MINIO_ROOT_PASSWORD; `storefront` profile for the consumers to run |
| `MINIO_ROOT_PASSWORD` | required (even if the storefront profile is off) | YES | >= 8 characters (preflight FAILs shorter); URL-userinfo-safe (hex). | GENERATE on the box | YES: `openssl rand -hex 32` | >= 8 chars, URL-userinfo-safe | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__minio_root_password` in the example) | minio, minio-init (since: release/backlog-complete (document service + MinIO, ADR-0009)) | generated on the box per RUNBOOK 'Secrets' (step 5) and 'Document and MinIO' | MINIO_ROOT_USER |
| `DOCUMENT_S3_ACCESS_KEY` | required (even if the storefront profile is off) | YES | a free-choice access key; minio-init CREATES a MinIO user with this name. Must differ from MINIO_ROOT_USER. MinIO's own length limits are not stated in the repo; hex-16 is the recommendation. | GENERATE on the box | YES: `openssl rand -hex 16` | string, different from the root and orphan users | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_16__document_s3_access_key` in the example) | document, minio-init (and document-cron until slice/memory-headroom merges) (since: release/backlog-complete (document service + MinIO, ADR-0009)) | generated on the box per RUNBOOK 'Document and MinIO' | minio-init creates it from MINIO_ROOT_*; DOCUMENT_S3_SECRET_KEY; S3_PUBLIC_BUCKET |
| `DOCUMENT_S3_SECRET_KEY` | required (even if the storefront profile is off) | YES | >= 8 characters (preflight FAILs shorter). | GENERATE on the box | YES: `openssl rand -hex 32` | >= 8 chars | none (placeholder `CHANGE_ME_generate_openssl_rand_hex_32__document_s3_secret_key` in the example) | document, minio-init (and document-cron until slice/memory-headroom merges) (since: release/backlog-complete (document service + MinIO, ADR-0009)) | generated on the box per RUNBOOK 'Document and MinIO' | DOCUMENT_S3_ACCESS_KEY |
| `DOCUMENT_S3_ORPHAN_ACCESS_KEY` | optional-empty in preflight; RECOMMENDED on any box with the storefront profile (see format) | YES | same shape as DOCUMENT_S3_ACCESS_KEY, and different from it. minio-init runs `mc admin user add` with this pair unconditionally; what mc does with two empty strings is not verifiable from the repo, so set both at first deploy. | GENERATE on the box | YES: `openssl rand -hex 16` | empty, or a string different from the other two users | empty | minio-init (creates the list+delete user); document-cron until slice/memory-headroom merges (since: release/backlog-complete (document service + MinIO, ADR-0009)) | generated on the box per RUNBOOK 'Document and MinIO' (orphan pass) | DOCUMENT_S3_ORPHAN_SECRET_KEY; DOCUMENT_ORPHAN_SWEEP_ENABLED |
| `DOCUMENT_S3_ORPHAN_SECRET_KEY` | optional-empty in preflight; RECOMMENDED with the access key | YES | >= 8 characters (NOT length-checked by preflight). | GENERATE on the box | YES: `openssl rand -hex 32` | empty, or >= 8 chars | empty | minio-init; document-cron until slice/memory-headroom merges (since: release/backlog-complete (document service + MinIO, ADR-0009)) | generated on the box per RUNBOOK 'Document and MinIO' (orphan pass) | DOCUMENT_S3_ORPHAN_ACCESS_KEY |
| `ASSET_PURGE_RETENTION_DAYS` | required | no | integer days. | CHOOSE (committed default matches ADR-0009 s8) | no | positive integer | `30` | document once slice/memory-headroom merges; document-cron before (since: release/backlog-complete (document service + MinIO, ADR-0009)) | committed non-secret default | document service |
| `DOCUMENT_ORPHAN_SWEEP_ENABLED` | required | no | true\|false. Once slice/memory-headroom merges NO container reads it any more (the orphan pass runs only by hand); until then document-cron does. | CHOOSE (leave false until the orphan pair is set) | no | true \| false | `false` | document-cron until slice/memory-headroom merges; no container after (since: release/backlog-complete (document service + MinIO, ADR-0009)) | committed non-secret default | DOCUMENT_S3_ORPHAN_* set; DOCUMENT_ORPHAN_SWEEP_DRY_RUN |
| `DOCUMENT_ORPHAN_SWEEP_DRY_RUN` | required | no | true\|false. Reporting-only while true. | CHOOSE (keep true until dry runs look right) | no | true \| false | `true` | document-cron until slice/memory-headroom merges; no container after (since: release/backlog-complete (document service + MinIO, ADR-0009)) | committed non-secret default | DOCUMENT_ORPHAN_SWEEP_ENABLED |
| `S3_PUBLIC_BUCKET` | required | no | bucket name (lowercase letters, digits, hyphens); must be the last path segment of PUBLIC_ASSET_BASE_URL (preflight compares them). | CHOOSE (committed default is fine) | no | bucket name | `opsgrids-public` | caddy (path matcher), minio-init (creates it), document (since: release/backlog-complete (document service + MinIO, ADR-0009)) | committed non-secret default | PUBLIC_ASSET_BASE_URL (must match); Caddy route |
| `PUBLIC_ASSET_BASE_URL` | required | no | EXACTLY `https://<EDGE_HOST>/<S3_PUBLIC_BUCKET>`, no trailing slash - preflight compares the strings and FAILs on any difference. Republish the buyer + storefront bundles after changing it. | DERIVED from EDGE_HOST and S3_PUBLIC_BUCKET | no | only `https://<EDGE_HOST>/<S3_PUBLIC_BUCKET>` | `https://edge.opsgrids.com/opsgrids-public` | document; baked into the storefront bundle by publish-web.sh (since: release/backlog-complete (document service + MinIO, ADR-0009)) | committed non-secret default | EDGE_HOST, S3_PUBLIC_BUCKET; republish buyer + storefront bundles |


### 4.10 Observability and ops-monitor
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `SENTRY_DSN` | optional-empty | YES | Sentry DSN URL; empty = no error reporting. | EXTERNAL (a Sentry project, if one exists) | no | empty or a DSN URL | empty | every service (x-service-env) (since: main) | no - placeholder only | none |
| `OPS_ALERT_EMAIL` | optional-empty (line must exist; empty = alerts logged, nobody paged) | no | one address, or SEVERAL COMMA-SEPARATED (no spaces): the value goes verbatim into the To header and smtplib derives the recipients from it (verified for `a,b`). `;`, spaces and the word `and` are NOT separators - only the first address would receive mail, silently. Quote the value if it contains a space: preflight `source`s this file as bash. | CHOOSE (mailboxes a human reads) | no | empty, or one/more addresses comma-separated with NO spaces (quote if any space) | `info@opsgrids.com,zionna@opsgrids.com` | ops-monitor ONLY (since: release/backlog-complete (ops-monitor, merged at 50756f7)) | committed non-secret default | SMTP_HOST + EMAIL_FROM (all three must be set to send); SMTP_USERNAME/PASSWORD + SMTP_USE_TLS or SMTP_USE_SSL for an authenticating relay |
| `OPS_MONITOR_API_CONTAINERS` | optional-empty (line must exist; empty is the normal state) | no | comma-separated `name[:port]`, each name matching ^[a-z_][a-z0-9_.-]*$. A non-empty value REPLACES the derived list. | DERIVED from COMPOSE_PROFILES when empty | no | empty (derive) or a comma list of name[:port] | empty | ops-monitor ONLY (since: release/backlog-complete (ops-monitor, merged at 50756f7)) | no - placeholder only | COMPOSE_PROFILES |
| `OPS_MONITOR_DLQ_QUEUES` | optional-empty (line must exist; empty is the normal state) | no | comma-separated consumer queue names without `.dlq`, each matching ^[a-z_][a-z0-9_.-]*$. A non-empty value REPLACES the derived list. | DERIVED from COMPOSE_PROFILES when empty | no | empty (derive) or a comma list of queue names | empty | ops-monitor ONLY (since: release/backlog-complete (ops-monitor, merged at 50756f7)) | no - placeholder only | COMPOSE_PROFILES; RabbitMQ |


### 4.11 Not `.env.example` keys (do not add these to `.env`)
| Variable | Required / optional | Secret | Exact format | Source | Generate? | Accepted values | Default | Consumer service (since) | Exists in repo / deploy config? | Depends on |
|---|---|---|---|---|---|---|---|---|---|---|
| `MFA_ACCEPT_LEGACY_PLAINTEXT` | optional; NOT a `.env` key (never put it in `.env`) | no | boolean (pydantic); repo documents only `true`/`false` | SET BY THE DEPLOY on one one-off container (`-e`); never by hand in `.env` | no | `true` on that one-off run only; otherwise unset (false) | false (`OC:services/auth/app/config.py:65`) | auth (only the one-off container `remote.sh migrate` starts; compose never forwards it) | no - not in deploy config (a commented `# MFA_ACCEPT_LEGACY_PLAINTEXT=false` exists only in the ROOT dev .env.example on OC) | PII_ENCRYPTION_KEY (the reseal seals under it), the new auth image, Postgres, lib/mfa-reseal.sh |
| `BILLING_RUN_ENABLED` | optional; commented in the example (not a required key) | no | literal `true` to enable; anything else is off | OWNER DECISION | no | `true` \| unset | false (compose `${BILLING_RUN_ENABLED:-false}`) | subscription-worker | no - commented placeholder only | BILLING_RUN_NOT_BEFORE when true |
| `BILLING_RUN_NOT_BEFORE` | conditional: required only when BILLING_RUN_ENABLED=true | no | `^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}(:[0-9]{2})?([+-][0-9]{2}:[0-9]{2}\|Z)$` | OWNER DECISION | no | an ISO-8601 instant with offset | empty | subscription-worker | no - commented placeholder only (on origin/slice/billing-run since 07ea4e2) | BILLING_RUN_ENABLED |
| `EDGE_STATE_DIR` | optional; process environment only (not in `.env.example`) | no | absolute directory path | CHOOSE (leave unset) | no | any writable directory | /srv/opsgrids/state | remote.sh, preflight.sh, backup.sh, ops-monitor bind mount | no - default only | remote.sh does not read `.env`: setting it in `.env` splits the box's state directory in two |
| `EDGE_MEMINFO_FILE` | optional; test seam, process environment only | no | path to a file containing a `MemTotal: <N> kB` line | test/dry-run seam | no | any readable file | /proc/meminfo | preflight.sh only | n/a - test seam | none |
| `RUN_SWEEPS_IN_API` | optional; compose interpolation, not in `.env.example` | no | boolean string | CHOOSE (leave unset) | no | true \| false | true (compose) | document, operations, storefront | no - default only | slice/memory-headroom |
| `EDGE_RELAY_SCHEMAS` | optional; compose interpolation, not in `.env.example` | no | comma-separated schema names | CHOOSE (leave unset) | no | schema names | two compose defaults: edge-relay 28 schemas (incl. `lead`); ops-monitor 27 (no `lead`) - see cross-slice note | edge-relay, ops-monitor | no - default only | remote.sh SERVICES_WITH_SCHEMA |
| `ROUTES_SHA` | derived; never set by hand | no | 16 hex chars | DERIVED by remote.sh (sha256 of routes.prod.json) | no | - | `unset` (compose) | app-gateway | no | routes.prod.json |


## 5. The variables you asked about

### 5.1 `ADMIN_GATEWAY_HOP_KEY`

- **What it is.** The HMAC key admin-gateway signs its hop header with, and auth verifies
  (`OC:libs/platform-core/src/platform_core/internal_auth.py:175`: `hmac.new(key.encode(), ..., sha256)`). It is used **as the string it is**, not hex- or base64-decoded.
- **Rules the code enforces** (`OC:libs/platform-core/src/platform_core/config.py:271-279`,
  `OC:services/auth/app/config.py:81-95`): at least 32 UTF-8 bytes; not starting `dev-only-` or
  `ci-only-` in production/staging/uat; **never equal to `INTERNAL_SERVICE_TOKEN`** (any environment); and auth
  refuses to start without it in production/staging/uat.
- **Where a bad or missing value is caught: preflight.** On OC `94e412b` preflight mirrors those rules before
  anything changes (`OC:preflight.sh:98-100`, `OC:lib/secret-strength.sh`): `INTERNAL_SERVICE_TOKEN`,
  `PII_ENCRYPTION_KEY` and `ADMIN_GATEWAY_HOP_KEY` each at least 32 **bytes** (`wc -c`, matching
  `len(value.encode())`), and the hop key must differ from the internal token; an empty value is the generic
  "missing or empty" failure. Compose passes it as `${ADMIN_GATEWAY_HOP_KEY:-}` (`OC:compose.prod.yml:573`), an
  empty default **on purpose**: a `:?` would abort every compose command on a box without the key, including
  `backup.sh`'s `pg_dump` and a rollback (`OC:RUNBOOK.md`, "ADMIN_GATEWAY_HOP_KEY"). An empty string is read as
  *unset* (`OC:libs/platform-core/src/platform_core/config.py:189-199`), so auth then refuses to start for the right
  reason. The first OC head
  (`5f89e27`) had `:?`; this document was first written against it.
- **Secure generation.** 256 bits of randomness, hex-encoded (64 ASCII characters, 64 bytes as a HMAC key, which
  equals SHA-256's block size; shell-, dotenv- and URL-safe):

  ```bash
  # on the box, in /srv/opsgrids/repo/infrastructure/edge, without echoing the value:
  sed -i "s|^ADMIN_GATEWAY_HOP_KEY=.*|ADMIN_GATEWAY_HOP_KEY=$(openssl rand -hex 32)|" .env
  grep -c '^ADMIN_GATEWAY_HOP_KEY=[0-9a-f]\{64\}$' .env      # must print 1
  ```

  Run it as its own `openssl` invocation (never reuse the `INTERNAL_SERVICE_TOKEN` value). `openssl rand -hex 16`
  passes the 32-byte check but is only 128 bits: do not use it. Base64 works but its `+ / =` characters are
  awkward in `.env`. Generate on the box, never copy from dev, another environment or a chat window
  (`OC:RUNBOOK.md`, "Secrets"). The section 7 helper does this only when the current value is empty or a
  placeholder.
- **Consumer.** auth only: `OC:compose.prod.yml:573` is the single occurrence. app-gateway does not get it
  (`OC:tests/infra/test_compose_hop_key.py` pins the carriers). **admin-gateway, the only signer, is not in
  `compose.prod.yml`**, so operator sign-in is unreachable on this box, and the key exists so that stays true.
- **Ordering.** It must be in `.env` before `make deploy` (preflight is the first step and refuses). No other
  ordering constraint remains.
- **Placeholders in the example** are key-specific (`CHANGE_ME_generate_openssl_rand_hex_32__<key>`): two
  identical placeholders for `INTERNAL_SERVICE_TOKEN` and `ADMIN_GATEWAY_HOP_KEY` would trip the "must differ"
  check on top of the placeholder failure. Preflight refuses any `CHANGE_ME*` value regardless.

### 5.2 `MFA_ACCEPT_LEGACY_PLAINTEXT`

**Why the application has it.** On INT (what production runs) an authenticator secret is stored as raw base32
bytes: `factor.secret_enc = secret.encode()`, read back with `.decode()`
(`INT:services/auth/app/services.py:1032,1058,1153`), for **tenant members and staff alike** (`OC:RUNBOOK.md`,
"The reseal"). OC seals every new secret with the platform PII envelope and a per-row AAD
(`OC:services/auth/app/services.py:1077`) and reads with a three-tier tolerant open (`:1111-1174`): (1) sealed
with the row's AAD; (2) sealed without AAD (an interim envelope, GCM-authenticated, accepted always); (3) **raw
base32 plaintext**, which nothing authenticates, honoured **only** while `mfa_accept_legacy_plaintext` is true
(`:1153`, `OC:services/auth/app/config.py:65`, default `false`). Any accepted row is resealed in place and audited
as `auth.mfa_factor_resealed`. The flag exists solely so one maintenance run can convert rows the old code wrote.

**Secure production value: unset (false), always.** It is not in `.env.example`, and compose does not forward it
(no such string in `OC:compose.prod.yml`, no `env_file:` in it), so the running auth container can never have it.
Its only home is a `-e` on the one-off container the deploy starts (`OC:infrastructure/edge/lib/mfa-reseal.sh:63-64`).
The release's own test forbids naming it in compose or in the edge `.env.example`
(`OC:tests/infra/test_mfa_reseal.py::test_the_live_auth_container_can_never_be_given_the_plaintext_flag`), which is
why `.env.example` here describes it without naming it. Putting it in `.env` would also make preflight demand it.

**The deploy does the reseal (OC `94e412b`; the first OC head left it as a manual step after `up`).** Order:
`build -> migrate -> RESEAL -> up -> health`. `remote.sh migrate` ends with `reseal_mfa_factors_if_pending`
(`OC:remote.sh:38,205`). It counts the rows still holding a raw secret with
`MFA_PLAINTEXT_COUNT_SQL` (`OC:lib/mfa-reseal.sh:33`; the RUNBOOK gives operators the same statement and a test
pins the two together). Count 0 (every deploy after the first): nothing runs, not even a container. Count N: a
one-off container **from the new image**, before any new container serves:

```bash
docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T \
  -e MFA_ACCEPT_LEGACY_PLAINTEXT=true auth python -m app.mfa_maintenance reseal
```

It prints `resealed=N` and `remaining=N` (counts only) and exits 1 if `remaining` is above zero; the deploy then
re-counts and **aborts before `up` unless the count is 0** (`OC:lib/mfa-reseal.sh:46-74`).

**Does enabling it downgrade security? Yes, narrowly, and only while that one-off container runs.** With it on,
the trust rule changes from "the bytes must be a GCM envelope under our key" to "or any base32-looking string".
Anyone who can *write* `auth.mfa_factors.secret_enc` in that window (SQL injection, a compromised database role,
a bad restore) can plant a secret of their choosing, generate valid codes for that user, and the service then
seals it under the platform key and logs it as an ordinary `auth.mfa_factor_resealed` row: the plant is
indistinguishable from a legitimate legacy reseal (`OC:services/auth/app/services.py:1162-1170`). TOTP verification
strength and replay protection are unchanged. The larger standing weakness is the plaintext itself (anyone with
database *read* access holds every seed), and the reseal removes it, so one run is a net security gain. **A
persistent `true` would be the downgrade**, and nothing here can persist it.

**What the deploy accepts, and what to watch.**

- *The window.* Between the reseal finishing and `up` recreating auth, the **old** auth container still serves and
  cannot read a sealed envelope. Per the RUNBOOK a member signing in with an authenticator code is refused for those
  seconds, nothing is counted against them, and a recovery code still works. By my read of the old code the refusal
  is an unhandled `.decode()` error (`INT:services.py:1058,1153`); a check run for this audit found 10,000 of 10,000
  random envelope-shaped byte strings fail to decode as UTF-8. The slice accepts it: the alternative order (`up`,
  then reseal) is a lockout.
- *Fail-open on a count that cannot be read.* If Postgres is not answering or the auth schema does not exist, the
  step prints `cannot count legacy factors ... skipped` and the deploy continues (`OC:lib/mfa-reseal.sh:51`).
- *A plaintext row met later with the flag off* (a restore from an old backup, a manual insert): auth answers a
  distinct **503 "Multi-factor re-seal pending - use a recovery code"**, not counted against the member's MFA failure
  counter, never locking the account; the next deploy reseals it, or run the command above by hand (`OC:RUNBOOK.md`).
- *Pre-check.* Whether any legacy row exists on the real box is unknown from the repo. To look without changing
  anything: `docker compose -f compose.prod.yml --env-file .env exec -T postgres psql -U opsgrids -d opsgrids -Atc
  "select count(*) from auth.mfa_factors where secret_enc is not null and encode(secret_enc,'escape') ~ '^[A-Z2-7]{16,32}\$'"`
  (the statement in `OC:lib/mfa-reseal.sh:33`); it must end at `0` after the deploy.
- **Rollback is a one-way door for MFA** (derived; not in the RUNBOOK). Once any row is sealed (by the reseal, by lazy
  reseal at sign-in, or by a new enrolment), `make edge-rollback` to a pre-OC ref puts back code that cannot read
  sealed secrets. Decide before deploying.

### 5.3 `EDGE_PROFILES`, `COMPOSE_PROFILES` and `EDGE_BUDGET_ACCEPTED_MB`

**The parsers.** There is no validator for a profile name; the only parsing is:

| Where | What it does |
|---|---|
| `MH:infrastructure/edge/lib/active-profiles.sh` `resolve_declared_profiles` | non-empty `EDGE_PROFILES` **replaces** `COMPOSE_PROFILES` (never a union); both empty = empty = core only, never an error |
| `MH:preflight.sh:93,101` | resolves it, then `export COMPOSE_PROFILES=<resolved>` so every compose call sees it |
| `MH:remote.sh:52-64` | reads the first `^EDGE_PROFILES=` line with `sed` (`:55-56`), deletes quotes, spaces and CR, exports `COMPOSE_PROFILES` only when `EDGE_PROFILES` is non-empty |
| `MH:lib/memory_budget_totals.py:214-215` `_csv_set` | `{v for v in value.split(",") if v}` : **no strip**, so `hrms, crm` prices ` crm` as unknown |
| preflight `case ",$declared,"` matching | exact, case-sensitive substring |

**Exact syntax.** `EDGE_PROFILES=<csv>` on one line: lowercase profile names, comma-separated, **no
spaces, no `*`, no inline comment**, empty allowed. Valid names are the ten compose profiles (also
the `profiles` list of `memory-budget.json`): `inventory storefront hrms crm pulseboard projects support
marketing finance itsm`. The Inventory product's catalogue key is `operations` but its profile is
`inventory`. An unknown or misspelt name is **silently ignored** (priced as nothing, key-mapped to
nothing). Units: none. Reproduced in section 8: `EDGE_PROFILES=inventory, storefront` unquoted exits
127; `EDGE_PROFILES=storefront` overrides a ten-profile `COMPOSE_PROFILES` and prices 2242 MB.

**`EDGE_BUDGET_ACCEPTED_MB` syntax and units.** A whole number of megabytes, digits only
(`^[0-9]+$`; anything else FAILs preflight: `MH:preflight.sh:261`, `MH:lib/memory-budget.sh`
`apply_accepted_ceiling`, `:67`). The unit is the same as the `<n>M` preflight prints: the sum of
`memory-budget.json`'s `expected_rss_mb` per container, compared with `MemTotal` from `/proc/meminfo`
in kB divided by 1024 (`MH:lib/memory-budget.sh:27-28`), i.e. MiB in practice. The comparison is inclusive:
deploy allowed (as a loud warning) iff `expected_mb <= EDGE_BUDGET_ACCEPTED_MB`, and only ever for a
`refuse` verdict. Verified by runs (section 8): ceiling 4112 -> warn; 4111 -> refuse; `4112MB` ->
`must be a whole number of megabytes`.

**The number, on the final release** (computed with the repo's own `lib/memory_budget_totals.py` on
the final union, `memory-budget.json` `mem_total_mb_reference` = 3862):

| declared profiles | cumulative MB | of 3862 MB |
|---|---|---|
| core only | 2012 | 52% |
| + storefront | 2242 | 58% |
| + inventory | 2327 | 60% |
| + hrms | 2922 | 75% |
| + crm | 3262 | 84% |
| + pulseboard | 3517 | 91% |
| + projects | 3602 | 93% |
| + support | 3687 | 95% |
| + marketing | 3772 | 97% |
| + finance | 4027 | **104%** |
| + itsm (all ten) | **4112** | **106%** |

So for all ten profiles the ceiling that lets preflight through is **4112**, not the **3894** in the
RUNBOOK's and ADR-0013's worked example: that figure is memory-headroom alone; ops-monitor (+48) and
crm-leads (+`lead` 85, +`customer-worker` 85) add 218. The repo's own test
`tests/infra/test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_print`
fails on the final union for exactly this reason (section 9). The authoritative figure is whatever preflight
prints on the box: the price is *(running containers the new compose still defines) union
(declared services)*, so it depends on what is running at deploy time. Read it, do not compute it
(command in section 7). ADR-0013 also warns the API price (85 MB) may be low: the API images
measured 94-102 MiB (`MH:docs/adr/0013...md`, RSS measurements).

**What the repo records about the real box** (ADR-0013, from a read-only look during review:
`MH:docs/adr/0013-self-serve-products-and-the-one-box-deployment.md:326,615-622`): MemTotal 3862 MB,
about 2 GB of swap in use, 40 containers running, an `.env` whose `COMPOSE_PROFILES` lists all ten
profiles and which has no `EDGE_PROFILES`, no `edge-budget.last-ok`, running set priced at 3494 MB
(90%). Not re-verified here.

### 5.4 `OPS_ALERT_EMAIL`, `SMTP_*`, `OPS_MONITOR_*`

**Multiple recipients: supported, comma-separated.** The value is assigned verbatim to the message's
`To` header (`INT:libs/platform-core/src/platform_core/ops_monitor/mailer.py:89`) and sent with
`smtp.send_message(message)` and no explicit recipients (`:117`), so `smtplib` derives the envelope
recipients from the header. Verified with the standard library (Python 3.13.2, a capture subclass
overriding `sendmail`, no network):

| value | envelope recipients |
|---|---|
| `info@opsgrids.com,zionna@opsgrids.com` | both |
| `info@opsgrids.com, zionna@opsgrids.com` | both |
| `info@opsgrids.com;zionna@opsgrids.com` | **first only, silently** |
| `info@opsgrids.com and zionna@opsgrids.com` | **first only, silently** |
| `info@opsgrids.com zionna@opsgrids.com` | **first only, silently** |

Caveats: no repo test sends to two addresses (the tests use one and their fake `send_message` ignores
recipients), so this is stdlib behaviour, not a pinned contract; both recipients see each other in
`To`. And `.env` is sourced by bash: **`OPS_ALERT_EMAIL=info@opsgrids.com and zionna@opsgrids.com`
unquoted exits 127 (`and: command not found`) before preflight prints anything**, while docker compose
reads the same line as a literal string. Use the comma form, no spaces (or quote the whole value).
Sending needs `SMTP_HOST`, `OPS_ALERT_EMAIL` and `EMAIL_FROM` all non-empty
(`INT:ops_monitor/config.py:267-268`); it does not consult `EMAIL_TRANSPORT`.

**The SMTP pair.** `SMTP_USE_SSL` is read by ops-monitor only. It and `SMTP_USE_TLS` are mutually
exclusive: both true raises at **send** time (`INT:mailer.py:92-96`), not at startup and not in
preflight. ops-monitor also refuses to log in over an unencrypted connection (`:110-116`).
`SMTP_USE_TLS` is one shared key for notification **and** ops-monitor, so choosing SMTPS for
ops-monitor (`SMTP_USE_TLS=false`) also switches off STARTTLS for notification, which has no
implicit-TLS path. For the committed Google Workspace setup the answer is `SMTP_USE_TLS=true`,
`SMTP_USE_SSL=false`, port 587.

**`OPS_MONITOR_API_CONTAINERS` / `OPS_MONITOR_DLQ_QUEUES`.** Leave empty: empty means *derive from
`COMPOSE_PROFILES`*, not *check nothing*, and a non-empty value **replaces** the derived list. Each
name must match `^[a-z_][a-z0-9_.-]*$`. With all ten profiles the derivation on the final release
(computed from `ops_monitor/config.py` on the final union) is **28 API containers**
`auth account product app-gateway catalog subscription billing metering payment notification operations storefront document approval employee attendance leave customer deal lead project timesheet standup projects support marketing finance itsm`
and **12 queues** `account.events product.events subscription.events payment.events notification.events
notification.dispatch approval.events attendance.events leave.events customer.events finance.events
scheduler.events`. (The crm-leads follow-up `6c3196f` added `lead` and `customer.events`, see section 9.)

### 5.5 `MINIO_*` and `DOCUMENT_S3_*` (how the credentials relate)

There are three independent pairs, none derived from another (`INT:compose.prod.yml:391-392,440,447-448,494,523`):

1. `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`: the MinIO server's root account. Used by `minio` and,
   once, by `minio-init` through `MC_HOST_og: http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000`.
   Because of that URL both must be URL-userinfo-safe: hex is.
2. `DOCUMENT_S3_ACCESS_KEY` / `DOCUMENT_S3_SECRET_KEY`: free-choice values `minio-init` turns into a
   MinIO **user** (`mc admin user add og ...`, `:494`) with a put/delete-only policy on the one bucket;
   `document` receives the same values as `S3_ACCESS_KEY`/`S3_SECRET_KEY` (`:1025-1026`). They are
   *not* the root pair and must not equal it.
3. `DOCUMENT_S3_ORPHAN_ACCESS_KEY` / `_SECRET_KEY`: a second user (list + delete). `minio-init` runs
   `mc admin user add` with this pair **unconditionally under `set -e`** (`:523`) while preflight
   allows both empty. What `mc` does with two empty strings cannot be determined from the repo, so
   generate both at the first deploy. (MH folds `document-cron` into `document`; after it merges only
   `minio-init` still reads these and the orphan pass runs by hand.)

There is no external S3 for these: production uses the box-local MinIO (`S3_ENDPOINT_URL` is hardcoded
`http://minio:9000`). The only external bucket in this repo is the **backup** destination
(`BACKUP_S3_*`, read by `backup.sh` on the host). Preflight requires all of 1-2 even on a box whose
`storefront` profile is off, because it does not special-case profiles. MinIO's own length limits
beyond "password >= 8" are not stated in the repo; preflight enforces 8 for the root password and the
document secret only.

### 5.6 `ENGAGE_*`

Only two are environment variables an operator sets. `ENGAGE_UNSUBSCRIBE_SECRET` is an HMAC-SHA256
key with **no length floor** in code (unlike the two platform secrets), and a `dev-only-` value in a
real environment disables campaign sending at the **feature** level (`unsubscribe_key()` returns
`None`: `INT:services/notification/app/services.py:2818-2829`) rather than stopping the process;
rotating it invalidates every unsubscribe link already sent. `ENGAGE_PUBLIC_API_BASE` must be https
and not localhost (`INT:preflight.sh:273`, `INT:.../services.py:2832`). Five more `ENGAGE_*` tunables
(`..._RATE_LIMIT_PER_MINUTE`, `..._CAMPAIGN_BATCH_SIZE`, and three more in
`services/notification/app/config.py`) exist but compose never passes them, so they cannot be set from
`.env` and are not variables to populate.

## 6. The partition

### A. Values you must obtain from an external service

| Value | From | Note |
|---|---|---|
| `SMTP_PASSWORD` | Google Workspace: an **app password** for the `SMTP_USERNAME` mailbox | optional-empty in preflight, but with a username set and no password every ops-monitor alert send fails at login and notification cannot authenticate |
| `SMTP_HOST`, `SMTP_USERNAME`, `EMAIL_FROM` | confirm with the provider that the mailbox may send as `EMAIL_FROM` | committed defaults `smtp.gmail.com` / `info@opsgrids.com` |
| `BACKUP_S3_BUCKET`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION` | an off-box S3-compatible bucket you provide | the committed bucket name `opsgrids-backups` is a placeholder; the repo does not prove it exists |
| `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY` | the backup provider | both or neither. Design tension: the example asks for a write-only key but the restore drill (`RUNBOOK`) downloads with the same pair |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Razorpay dashboard | empty = the stub; keys alone change nothing (`PAYMENT_WIRE=live` does) |
| `PAYMENT_PROVIDER`, `PAYMENT_WIRE`, `PAYMENT_ALLOW_LIVE_KEYS`, `CASHFREE_ENV` | the owner (commented in the example; defaults are the safe side) | RUNBOOK 'Payments: turning a provider on' |
| `PAYMENT_ALLOW_TEST_KEYS` | the owner, staging boxes only (commented in the example; default `false`) | RUNBOOK 'Payments: test mode on a staging box' |
| `CASHFREE_APP_ID`, `CASHFREE_SECRET_KEY` | Cashfree dashboard | optional-empty; the secret also signs Cashfree's webhooks |
| `WHATSAPP_APP_SECRET`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN` | Meta | only if you enable WhatsApp |
| `EDGE_RESERVED_IP` | the cloud provider's reserved IP for the VM | empty is valid; never a documentation range |
| `SENTRY_DSN` | a Sentry project | optional |
| the mailboxes `info@opsgrids.com`, `zionna@opsgrids.com` | your mail admin | preflight cannot check they exist |

### B. Values you must generate (on the box, never from dev or chat)

| Variable | Command |
|---|---|
| `ADMIN_GATEWAY_HOP_KEY` | `openssl rand -hex 32` (a separate invocation from `INTERNAL_SERVICE_TOKEN`) |
| `ENGAGE_UNSUBSCRIBE_SECRET` | `openssl rand -hex 32` (or `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`) |
| `MINIO_ROOT_USER` | `openssl rand -hex 16` |
| `MINIO_ROOT_PASSWORD` | `openssl rand -hex 32` |
| `DOCUMENT_S3_ACCESS_KEY` | `openssl rand -hex 16` (different from the root user) |
| `DOCUMENT_S3_SECRET_KEY` | `openssl rand -hex 32` |
| `DOCUMENT_S3_ORPHAN_ACCESS_KEY` / `_SECRET_KEY` | `openssl rand -hex 16` / `openssl rand -hex 32` (recommended; see 5.5) |
| `RAZORPAY_WEBHOOK_SECRET` | `openssl rand -hex 32`, then paste the same value into Razorpay's webhook settings |
| `WHATSAPP_VERIFY_TOKEN` | `openssl rand -hex 32`, then paste it into the Meta app (only with WhatsApp) |
| fresh box only: `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD` (hex/alphanumeric: it goes into a URL), `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY`, `RABBITMQ_PASSWORD` | `openssl rand -hex 32` each. **Never regenerate `PII_ENCRYPTION_KEY` on a box that holds data.** |
| fresh box only: the signing key file (`JWT_PRIVATE_KEY_HOST_PATH`) | `openssl genpkey -algorithm ed25519 -out /srv/opsgrids/secrets/jwt-ed25519.pem && chmod 600 /srv/opsgrids/secrets/jwt-ed25519.pem` |

### C. Values you can safely choose or configure

| Variable | Recommended value | Why |
|---|---|---|
| `OPS_ALERT_EMAIL` | `info@opsgrids.com,zionna@opsgrids.com` | your stated intent; comma form verified (5.4); needs `SMTP_HOST` and `EMAIL_FROM` set to send |
| `OPS_MONITOR_API_CONTAINERS`, `OPS_MONITOR_DLQ_QUEUES` | empty | derived from `COMPOSE_PROFILES`; an explicit value replaces the derived list |
| `SMTP_USE_SSL` / `SMTP_USE_TLS` / `SMTP_PORT` | `false` / `true` / `587` | Google submission; one shared `SMTP_USE_TLS` (5.4) |
| `EMAIL_TRANSPORT` | `smtp` once `SMTP_PASSWORD` exists, else `log` | `log` sends nothing (preflight warns) |
| `EDGE_PROFILES` | empty (add the line, leave it empty) | the repo records that the real box sets `COMPOSE_PROFILES` and no `EDGE_PROFILES`; the override adds nothing. Which profiles run is E1 |
| `COMPOSE_PROFILES` | leave the box's existing value | see E1 |
| `ENGAGE_PUBLIC_API_BASE` | `https://app.opsgrids.com/api/v1` | committed; matches the Caddy `/api/*` route to app-gateway and `app.opsgrids.com` in `TLS_PLATFORM_HOSTS` |
| `S3_PUBLIC_BUCKET`, `PUBLIC_ASSET_BASE_URL` | `opsgrids-public`, `https://edge.opsgrids.com/opsgrids-public` | the URL is derived: exactly `https://$EDGE_HOST/$S3_PUBLIC_BUCKET` |
| `ASSET_PURGE_RETENTION_DAYS`, `DOCUMENT_ORPHAN_SWEEP_ENABLED`, `DOCUMENT_ORPHAN_SWEEP_DRY_RUN` | `30`, `false`, `true` | committed defaults; turn the orphan pass on only after the orphan pair is set and dry runs look right |
| `BILLING_TAX_CODE`, `BILLING_TAX_JURISDICTION` | empty | until the deployment is tax-registered and a rate row exists |
| `BILLING_RUN_ENABLED` | unset (off) | turning it on converts every expired trial and bills every overdue period; needs `BILLING_RUN_NOT_BEFORE` |
| `ACME_EMAIL` | a real inbox you read. `info@opsgrids.com` satisfies preflight | your call: the repo only requires a monitored inbox without `example.com`; the committed `ops@example.com` is refused |
| `WHATSAPP_TRANSPORT` | `log` | until a Meta app exists |
| `EDGE_HOST`, `TLS_PLATFORM_HOSTS`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `APP_BASE_URL`, `CORS_ORIGINS`, `LOG_LEVEL`, `STORE_SLUG_CACHE_SECONDS`, `BACKUP_S3_PREFIX`, `BACKUP_LOCAL_KEEP`, `JWT_ISSUER`, `JWT_KID`, `EDGE_WEB_ROOT` | the committed defaults | non-secret; `JWT_KID` is inert |
| `MFA_ACCEPT_LEGACY_PLAINTEXT` | unset | not an `.env` variable (5.2); the *ordering* is E2 |

### D. Values that already exist

- **Repository (non-secret, committed):** the defaults in the C table above. No production secret value
  exists anywhere in the repository, and none is printed here.
- **Untracked dev `.env` at the repo root** (key presence found with `grep -c`, values never read;
  **dev-only, never promote**): `APP_BASE_URL CORS_ORIGINS INTERNAL_SERVICE_TOKEN JWT_ISSUER JWT_KID
  LOG_LEVEL PII_DECRYPT_KEYS PII_ENCRYPTION_KEY SMTP_HOST SMTP_PASSWORD SMTP_PORT SMTP_USE_TLS`. The
  other 54 keys are absent; so are `ADMIN_GATEWAY_HOP_KEY`, `EDGE_BUDGET_ACCEPTED_MB` and
  `MFA_ACCEPT_LEGACY_PLAINTEXT`. No `infrastructure/edge/.env` exists in any local checkout.
- **The box** (read-only facts committed in ADR-0013, 5.3): a running deployment with `COMPOSE_PROFILES`
  listing all ten profiles and no `EDGE_PROFILES`. A box that is running was, by construction, configured
  with the keys **its** release required (main's 46), so those secrets exist there, but this audit could
  not verify which keys or values, and it says nothing about the 20 keys added since main. Run the section 7
  commands on the box to find out. The signing key file, if auth is running, is generated on the box per RUNBOOK
  "Secrets" (step 5).

### E. Values that need a decision or a migration

1. **The memory overcommit (`EDGE_BUDGET_ACCEPTED_MB`).** Choose one: (a) resize the VM (the only option that
   adds headroom, but the owner has said no resize, recorded before this release added ~400 MB, and the RUNBOOK
   asks again), (b) de-list a product and drop its profile only after confirming it has no live activation, or
   (c) accept the overcommit in writing by setting `EDGE_BUDGET_ACCEPTED_MB` to the number **preflight prints**
   (4112 for all ten on this release, at MemTotal 3862 MB). It has to be raised again after every slice that adds a
   container. Never set it to make a refuse go away without reading why it refused.
2. **`MFA_ACCEPT_LEGACY_PLAINTEXT` ordering: settled by OC `94e412b`, with three things to accept.** The deploy reseals
   between `migrate` and `up`, with the flag on one one-off container only. You accept: (a) the seconds-long window in which the
   old auth container refuses authenticator codes for resealed members (recovery codes work, nothing is counted against
   them); (b) that a count that cannot be read skips the step and the deploy continues (affected members then see the
   503 "re-seal pending" until the next deploy or a manual run); (c) that rollback to a pre-OC ref is a one-way door for
   MFA. Whether any legacy row exists on the real box is unknown (5.2). Nothing to run by hand for the ordering.
3. **The TLS row in preflight** (section 3): a one-token code fix, not an env value. Blocks every deploy.
4. **`DOCUMENT_S3_ORPHAN_*` empty vs `minio-init`'s unconditional `mc admin user add`.** Either set both (recommended)
   or guard the script. Behaviour with empty arguments is not verifiable from the repo.
5. **`SMTP_USE_TLS` shared between notification and ops-monitor**, and notification's STARTTLS does not verify the
   server certificate (`INT:services/notification/app/transports.py:226`: `client.starttls()` with no context;
   CPython's default there is `ssl._create_stdlib_context`, which is the unverified context, checked on 3.13.2; the
   image runs 3.12, not executed here). ops-monitor passes `ssl.create_default_context()`. Decide whether to
   harden notification.
6. **Multi-recipient alerts** work today by stdlib behaviour (5.4). Optional hardening if you want a contract: a
   validator that splits with `email.utils.getaddresses` and rejects `;`, spaces and a missing `@`, passing the
   parsed list as `to_addrs=`, plus a test asserting both recipients. Nothing needed to deploy.
7. **Backup credential scope.** A write-only backup key cannot run the RUNBOOK's restore drill, which downloads
   with the same pair; the repo defines only one pair.
8. **After MH merges, `DOCUMENT_ORPHAN_SWEEP_ENABLED` / `_DRY_RUN` are read by no container** (the orphan pass runs
   only by hand). They stay required in the example because MH's own tests build `.env` from it.

## 7. Operator commands

All run **on the box** in `/srv/opsgrids/repo/infrastructure/edge`, as the deploy user. None prints a value.

**7.1 Which required keys are missing from `.env`** (names only; `REF` is the ref you are about to deploy and it must
contain the `.env.example` of that release, i.e. this file once merged):

```bash
cd /srv/opsgrids/repo/infrastructure/edge
REF=origin/release/backlog-complete
git fetch --prune origin
comm -23 \
  <(git show "$REF:infrastructure/edge/.env.example" | sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' | sort -u) \
  <(sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env | sort -u)
```

Same rule preflight uses (`INT:preflight.sh:37`), so this list is exactly its "missing" set for absent lines
(preflight also lists, as missing, keys that are present but empty and not allowed to be).

**7.2 Which are present but empty (not allowed) or still placeholders** (names only):

```bash
allowed=$(git show "$REF:infrastructure/edge/preflight.sh" \
  | sed -n 's/^optional_empty="\(.*\)"$/\1/p' | tr ' ' '\n' | grep -v '^\$' | sort -u | tr '\n' ' ')
awk -v allowed="$allowed" '
BEGIN { n = split(allowed, a, " "); for (i = 1; i <= n; i++) ok[a[i]] = 1 }
/^[A-Z][A-Z0-9_]*=/ {
  i = index($0, "="); k = substr($0, 1, i - 1); v = substr($0, i + 1)
  gsub(/["\047 \r]/, "", v)
  if (v == "" && !(k in ok)) print "EMPTY (must be set)  " k
  else if (v ~ /^(replace-me|CHANGE_ME|dev-only-|ci-only-)/ || v ~ /example\.com/) print "PLACEHOLDER          " k
}' .env
```

**7.3 Append the missing keys without overwriting anything** (each line comes from `.env.example`, so it is a
placeholder or an empty value, never a secret):

```bash
missing=$(comm -23 \
  <(git show "$REF:infrastructure/edge/.env.example" | sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' | sort -u) \
  <(sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env | sort -u))
if [ -n "$missing" ]; then
  [ -z "$(tail -c1 .env)" ] || echo >> .env          # .env may not end in a newline
  for k in $missing; do git show "$REF:infrastructure/edge/.env.example" | grep -m1 "^${k}=" >> .env; done
  chmod 600 .env
  echo "appended: $missing"
fi
```

**7.4 Generate a secret only where the value is empty or a placeholder** (never overwrites a real value; never
prints one; writes through a 0600 temp file):

```bash
fill_if_unset() {   # usage: fill_if_unset KEY BYTES
  local k="$1" n="$2" cur new tmp
  cur=$(sed -n "s/^${k}=//p" .env | head -1 | tr -d "\"' \r")
  case "$cur" in
  '' | CHANGE_ME* | replace-me*)
    new=$(openssl rand -hex "$n")
    tmp=$(umask 077 && mktemp ./.env.XXXXXX)
    sed "s|^${k}=.*|${k}=${new}|" .env > "$tmp" && mv "$tmp" .env
    chmod 600 .env; echo "generated: $k" ;;
  *) echo "kept:      $k" ;;
  esac
}
fill_if_unset ADMIN_GATEWAY_HOP_KEY        32
fill_if_unset ENGAGE_UNSUBSCRIBE_SECRET    32
fill_if_unset MINIO_ROOT_USER              16
fill_if_unset MINIO_ROOT_PASSWORD          32
fill_if_unset DOCUMENT_S3_ACCESS_KEY       16
fill_if_unset DOCUMENT_S3_SECRET_KEY       32
fill_if_unset DOCUMENT_S3_ORPHAN_ACCESS_KEY 16   # recommended, see 5.5
fill_if_unset DOCUMENT_S3_ORPHAN_SECRET_KEY 32
fill_if_unset RAZORPAY_WEBHOOK_SECRET      32
# fresh box only, never on a box that holds data:
#   POSTGRES_PASSWORD OPSGRIDS_DB_PASSWORD INTERNAL_SERVICE_TOKEN PII_ENCRYPTION_KEY RABBITMQ_PASSWORD
```

Then check the shape without printing: `grep -c '^ADMIN_GATEWAY_HOP_KEY=[0-9a-f]\{64\}$' .env` must print `1`.
7.1-7.4 were exercised in the audit against a throwaway `.env` (missing keys listed and appended, an empty and a
placeholder value generated, a real one kept, mode left 600, no stray temp files); 7.3's `git show` form differs
from that test only in where the key list comes from.

**7.5 Run preflight for real.** `make edge-preflight HOST=deploy@<ip> CONFIRM=edge.opsgrids.com` runs whatever ref
is checked out on the box (it does not take `REF`). To preflight an unmerged ref run
`infrastructure/edge/ssh-deploy.sh <host> <confirm> <ref> preflight`, which first checks the ref out
(detached) on the box. `make deploy` runs
preflight first and stops there if it fails. Read the memory line:
`memory budget (post-deploy: ...): <N>M of <M>M MemTotal (<P>%)`; `<N>` is the number for
`EDGE_BUDGET_ACCEPTED_MB` if you choose E1(c).

**7.6 The MFA reseal** needs no command: the deploy runs it (5.2). **7.7** After changing the profile set, `docker compose up` starts what a new
profile adds but does **not** stop what a removed one no longer lists (`MH:lib/active-profiles.sh` header).

## 8. The preflight validation that was run

**What ran.** `preflight.sh` of the final union, copied to a scratch directory, against a temporary `.env` built from
the new `.env.example`, on the audit machine (macOS, Docker Desktop 28.1.1 with compose v2.35.1, bash 3.2, BSD sed and
awk). Never against production, never with a real secret. `EDGE_STATE_DIR` was pointed at a non-existent temp
directory and `EDGE_MEMINFO_FILE` at a file saying `MemTotal: 3862 MB` (the figure from ADR-0013) because macOS has no
`/proc/meminfo`. The docker calls preflight makes are read-only (`info`, `ps`, `compose config --services`); no
container was created and no docker state changed. Each run was leak-checked: the throwaway secrets never appeared
in the output. Runs 2-13 replace the placeholders with random throwaway values ("filled").

| Run | Exit | Flagged |
|---|---|---|
| **1. `.env` = the example verbatim (placeholders only)** | 1 | `every key in .env.example is set` (ok). **FAIL placeholders:** `ACME_EMAIL POSTGRES_PASSWORD OPSGRIDS_DB_PASSWORD INTERNAL_SERVICE_TOKEN ADMIN_GATEWAY_HOP_KEY PII_ENCRYPTION_KEY RABBITMQ_PASSWORD RAZORPAY_WEBHOOK_SECRET ENGAGE_UNSUBSCRIBE_SECRET MINIO_ROOT_USER MINIO_ROOT_PASSWORD DOCUMENT_S3_ACCESS_KEY DOCUMENT_S3_SECRET_KEY` (expected: the 12 generated secrets and the committed `ops@example.com`). `ok secrets are long enough and distinct`. FAIL no signing key (path absent on this machine). FAIL TLS row (section 3). Laptop-only FAILs: disk (4 GB free) and inodes (99-100%: BSD `df` column). Warns: no off-box backup, `EMAIL_TRANSPORT=log`, billing run off, Razorpay empty, ops-monitor state dir absent, minio upstream not run by core-only. Memory ok at 2012M / 3862M (52%). |
| **2. filled, all ten profiles, no ceiling** | 1 | key, placeholder and strength rows all ok; **FAIL memory: 4112M of 3862M (106%) - no `EDGE_BUDGET_ACCEPTED_MB`**; FAIL TLS row; laptop disk/inodes |
| **3. as 2 with `EDGE_BUDGET_ACCEPTED_MB=4112`** | 1 | memory becomes `warn ... OVER THE HARD BUDGET (100%), accepted: ... covers it`; only the TLS row and laptop rows remain |
| 4. as 2 with `=4111` | 1 | `FAIL memory ... over the owner-accepted ceiling` |
| 5. as 2 with `=4112MB` | 1 | `FAIL EDGE_BUDGET_ACCEPTED_MB (4112MB) must be a whole number of megabytes` |
| 6. `EDGE_PROFILES=storefront` over a ten-profile `COMPOSE_PROFILES` | 1 | `COMPOSE_PROFILES overridden by EDGE_PROFILES: storefront`; memory 2242M (58%) |
| 7. `EDGE_PROFILES` line deleted | 1 | `every key ... is set` (optional-absent works) |
| 8. `EDGE_PROFILES=inventory, storefront` (unquoted) | **127** | `./.env: line N: storefront: command not found`, no footer |
| 9. `OPS_ALERT_EMAIL=info@opsgrids.com and zionna@opsgrids.com` (unquoted) | **127** | `and: command not found`, no footer |
| 10. `OPS_ALERT_EMAIL=info@opsgrids.com,zionna@opsgrids.com` | 1 (other rows) | `ok ops-monitor alerts email: ...` |
| 11. `ADMIN_GATEWAY_HOP_KEY=tooshort` | 1 | `FAIL ADMIN_GATEWAY_HOP_KEY is 8 bytes: it must be at least 32` |
| 12. hop key equal to the internal token (both throwaway) | 1 | `FAIL ADMIN_GATEWAY_HOP_KEY must differ from INTERNAL_SERVICE_TOKEN` |
| 13. `ADMIN_GATEWAY_HOP_KEY=` (present, empty) | 1 | `FAIL missing or empty in .env: ADMIN_GATEWAY_HOP_KEY`; the later rows still ran (no silent abort) |
| 14. the new example against plain **INT's** preflight (no slices) | 1 | `FAIL missing or empty in .env: EDGE_PROFILES EDGE_BUDGET_ACCEPTED_MB BILLING_TAX_CODE BILLING_TAX_JURISDICTION` - **merge order matters** (below) |
| 15. TLS-row fix applied to a scratch copy of preflight | 1 | the TLS row is gone; only laptop disk/inode rows remain (with a filled env) |

In every run exit 1 came from the placeholders (run 1), the memory decision (runs 2, 4, 5), the strength rows the run
deliberately broke (11-13), the TLS row, or laptop-only rows. **Nothing else is missing or invalid.**

Also run: `docker compose -f compose.prod.yml --env-file <temp env> config -q` for no profiles and all ten profiles:
exit 0 both; and with the example itself as `--env-file` (what a repo test does): exit 0. `tests/infra` in the final
union with the new example: 236 passed, 3 failed (section 9); with the union's own unannotated example: the same
three. `tests/infra` on the audit branch itself (INT + these two files): 115 passed. While building the example a run
of `tests/infra` also caught that the release's own test forbids naming the MFA flag in `.env.example` (5.2); the first
draft did, and no longer does.

**Merge order.** `.env.example` here describes the final release. Merged **before** the slices, INT's own preflight
demands four keys it does not yet allow to be empty (run 14). Merge `slice/env-audit` **after** MH, OC and BR.

## 9. Cross-slice observations (not environment variables)

Seen while building the union; each needs an owner's call. The union was built by hand from five pending branches, so
a failure below may be partly an artefact of that merge; the ones marked *real* reproduce from the branches' own files.

- *Real.* `tests/infra/test_planned_headroom.py::test_runbook_ceiling_example_is_the_number_preflight_would_print`:
  the ten-profile set prices at 4112 but the RUNBOOK example says 3894.
- *Real.* `tests/infra/test_metrics_never_routed.py`: OC adds `/api/v1/admin/metrics` to
  `services/admin-gateway/routes.json`; OPM's lint refuses any route table naming a path ending `/metrics`.
- *Artefact of my merge.* `tests/infra/test_io_outside_transaction.py` flags `services/payment/app/consumers.py:89`;
  I took INT's side of the io-outside-txn conflicts in payment code.
- *Real, small.* Two compose defaults for `EDGE_RELAY_SCHEMAS` differ on INT: edge-relay's list includes `lead`
  (`INT:compose.prod.yml:710`), ops-monitor's does not (`:770`), so its outbox check does not cover the `lead` schema.
- Resolved during the audit: (1) crm-leads added a `lead` API container and a `customer-worker` queue that ops-monitor's
  derived lists lacked; INT's `6c3196f` fixed it. (2) auth would have had two Alembic heads
  (`0c1a721d4214` from domain-verification, `5ba575e36b1a` from OC) and `INT:remote.sh:190` runs `alembic upgrade
  head`; OC `94e412b` adds the merge revision `f8e432a62a84` and the final union has one head (static parse of the
  revision files; every other service also has one). (3) OC's compose `:?` on the hop key would have broken a repo test
  that renders compose with `.env.example`; `94e412b` relaxed it to `:-`. (4) The manual post-`up` MFA reseal became a
  deploy step (5.2).

## 10. What could not be determined from the repository

- The production `.env`: which keys and values it has, which ref it last deployed. The VM was not touched.
- Whether any production customer has legacy plaintext MFA rows (the count statement in 5.2 is the slice's own; it was not run against any database).
- The exact memory figure the real deploy will print: it depends on what runs at deploy time. 4112 is the
  declared-profiles figure on a box with nothing else priced.
- What `mc admin user add` does with two empty strings; MinIO's own credential length limits beyond 8 characters;
  MinIO credential rotation (the RUNBOOK has no procedure).
- Behaviour of `smtplib` / `ssl` inside the 3.12 image (all stdlib checks ran on the audit machine's 3.13.2);
  which spellings pydantic accepts for booleans in the production image (verified on the audit machine's pydantic).
- Whether the WhatsApp inbound webhook route reaches notification through the gateway and Caddy after
  the whatsapp-inbound merge (`664989b`): not checked here.
- Whether the Deploy workflow would run at all (Actions billing state).
- The heads of the pending slices move (five of them moved during this audit); re-read them before acting on a number here.

## 11. Regenerating this file

When a slice head moves: re-run `git fetch`, then for each branch above
run `git diff HEAD_READ origin/BRANCH -- infrastructure/edge/.env.example infrastructure/edge/preflight.sh infrastructure/edge/compose.prod.yml`
(HEAD_READ is the SHA in section 0). A change to the set of uncommented keys, to `optional_empty` /
`optional_absent`, or to a `${VAR}` interpolation means this file and `.env.example` need updating. The key-set
check is `diff <(sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' infrastructure/edge/.env.example | sort) <(... same on the
merged tree ...)`. Then rebuild the union (merge each slice into a scratch worktree, union the infrastructure
files), run its `preflight.sh` against a temp `.env` as in section 8, and run `tests/infra`.
