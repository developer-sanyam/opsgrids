# Edge environment: source, generation and configuration matrix

For every variable the edge deploy reads: what it is, who consumes it, the exact accepted syntax (read from the
parser, not inferred from the name), where its value comes from, how to make or obtain it, whether it already
exists on the box, and how far it has been verified. Written for the **manual** deployment: the owner ssh's to
the box as `azureuser` (from the owner's instruction; the repo records only that Azure's provisioning account is `azureuser`, INT:rb:1106) and deploys with
`make deploy HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com REF=<ref>` (GitHub Actions is billing-blocked).

Companion files: [`ENV_SETUP_CHECKLIST.md`](ENV_SETUP_CHECKLIST.md) (the step-by-step),
[`../../infrastructure/edge/.env.example`](../../infrastructure/edge/.env.example) (the complete annotated example),
and the older [`env-reference.md`](env-reference.md), which this document supersedes where they disagree.

**Rules of evidence used throughout.**

- No production value of any kind is in this repository, in this file, or in `.env.example`. Placeholders only.
- The production VM, every deployment secret store and every external service were not touched. The box's own
  `.env` was **not read**, so nothing below says what the box actually contains. Where the repository does not
  say, the row says **"not determinable from the repo"**.
- Every fact cites `BRANCH:file:line` on a named branch at the heads in section 1. The citations are produced
  mechanically (each is located by its text on that branch when this file is generated, and generation fails if
  the text is missing), so none is stale at this snapshot. The citations added in the fix pass (section 13.6) were checked by hand against the same heads; `prod:` means commit `47143a0`.
- Anything about a provider's own dashboard or behaviour is marked **provider-side, not verified in repo**.
- Two words are kept strictly apart. **"Preflight structurally valid"** means the presence/shape check passed
  on the value. **"Production credential verified"** means a real value was confirmed working by a live call or
  by the running service. **No row in this document is in the second state.** A placeholder or a fake value
  passing preflight never means a variable is resolved.

## 1. Provenance: the heads that were read

Snapshot: 2026-09-24 (IST), after `git fetch origin`. **The heads moved repeatedly while this was being
written** (integration `1706135` -> `13c7181` -> `193b529` -> `ce35c77` (the memory-headroom merge) -> `5320848` (the io-outside-txn merge);
memory-headroom `c159a3e` -> `9ceb85a` -> `73fbfea` -> `0ff2954`; billing-run `07ea4e2` -> `e77d521`; io-outside-txn `2108573` -> `d0b3dab` -> `ed15f27`; engage-campaigns-ui `c47f976` ->
`3b9f260`; operator-console-backend `94e412b` -> `278aa78`). The phase-1 table in `env-reference.md` describes
the older heads; several of its statements changed (section 11, item 1). Re-read the heads before acting on any
number here, and regenerate when they move.

| Abbrev | Ref | Head read (full SHA) | What it is |
|---|---|---|---|
| INT | `origin/release/backlog-complete` | `5320848f770b9d960c6b5106047f95cf31b6f967` | Integration, **the final release**. The merge commit of `ce35c77` (memory-headroom) and io-outside-txn `ed15f27`, so it contains everything below (OC, BR, ECU, MH, IO, ops-monitor, domain-verification, crm-leads). |
| PRE | commit `193b529` | (fixed) | Integration **before** the memory-headroom merge (first parent of `ce35c77`). Used only where the comparison to the earlier behaviour is the point (runs R7a, R7b). |
| MID | commit `ce35c77` | (fixed) | Integration after the memory-headroom merge and before the io-outside-txn merge (first parent of INT): the head my earlier passes read. Its tree equals the memory-headroom slice's. |
| MH | `origin/slice/memory-headroom` | `0ff2954d0f759b446baa7f7050bf60e80ec3b65d` | **Merged into INT at `ce35c77`** (second parent). Adds `EDGE_PROFILES`, `EDGE_BUDGET_ACCEPTED_MB`, the bounded memory ceiling, `COMPOSE_PROFILES=*` expansion, the `.env` pre-check, the undefined-profile FAIL, `RUN_SWEEPS_IN_API` and the Caddyfile catch-all fix in preflight. Its final fixup `0ff2954` changed how `.env` is read (section 11, item 2). |
| IO | `origin/slice/io-outside-txn` | `ed15f270a8454b53348dd82e3bbeea403ec4b194` | **Merged into INT at `5320848`** (second parent). No `.env.example` key and no compose variable: two comment blocks in `compose.prod.yml`, defaulted Settings tunables and start-up validators (section 11, item 10). Its own tests pass on INT (section 13.4). |
| BR | `origin/slice/billing-run` | `e77d521bde245349a79f1a3bd775422c61900d2a` | Merged into INT at `193b529`. `BILLING_TAX_*`, `BILLING_RUN_ENABLED`, `BILLING_RUN_NOT_BEFORE`. |
| ECU | `origin/slice/engage-campaigns-ui` | `3b9f2606807d5f5d756926607505a1139ca052d8` | Merged into INT. No env change (frontend and a notification selector). |
| OC | `origin/slice/operator-console-backend` | `278aa784e4bdaf94182cdb925880acf2e2451d8f` | Merged into INT at `13c7181`. `ADMIN_GATEWAY_HOP_KEY`, the deploy-time MFA reseal, the secret-strength check. |
| main | `origin/main` | `e3c525ab9024738f49cd84c8e12b9088168284cb` | Baseline: 46 keys in the edge example. |
| prod | commit `47143a0` | (fixed) | The ref the RUNBOOK says the production box runs (RUNBOOK 'What this release costs'); its `.env.example` has 44 keys. |
| env-audit | `origin/slice/env-audit` | `5487a2c` at the start (phase 1), `5c48e76` after phase 2, then the fix pass of section 13.6 | The branch this document is committed on. |

`ops-monitor` (`9bb0eff`), `domain-verification` (`213ebdb`) and `crm-leads` (`7a3f587`) are merged into INT and
were not re-read as separate branches.

**Fix pass.** This document was corrected on 2026-09-24 after three adversarial reviews (parsers and syntax; secrets and security claims; completeness and categories). Every finding was re-verified against INT `5320848` before
it was accepted or rejected; the experiments are in section 13.6. Heads read for the fix pass: INT `5320848f770b9d960c6b5106047f95cf31b6f967`, MH `0ff2954d0f759b446baa7f7050bf60e80ec3b65d`, OC `278aa784e4bdaf94182cdb925880acf2e2451d8f`,
BR `e77d521bde245349a79f1a3bd775422c61900d2a`, IO `ed15f270a8454b53348dd82e3bbeea403ec4b194`, ECU `3b9f2606807d5f5d756926607505a1139ca052d8`, main `e3c525ab9024738f49cd84c8e12b9088168284cb`, prod `47143a06542cf251d1e7d3e0b7613620703e72ee`.

**How the final release was read.** The final release is integration itself: `INT` (`5320848`) contains MH, IO and everything else, so no scratch merge was needed.
A scratch copy of it outside the repository (never committed or pushed) is what every preflight run and the `tests/infra` run in section 13 used, and `PRE` (`193b529`) is
used where the comparison with the earlier behaviour is the point. Two earlier facts still hold and are what made the last re-baseline cheap: at `ce35c77` the tree of `INT` was
identical to the tree of `MH` (`git diff origin/slice/memory-headroom origin/release/backlog-complete` was empty for the whole repository), and between `ce35c77` and `5320848` the only files
under `infrastructure/`, `tests/infra/` or `docs/deploy/` that changed were `compose.prod.yml` (two comment blocks), two `tests/infra` files and no env file.

**Path aliases used in citations.** A bare file name is under `infrastructure/edge/`.

| Alias | Path | Alias | Path |
|---|---|---|---|
| `pf` | `infrastructure/edge/preflight.sh` | `rs` | `infrastructure/edge/remote.sh` |
| `cp` | `infrastructure/edge/compose.prod.yml` | `rb` | `infrastructure/edge/RUNBOOK.md` |
| `ex` | `infrastructure/edge/.env.example` | `mj` | `infrastructure/edge/memory-budget.json` |
| `ss` | `infrastructure/edge/lib/secret-strength.sh` | `mr` | `infrastructure/edge/lib/mfa-reseal.sh` |
| `ap` | `infrastructure/edge/lib/active-profiles.sh` | `mb` | `infrastructure/edge/lib/memory-budget.sh` |
| `bk` | `infrastructure/edge/backup.sh` | `cf` | `infrastructure/edge/caddy/Caddyfile` |
| `sd` | `infrastructure/edge/ssh-deploy.sh` | `pw` | `infrastructure/edge/publish-web.sh` |
| `pi` | `infrastructure/edge/postgres-init/30-role-passwords.sh` | `mk` | `Makefile` |
| `pc` | `libs/platform-core/src/platform_core` | `om` | `libs/platform-core/src/platform_core/ops_monitor` |
| `adr13` | `docs/adr/0013-self-serve-products-and-the-one-box-deployment.md` | | |

## 2. Legend

**Categories.** Every variable is in exactly one.

| Cat | Meaning | Rule used |
|---|---|---|
| **A** | Generate locally | A secret we create ourselves with a command; no outside party issues it. |
| **B** | Obtain from an external provider or service | A credential or identifier only an outside service can issue, or one that names an outside resource (a host, endpoint, region, bucket, account or key id, or a secret). |
| **C** | Choose and configure ourselves | A non-secret setting we set within the syntax the parser accepts (given in the row). A protocol knob such as `SMTP_PORT` or `SMTP_USE_TLS` is C even when the provider dictates the value: we choose it to match; the host, login and password are B. Also C: the one-off `-e` copies the operator makes for a by-hand run (`S3_ORPHAN_*`). |
| **D** | Already available in deployment infrastructure | The value already exists on the running box (created at first deploy per the RUNBOOK, or required non-empty by the production ref's preflight) or is computed by the deploy tooling. **Keep it; never regenerate it.** Existence is *inferred*: the key is in the production ref's `.env.example`, that ref's preflight refuses a deploy without it, and ADR-0013 records that box running. It is **not verified**; the box was not read, so every 'already exists' cell says 'not determinable from the repo' first. The `source:` tag in `.env.example` describes a FRESH box (GENERATE or CHOOSE); the matrix category says what to do on THIS box, so some D keys carry a GENERATE or CHOOSE tag there. |
| **E** | Requires an architectural or migration decision | Filling or changing it needs an owner decision that changes what runs, what is billed, or how data is stored, or is coupled to a data migration. That includes a secret whose only use is a rotation (`PII_DECRYPT_KEYS`). `COMPOSE_PROFILES` is E because changing it is coupled to live activations and the memory decision; `EDGE_PROFILES` is C because leaving it empty is the documented state and it only overrides. |

**Verification status vocabulary.**

| Term | Meaning |
|---|---|
| Preflight structurally valid | The presence / placeholder / length / shape check preflight applies passed. Proved by the runs in section 13 (`R1` ... `R7c`). |
| Production credential verified | A real value confirmed working by a live call or by the running service's behaviour. **Never claimed here.** |
| needs an owner live call | The only safe way to verify is a call to a provider or to the live box that only the owner should run; the call is given in the checklist and it sends no message and changes nothing. |

**Columns.** "Required?" is the preflight rule: every uncommented `KEY=` line of `.env.example` must exist in the
box's `.env` (the LINE, not necessarily a value); non-empty unless the key is in `optional_empty`
(INT:pf:42); and for `EDGE_PROFILES` / `EDGE_BUDGET_ACCEPTED_MB` even the line
may be absent (INT:pf:81). "Cat" is A-E above. "P" in the
index marks a process-only variable.

## 3. The matrix: every key of `infrastructure/edge/.env.example`

66 keys (the uncommented `KEY=` lines; preflight's required set). One row each, one category each.
Wide tables: scroll sideways. "Already exists" rows also state whether the key is in the production ref
`47143a0`'s `.env.example` (44 keys): a key that is not there is not expected on the box by the RUNBOOK's
own rule. Every 'already exists' cell is an inference: the box's `.env` was not read (section 12, item 1).

### 3.1 Identity and TLS

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `EDGE_HOST` | D | Required, non-empty | no | caddy, storefront, ops-monitor (cert probe SNI), preflight.sh, remote.sh, publish-web.sh | Bare hostname, no scheme or path. `make deploy CONFIRM=<value>` must equal it exactly: remote.sh reads the LAST `^EDGE_HOST=` assignment through `dotenv_get`, which strips quotes, an inline comment, spaces and CR (INT:rs:143), but reads only a line that starts with `EDGE_HOST=` at column 0: `export EDGE_HOST=...` or an indented line reads as unset (assert-host then reports `<unset>` and refuses, which fails safe; run Q5). PUBLIC_ASSET_BASE_URL must be `https://<value>/<S3_PUBLIC_BUCKET>` (INT:pf:315). | `edge.opsgrids.com` (committed default) | The box's DNS name (RUNBOOK step 2). The owner's deploy command uses `CONFIRM=edge.opsgrids.com`, so the box's value is expected to be `edge.opsgrids.com` | Already set; do not change it. Read-only check: `make edge-preflight HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com` fails at its first step if the box says otherwise (INT:sd:52). | no | The owner's deploy command uses `CONFIRM=edge.opsgrids.com` (from the owner's instruction, not in the repo), which implies that value; it was not read from the box, so it is not determinable from the repo. [Key in the production ref 47143a0's `.env.example`: yes.] | A wrong or changed value breaks the wrong-box interlock and every URL derived from it; changing it means changing PUBLIC_ASSET_BASE_URL and republishing the bundles. | Preflight structurally valid: presence and placeholder check. Production value verified by evidence that needs no credential: `assert-host` (INT:rs:129-151) passes only when the box's own value equals CONFIRM. |
| `EDGE_RESERVED_IP` | B | Line must exist; may be empty | no | storefront (`edge_reserved_ip`, INT:services/storefront/app/config.py:102) | IPv4 dotted quad, or empty. Preflight only warns for RFC 5737 documentation ranges (192.0.2.*, 198.51.100.*, 203.0.113.*) and otherwise prints ok for ANY non-empty text: it does not check the shape (INT:pf:276-282). | empty (committed default) | The cloud provider (Azure, per INT:rb:1106): the VM's public IP address resource. Provider-side, not verified in repo | Azure portal, the VM's Public IP address resource: confirm it is Static and copy the address. The deploy target in the owner's command is 20.219.18.84; whether that address is a static reservation is not determinable from the repo. The repo records the Standard-SKU/static confirmation as an open owner action (INT:docs/adr/0012-custom-domains.md:1793-1797; INT:docs/adr/open-questions-register.md:72, item 36); the portal check is provider-side, not verified in repo. | no | Not determinable from the repo. Inferred present, not verified: the key is in the production ref 47143a0's `.env.example` (prod:infrastructure/edge/.env.example:22) and that ref's preflight refused it empty or as the documentation address `198.51.100.7` (prod:infrastructure/edge/preflight.sh:37-72), so a real address is expected; the release's own preflight allows it empty, so **do not blank an existing value**. [Key in the production ref 47143a0's `.env.example`: yes.] | Public contract: apex merchants put it in their DNS. A wrong value prints a wrong address in the merchant UI. Not a secret. | Preflight structurally valid: only the documentation-range warning (proved: run R3p). Production value verified: no; compare with `dig +short edge.opsgrids.com` and the provider console. |
| `EDGE_WEB_ROOT` | C | Required, non-empty | no | caddy (read-only bind mount), publish-web.sh, preflight.sh (bundle warning) | Absolute host path. Bundles live in `<path>/releases/<utc-ts>/` with a `current` symlink (INT:cp:194); publish-web.sh reads the line raw over ssh: the FIRST `^EDGE_WEB_ROOT=` line, with no unquoting, comment stripping or CR handling (INT:pw:157), so a quoted value (`EDGE_WEB_ROOT="/srv/opsgrids/web"`) becomes a path that starts with a double quote and publishes to the wrong place while Caddy serves the real one: write it bare, no quotes, no inline comment. | `/srv/opsgrids/web` (committed default) | Free choice on the host | Keep the default; it exists on the box per RUNBOOK step 3. | no | Directory created at box setup (INT:rb:119); not verified. [Key in the production ref 47143a0's `.env.example`: yes.] | Served read-only to the internet by Caddy: nothing private may be placed in it. | Preflight structurally valid: presence. A missing `current/index.html` is only a warning (INT:pf:754-761). Production value verified: not applicable (configuration, no provider) |
| `ACME_EMAIL` | C | Required, non-empty and must not contain `example.com` | no | caddy (ACME account contact) | An email address. The committed `ops@example.com` matches preflight's `*example.com*` placeholder glob and is refused (INT:pf:247). | `info@opsgrids.com` satisfies preflight; any real inbox a human reads | Choose | Set to a monitored inbox. Let's Encrypt's expiry warning for a merchant domain is otherwise the only signal. | no | Not determinable from the repo. Inferred present, not verified: the key is in the production ref 47143a0's `.env.example` (prod:infrastructure/edge/.env.example:31) and that ref's preflight refused the placeholder `ops@example.com` (prod:infrastructure/edge/preflight.sh:37-72), so a real inbox is expected; replace the value only if it is still the placeholder (checklist step 5.1 guards this). [Key in the production ref 47143a0's `.env.example`: yes.] | Not a secret; goes to Let's Encrypt. | Preflight structurally valid: not a placeholder (proved: run R3o). Production value verified: not applicable (configuration, no provider); whether the inbox is read is not checkable. |
| `TLS_PLATFORM_HOSTS` | C | Required, non-empty | no | storefront (tls-authorize issuance gate), preflight.sh | Comma-separated exact hostnames, no spaces, lower case, no wildcard. The container tolerates spaces and case (INT:services/storefront/app/config.py:92; probe: `A.opsgrids.com, b.opsgrids.com` parses to two lower-case names) but preflight matches `,name,` literally (INT:pf:615-621), so write it clean. | `edge.opsgrids.com,api.opsgrids.com,app.opsgrids.com,ops.opsgrids.com,people.opsgrids.com,revenue.opsgrids.com,pulse.opsgrids.com,projects.opsgrids.com,support.opsgrids.com,marketing.opsgrids.com,finance.opsgrids.com,it.opsgrids.com,store.opsgrids.com,engage.opsgrids.com` | Choose: every platform name in DNS (RUNBOOK step 2) | Keep the committed default; it lists the 12 named Caddy sites (INT:cf:193-406) plus edge and api. | no | Committed non-secret default. The box's value was not read (not determinable from the repo) and may be shorter than the committed one: a name the Caddyfile serves but the box's list omits is a named preflight FAIL (checklist step 7.3). [Key in the production ref 47143a0's `.env.example`: yes.] | A name missing here gets no certificate: a TLS error instead of a page. | Preflight structurally valid: each literal `https://<name> {` Caddy site (twelve) must be listed; the EDGE_HOST site (`https://{$EDGE_HOST} {`) and `api.opsgrids.com` are NOT checked (INT:pf:615-621; run Q10 passes with both missing), so keep `edge.opsgrids.com` and `api.opsgrids.com` in the list by hand (RUNBOOK: every platform name in its DNS table, INT:rb:98,114). The catch-all `https:// {` (INT:cf:429) used to read as an empty name, so this row failed on every run of `193b529` (proved: run R7a); fixed by the merged memory-headroom (INT:pf:621; the row is gone in run R7c). Production value verified: not applicable (configuration, no provider) |

### 3.2 Database, auth and broker

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `POSTGRES_PASSWORD` | D | Required, non-empty | YES | postgres (superuser `opsgrids`); backup.sh and runbook psql sessions; no service connects with it | Any string; hex recommended. Preflight checks presence and placeholders only. It takes effect only on a fresh data volume (postgres init), so changing it later in `.env` does not change the stored password. | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generated on the box at first deploy (RUNBOOK step 5) | Already exists. Do NOT regenerate. Fresh box only: `openssl rand -hex 32`. | Fresh box only: `openssl rand -hex 32` | Not determinable from the repo (the box's `.env` was not read). Inferred present, not verified: the key is in the production ref 47143a0's `.env.example`, that ref's preflight refuses a deploy without a non-empty value (prod:infrastructure/edge/preflight.sh:37-72), and ADR-0013 records that box running (INT:adr13:365-368). [Key in the production ref 47143a0's `.env.example`: yes.] | Superuser. Never reuse a development value. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production credential verified: NO. A preflight pass proves presence and not-a-placeholder only: there is NO length or strength check on this key (checklist step 2.5b prints its length), and it never proves that the running service holds this value. |
| `OPSGRIDS_DB_PASSWORD` | D | Required, non-empty | YES | every service (DATABASE_URL, INT:cp:62); postgres-init role passwords | Letters and digits ONLY: the postgres init script refuses anything matching `*[!A-Za-z0-9]*` (INT:pi:26-33) because the value goes into a URL. `openssl rand -hex 32` satisfies it. The script runs once on a fresh volume; rotating means `ALTER ROLE` by hand (INT:pi:13-16). | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generated on the box at first deploy | Already exists. Do NOT regenerate on a running box: the roles already carry it and every service would fail to log in. Fresh box only: `openssl rand -hex 32`. | Fresh box only: `openssl rand -hex 32` | Not determinable from the repo (the box's `.env` was not read). Inferred present, not verified: the key is in the production ref 47143a0's `.env.example`, that ref's preflight refuses a deploy without a non-empty value (prod:infrastructure/edge/preflight.sh:37-72), and ADR-0013 records that box running (INT:adr13:365-368). Changing it in `.env` alone breaks every service's database login. [Key in the production ref 47143a0's `.env.example`: yes.] | One password shared by every `opsgrids_*` role; isolation is by grants, not passwords (INT:pi:7-11). | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259) (the alphanumeric rule is enforced by the postgres init script, not by preflight). Production credential verified: NO. A preflight pass proves presence and not-a-placeholder only: there is NO length or strength check on this key (checklist step 2.5b prints its length), and it never proves that the running service holds this value. |
| `INTERNAL_SERVICE_TOKEN` | D | Required, non-empty | YES | every service (x-service-env, INT:cp:83) | >= 32 UTF-8 bytes (`len(value.encode())`), must not start `dev-only-` or `ci-only-`, must differ from ADMIN_GATEWAY_HOP_KEY. Enforced at service startup in production/staging/uat (INT:pc/config.py:250-281) and mirrored by preflight (INT:ss:23-32). Probes: 31 bytes refused, 64 hex accepted, `dev-only-` + 55 chars refused. | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generated on the box at first deploy | Already exists. Do NOT regenerate on a running box. Fresh box only: `openssl rand -hex 32`. | Fresh box only: `openssl rand -hex 32` | Not determinable from the repo (the box's `.env` was not read). Inferred present, not verified: the key is in the production ref 47143a0's `.env.example`, that ref's preflight refuses a deploy without a non-empty value (prod:infrastructure/edge/preflight.sh:37-72), and ADR-0013 records that box running (INT:adr13:365-368). The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | Signs the 60-second service tokens (INT:pc/config.py:144). A leak lets a caller mint service tokens. | Preflight structurally valid: presence, placeholder, `wc -c` >= 32 bytes, differs from the hop key (proved: runs R3e-R3g). Production credential verified: NO. A preflight pass proves presence, not-a-placeholder and a 32-byte floor only, never strength or that the running service holds this value. |
| `ADMIN_GATEWAY_HOP_KEY` | A | Required, non-empty (compose passes it as `${...:-}` on purpose, so an empty value is a preflight FAIL, not a compose abort) | YES | auth ONLY (INT:cp:588). admin-gateway, the signer, is not in compose.prod.yml | >= 32 UTF-8 bytes; not `dev-only-`/`ci-only-`; must differ from INTERNAL_SERVICE_TOKEN in every environment; auth refuses to start without it in production/staging/uat (INT:services/auth/app/config.py:67-90; INT:pc/config.py:271-281). An empty string is read as unset (INT:pc/config.py:191). Used verbatim as the HMAC-SHA256 key, not hex/base64-decoded (INT:pc/internal_auth.py:175). Probes: unset and empty refused; 31 bytes refused; 32 bytes accepted; equal to the token refused. | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generate on the box (new in this release) | `openssl rand -hex 32` in its own invocation (never the INTERNAL_SERVICE_TOKEN value); use the fill helper in the checklist so the value is never printed. | YES: `openssl rand -hex 32` | Not determinable from the repo. New in this release: not in the production ref 47143a0's `.env.example` and not in main, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Defence in depth behind the route table's block of `/api/v1/auth/operator`. 256 bits of hex is the right shape; `-hex 16` passes the 32-byte check but is only 128 bits. | Preflight structurally valid: presence, placeholder, >= 32 bytes, differs from INTERNAL_SERVICE_TOKEN (proved: R3e, R3f). Production credential verified: NO. A preflight pass proves presence, not-a-placeholder and a 32-byte floor only, never strength or that the running service holds this value; auth refusing to start is the only later signal. |
| `PII_ENCRYPTION_KEY` | D | Required, non-empty | YES | every service (x-service-env, INT:cp:84); auth also seals MFA TOTP secrets with it | >= 32 UTF-8 bytes, not `dev-only-`/`ci-only-` (INT:pc/config.py:265). Any string is a passphrase: HKDF-SHA256 with a fixed info string derives the AES-256-GCM key (INT:pc/pii.py:93). The blind index used for lookups derives from the CURRENT key only, so rotation keeps old rows readable through PII_DECRYPT_KEYS but breaks blind-index lookups (INT:pc/pii.py:184). | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generated on the box ONCE, before any data exists | Already exists. NEVER regenerate on a box that holds sealed data. Fresh box only: `openssl rand -hex 32`, then back it up off the box (the RUNBOOK's step 5 generates it on the box, INT:rb:168, so 'already exists' is an inference about the running box, not a RUNBOOK statement). | Fresh box only: `openssl rand -hex 32` | Not determinable from the repo (the box's `.env` was not read). Inferred present, not verified: the key is in the production ref 47143a0's `.env.example`, that ref's preflight refuses a deploy without a non-empty value (prod:infrastructure/edge/preflight.sh:37-72), and ADR-0013 records that box running (INT:adr13:365-368). The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | Losing it loses every sealed PII value and every sealed MFA secret. Back it up off the box. Rotation is not written up as a procedure in the RUNBOOK. | Preflight structurally valid: presence, placeholder, >= 32 bytes (proved: R3g). Production credential verified: NO. A preflight pass proves presence, not-a-placeholder and a 32-byte floor only, never strength or that the running service holds this value. |
| `PII_DECRYPT_KEYS` | E | Line must exist; may be empty | YES | every service (x-service-env, INT:cp:85) | Comma-separated old passphrases; split on `,`, each item stripped, empties dropped (INT:pc/config.py:182; probe: `k1, k2,,` -> two items). A passphrase must not contain a comma. Empty = no rotation in progress. | empty | Derived: the previous PII_ENCRYPTION_KEY value(s), only while rotating | Leave empty. Setting it is part of a rotation, which needs its own decision (blind-index lookups do not survive a rotation): category E, and the only secret in it. | no | The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | Holds old key material: as sensitive as the key itself while set. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67). Production credential verified: NO. A preflight pass proves presence and not-a-placeholder only: there is NO length or strength check on this key (checklist step 2.5b prints its length), and it never proves that the running service holds this value. |
| `JWT_PRIVATE_KEY_HOST_PATH` | D | Required, non-empty; the FILE must exist | path: no / FILE: YES | auth ONLY (read-only bind mount to `/run/secrets/jwt-ed25519.pem`, INT:cp:590) | Absolute host path to an UNENCRYPTED PKCS8 Ed25519 PEM. Preflight FAILs unless the file exists with mode 400, 440, 600 or 640 (INT:pf:322-329). The auth container's uid must be able to read it (INT:rb:206). | `/srv/opsgrids/secrets/jwt-ed25519.pem` (committed default) | Generated on the box at first deploy (RUNBOOK step 5) | Already exists. Do NOT regenerate: every live token would stop verifying. Fresh box only: `openssl genpkey -algorithm ed25519 -out /srv/opsgrids/secrets/jwt-ed25519.pem && chmod 600 /srv/opsgrids/secrets/jwt-ed25519.pem`, then give the auth container's uid read access: `uid=$(docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T --entrypoint id auth -u); sudo chown "$uid" /srv/opsgrids/secrets/jwt-ed25519.pem` (INT:rb:206-212; a 600 file owned by the deploy user is unreadable by the auth image's non-root uid, and preflight checks only the mode). | Fresh box only: `openssl genpkey -algorithm ed25519 -out <path>; chmod 600 <path>` | Not determinable from the repo (the box's `.env` was not read). Inferred present, not verified: the key is in the production ref 47143a0's `.env.example`, that ref's preflight refuses a deploy without a non-empty value (prod:infrastructure/edge/preflight.sh:37-72), and ADR-0013 records that box running (INT:adr13:365-368). Auth cannot start without the file. [Key in the production ref 47143a0's `.env.example`: yes.] | The only private signing key in the platform. Never the `make devkeys` key. Never copy it off the box except into the offline backup. | Preflight structurally valid: file exists with an accepted mode (proved: R1, R3m); the PEM is not parsed by preflight. Production credential verified: NO. A preflight pass proves only that the file exists with an accepted mode (the PEM is not parsed), never that the running service holds this key; auth starting is the real check. |
| `JWT_KID` | C | Required, non-empty | no | interpolated into every service (INT:cp:82); nothing reads it | Free string. INERT: the only reference is the field declaration (INT:pc/config.py:124); auth derives the real `kid` from the public key's RFC 7638 thumbprint (INT:services/auth/app/keys.py:68). | `prod-1` (committed default) | Choose | Keep the default. | no | Committed default. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | None; changing it changes nothing. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `JWT_ISSUER` | C | Required, non-empty | no | every service (INT:cp:81); auth stamps `iss` (INT:services/auth/app/services.py:279), gateways verify it (INT:pc/auth.py:156) | Free string, an https URL by convention, identical in every service. Changing it invalidates every live token. | `https://auth.opsgrids.com` (committed default) | Choose | Keep the committed value. | no | Committed default. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | Not secret; must stay stable. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `RABBITMQ_PASSWORD` | D | Required, non-empty | YES | rabbitmq (INT:cp:291), every service's broker URL (INT:cp:66), the ops-monitor DLQ probe | Any string; hex recommended because it is embedded in `amqp://opsgrids:<value>@rabbitmq:5672/`. Like the postgres password it takes effect on a fresh broker volume. | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generated on the box at first deploy | Already exists. Do NOT regenerate on a running box. Fresh box only: `openssl rand -hex 32`. | Fresh box only: `openssl rand -hex 32` | Not determinable from the repo (the box's `.env` was not read). Inferred present, not verified: the key is in the production ref 47143a0's `.env.example`, that ref's preflight refuses a deploy without a non-empty value (prod:infrastructure/edge/preflight.sh:37-72), and ADR-0013 records that box running (INT:adr13:365-368). [Key in the production ref 47143a0's `.env.example`: yes.] | Stays on the compose network; never exposed. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production credential verified: NO. A preflight pass proves presence and not-a-placeholder only: there is NO length or strength check on this key (checklist step 2.5b prints its length), and it never proves that the running service holds this value. |

### 3.3 Products and memory

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `COMPOSE_PROFILES` | E | Line must exist; may be empty (empty = core only) | no | docker compose (every call), preflight.sh, remote.sh, ops-monitor (derives its check lists, INT:cp:776) | Comma-separated compose profile names, lower case, NO spaces: `inventory storefront hrms crm pulseboard projects support marketing finance itsm` (ten). A non-empty EDGE_PROFILES replaces it, never unions (INT:ap:60-61). An undefined name is a named preflight FAIL (INT:pf:214; runs R4o, R8d), where before `0ff2954` it was silently priced as core only; ops-monitor refuses a name not matching `^[a-z_][a-z0-9_]*$` at startup (INT:om/config.py:311-321; probes: `HRMS`, `hrms.crm`, `hrms crm` refused). `*` is expanded by preflight and remote.sh (INT:ap:78; INT:pf:192-222; run R4j); it was not on `193b529`. remote.sh reads only a line that starts with `COMPOSE_PROFILES=` at column 0: `export COMPOSE_PROFILES=...` or an indented line is priced as ten profiles by preflight (it `source`s the file) but is empty to remote.sh, which then exports `COMPOSE_PROFILES=""`; an exported-empty value overrides the env-file in docker compose (measured with compose v2.35.1: 21 services instead of 47), so `migrate` and `up` would run core only after a green preflight (INT:rs:56-74,104; runs Q5, Q5b). Write it as `COMPOSE_PROFILES=...` at column 0. | empty (committed default); the box lists all ten | Owner decision: which products this one box sells | Keep the box's existing value (ADR-0013 records all ten). Any change is coupled to the memory decision and to live activations: preflight refuses to drop a profile that has a live activation. | no | ADR-0013 records that the box's `.env` lists all ten profiles (INT:adr13:668-671; INT:adr13:428-429), from a read-only look during review, not re-verified; beyond that not determinable from the repo. [Key in the production ref 47143a0's `.env.example`: yes.] | Not secret. `docker compose up` does not stop containers of a profile you remove; an orphan keeps its memory (INT:adr13:399). | Preflight structurally valid: line present; profiles priced by `docker compose config --services` (runs R4a-R4o). Production value verified: not applicable (configuration, no provider) |
| `EDGE_PROFILES` | C | Optional: the line may be ABSENT or empty (`optional_absent`) | no | preflight.sh and remote.sh ONLY (exported to compose as COMPOSE_PROFILES); no container reads it | Same syntax as COMPOSE_PROFILES. Non-empty wins. remote.sh reads the LAST `^EDGE_PROFILES=` assignment through `dotenv_get` (INT:rs:87: quotes, an inline ` # comment`, spaces and CR are handled); preflight `source`s the file, where the last assignment also wins (run R8m). Unquoted whitespace or a shell metacharacter, text after a closing quote, and CRLF line endings are named FAILs before the file is read (INT:pf:101-154; runs R4m, R8a-R8c, R8l); unquoted whitespace exited 127 on `193b529` (run R7b). As for COMPOSE_PROFILES, remote.sh reads only a line that starts with `EDGE_PROFILES=` at column 0. Only preflight.sh and remote.sh honour this key: a `docker compose ... --env-file .env` you run by hand reads only COMPOSE_PROFILES, so with a non-empty EDGE_PROFILES such commands address a different profile set than the deploy (INT:ap:60-67; INT:rs:84-109). | empty (committed default) | Choose | Leave empty. The box sets COMPOSE_PROFILES and no EDGE_PROFILES (ADR-0013); the override adds nothing unless a profile must run ahead of its first sale. | no | Not determinable from the repo. New key: not in the production ref 47143a0's `.env.example`. ADR-0013 records that the box's `.env` had no `EDGE_PROFILES` at review time (INT:adr13:668-671; a read-only look, not re-verified). [Key in the production ref 47143a0's `.env.example`: no.] | Not secret. | Preflight structurally valid: absent or empty accepted (INT:pf:81); an undefined name is a named FAIL (proved: R8e); absent and empty accepted (R4i, R4l). Production value verified: not applicable (configuration, no provider) |
| `EDGE_BUDGET_ACCEPTED_MB` | E | Optional: the line may be ABSENT or empty (`optional_absent`) | no | preflight.sh ONLY; no container reads it | A whole number, digits only, **no leading zero** (`04581` and `00` FAIL) and at most seven digits, no unit, comma or sign (a bare `0` is accepted and useless; bash drops an unquoted trailing space or ` # comment` before the check); anything else FAILs (INT:pf:396; runs R4e, R4g, R8f, R8g). Unit: the same `<n>M` preflight prints, i.e. the sum of `memory-budget.json` `expected_rss_mb` compared with MemTotal kB / 1024 (INT:mb:27), so MiB in practice. Bounded: above `host.max_accepted_percent` = 125% of MemTotal (4827 on a 3862 MB box) it FAILs (INT:pf:417; INT:mj:72; run R4f). It covers only a total at or below it (INT:mb:94-100; runs R4b, R4c). | empty (committed default); on the merged tree the ten profiles print 4581 | OWNER DECISION, read off preflight's own projection for the commit being deployed | Run `make edge-preflight HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com` first; write the total it prints (RUNBOOK: "exactly the post-deploy total preflight printed for the commit being deployed"). See section 6.3. | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | A blank cheque if mistyped; that is why the 125% cap exists. It is the audit record of accepting a memory overcommit. | Preflight structurally valid: shape, cap and coverage checks run on any value; it never verifies the number is right for the box (proved: R4a-R4h). Production value verified: not applicable (configuration, no provider) |

### 3.4 Email

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `EMAIL_TRANSPORT` | C | Required, non-empty | no | notification, notification-worker (INT:cp:964) | Exactly `log` or `smtp`, case-sensitive: any other value makes `transports.build` raise at startup (INT:services/notification/app/transports.py:370-396; probes: `SMTP`, `Log`, `ses` all refused). Preflight only warns on `log` (INT:pf:511). | `log` (committed default) | Choose | `smtp` once the SMTP credentials work, otherwise `log` (sends nothing). | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | `log` records every rendered message in the service's logs. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `EMAIL_FROM` | C | Required, non-empty | no | notification, notification-worker, ops-monitor (INT:cp:782) | An email address, used as the From header; no format validation in code. It must be an address the SMTP account is allowed to send as (provider-side). | `info@opsgrids.com` (committed default) | Choose | Keep the default if the Google Workspace mailbox `info@opsgrids.com` may send as itself. | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | Sender identity; SPF/DKIM/DMARC live at the DNS registrar (INT:ex:145). | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider); a rejected From is only visible on the first real send. |
| `SMTP_HOST` | B | Line must exist; may be empty | no | notification, notification-worker, ops-monitor | A hostname. An EMPTY value is read as unset (`env_ignore_empty`, INT:pc/config.py:65) and notification silently falls back to `localhost` (INT:services/notification/app/config.py:28; probe confirmed); ops-monitor treats it as not configured (INT:om/config.py:268). | `smtp.gmail.com` (committed default) | The mail provider (Google Workspace for `@opsgrids.com`). Provider-side, not verified in repo | Keep `smtp.gmail.com` for Google Workspace (repo comment: INT:ex:142). Never leave it empty with EMAIL_TRANSPORT=smtp. | no | Committed default. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | Not secret. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67); empty only warns for ops-monitor (INT:pf:551). Production value verified: no; needs the SMTP probe in the checklist (owner-run live call). |
| `SMTP_PORT` | C | Required, non-empty | no | notification, notification-worker, ops-monitor | An integer (pydantic int; ` 587 ` parses, `587abc` refused, `0` and `-1` accepted: no range check). 587 with SMTP_USE_TLS=true (STARTTLS). 465 needs implicit TLS, which only ops-monitor supports (SMTP_USE_SSL=true and SMTP_USE_TLS=false). | `587` (committed default) | The provider's submission port | Keep 587 for Google. | no | Committed default. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | Never 25 for authenticated mail. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259) (not an integer check). Production value verified: not applicable (configuration, no provider) |
| `SMTP_USERNAME` | B | Line must exist; may be empty | no | notification, notification-worker, ops-monitor | String, normally the full mailbox address. Notification logs in only when BOTH username and password are set (INT:services/notification/app/transports.py:249); ops-monitor logs in when a username is set and then needs the password (INT:om/mailer.py:110-116). | `info@opsgrids.com` (committed default) | The Google Workspace mailbox that owns the app password | Set to the mailbox address. | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | Not secret, but it is the login name. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67). Production credential verified: no; needs the SMTP probe. |
| `SMTP_PASSWORD` | B | Line must exist; may be empty | YES | notification, notification-worker, ops-monitor | Opaque string. With spaces: **double** quotes (an unquoted one is a named FAIL, run R5e; exit 127 on `193b529`, run R7b). With `$`, a backtick or a backslash: **single** quotes, because double quotes still expand them (runs Q1-Q4). A value that also holds a single quote: regenerate it. Google shows app passwords with spaces (provider-side, not verified in repo); quote them. | empty | Google Workspace app password for the mailbox (Google Account -> Security -> 2-Step Verification -> App passwords; repo text at INT:ex:143; provider-side, not verified in repo) | Create the app password in the provider's dashboard and paste it directly into `.env` on the box (never into chat or a ticket). | no (issued by the provider) | Not determinable from the repo. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | With a username set and no password every ops-monitor send fails at login; the value is a live credential to a real mailbox. Notification's STARTTLS passes no TLS context, so it does not verify the server certificate or host name (INT:services/notification/app/transports.py:245-251; the stdlib default context is unverified, checked on Python 3.12.3): an on-path attacker between the box and the SMTP host could read this password on the notification path. ops-monitor's own STARTTLS verifies (INT:om/mailer.py:92-106). | Preflight structurally valid: line present; empty allowed (proved: R1 with a fake quoted value passes). Production credential verified: NO. Only a live login proves it: the SMTP login probe in the checklist (owner-run, sends no mail), or `last_send_at` in ops-monitor `/status` after a real alert or the 08:00 UTC digest. |
| `SMTP_USE_TLS` | C | Required, non-empty | no | notification, notification-worker, ops-monitor (one shared key) | A pydantic boolean: true/false, 1/0, yes/no, on/off, t/f, y/n, any case; `maybe` refused (probe). Compose passes `${SMTP_USE_TLS:-true}`. STARTTLS on the plain port. Mutually exclusive with SMTP_USE_SSL (ops-monitor raises at SEND time when both are true, INT:om/mailer.py:92-96; nothing checks at startup or in preflight). | `true` (committed default) | Dictated by the provider's port | `true` with port 587. | no | Committed default. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | With `false`, notification STILL logs in over a plain connection (INT:services/notification/app/transports.py:247-250); only ops-monitor refuses. Notification's `starttls()` passes no TLS context, so it does not verify the server certificate or host name (stdlib default; see SMTP_PASSWORD and section 11, item 15). | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `SMTP_USE_SSL` | C | Line must exist; may be empty | no | ops-monitor ONLY (INT:cp:788); notification has no implicit-TLS path | A pydantic boolean. Implicit TLS from the first byte (port 465). Never true together with SMTP_USE_TLS=true. | `false` (committed default) | Choose: true only for an SMTPS-only provider | Leave `false` for Google on 587. | no | Committed default; new key. [Key in the production ref 47143a0's `.env.example`: no.] | None beyond the TLS pairing. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67). The TLS/SSL exclusivity is not preflight-checked. Production value verified: not applicable (configuration, no provider) |

### 3.5 Payments and billing

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `RAZORPAY_KEY_ID` | B | Line must exist; may be empty (empty = the stub). Used only when `PAYMENT_WIRE=live` | no (a public identifier: Checkout is opened in the browser with it; treat as sensitive anyway) | payment, payment-worker | `str \| None` (`services/payment/app/config.py`); with `PAYMENT_WIRE=live` it must match `^rzp_(test\|live)_[A-Za-z0-9]{6,40}$` and be set together with the secret (a half-set pair stops `payment` starting, whatever the wire). `rzp_live_` is refused unless `PAYMENT_ALLOW_LIVE_KEYS=true`; `rzp_test_` is refused on a live wire when `ENVIRONMENT=production` (every compose service). Preflight mirrors these (`lib/payment-switches.sh`). **Keys never switch the network on**: the adapter is the in-process stub unless `PAYMENT_WIRE=live` (ADR-0032). | empty | Razorpay Dashboard -> Account & Settings -> API Keys (provider-side, not verified in repo) | Generate a test-mode pair in the dashboard for a local run (`tools/payments_smoke_razorpay.py`); leave empty on the box until the owner turns the wire on (RUNBOOK 'Payments: turning a provider on'). | no (issued by the provider) | Not determinable from the repo. [Key in the production ref 47143a0's `.env.example`: yes.] | Anyone with the pair can call Razorpay as the account. Keep test-mode and live-mode keys apart. The id is also returned to the browser by `POST /payment/checkouts` (public by design); the secret is never returned, logged or put in an exception. | Preflight structurally valid: empty allowed; both-or-neither; shape and mode rules when the wire is live. Production credential verified: NO until the owner runs the smoke tool with test keys; the platform makes no call with it while the wire is the stub. |
| `RAZORPAY_KEY_SECRET` | B | Line must exist; may be empty (set together with the key id). Used only when `PAYMENT_WIRE=live` | YES | payment, payment-worker | `SecretStr \| None`. Authenticates the wire (HTTP basic) and verifies the HMAC on a customer's checkout callback (`HMAC-SHA256(order_id\|payment_id)`, constant time) - a precondition for asking Razorpay, never proof of payment. | empty | Razorpay Dashboard -> Account & Settings -> API Keys (shown once at creation; provider-side, not verified in repo) | Copy it once from the dashboard into `.env` on the box; never into chat or a ticket. | no (issued by the provider) | Not determinable from the repo. [Key in the production ref 47143a0's `.env.example`: yes.] | Live financial credential: rotate at the provider if it ever leaves the box. A rejected key trips a latch (`payment.provider.credentials_rejected`, one page, no charge failed): fix the key and restart `payment` and `payment-worker`. | Preflight structurally valid: empty allowed; both-or-neither. Production credential verified: NO. |
| `RAZORPAY_WEBHOOK_SECRET` | D | Required, non-empty (compose passes it with no default, INT:cp:937) | YES | payment, payment-worker | Any string; the HMAC-SHA256 key over the raw webhook body (INT:services/payment/app/providers/razorpay.py:261). No length floor. The service default is the public `dev-only-razorpay-webhook-secret` (INT:services/payment/app/config.py:38) and NOTHING in the service refuses it in production (probe: a production Settings object accepted it; an empty value falls back to it through `env_ignore_empty`). Only PREFLIGHT guards it: it refuses a missing, empty or `dev-only-*`/`CHANGE_ME*` value. | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generated on the box (on a fresh box) and pasted into Razorpay Dashboard -> Webhooks (provider-side, not verified in repo); the value in use on this box, if any, was not read | The checklist's fill helper (step 3.3) fills it only if it is empty or a placeholder and keeps an existing value. To move a value into the Razorpay webhook form without printing it, use the clipboard command in checklist step 4.3. Do not rotate it without changing Razorpay's webhook settings too. | Fresh box, or a deliberate rotation only: `openssl rand -hex 32` | Not determinable from the repo. The key IS in the production ref 47143a0's `.env.example` (prod:infrastructure/edge/.env.example:102) and that ref's preflight required it non-empty and refused `dev-only-*` (prod:infrastructure/edge/preflight.sh:37-72), so a value is inferred present; its strength (no length floor exists anywhere: checklist step 2.5b prints it) and whether it is the value registered at Razorpay are not determinable from the repo. [Key in the production ref 47143a0's `.env.example`: yes.] | Anyone who knows it can forge payment events, so it must never be the public dev default. Rotating means changing it in `.env` and in Razorpay together. The route's reachability from the internet is NOT established (section 9). | Preflight structurally valid: present, non-empty, not `dev-only-*` or a placeholder (proved: R3c, R3d). Production credential verified: NO. It is proved only by Razorpay delivering a signed event the service accepts, which needs the owner and a reachable webhook URL (section 9). |
| `BILLING_TAX_CODE` | E | Line must exist; may be empty (empty = no tax line) | no | billing (INT:cp:909) | String, `str \| None`; empty is unset (INT:services/billing/app/config.py:41), NOT trimmed. It must equal the `code` of a `billing.tax_rates` row in force on the period's start date, else the run refuses to invoice (INT:services/billing/app/billing_run.py:255-265). No migration or seed inserts a rate row (the only mentions of `tax_rates` under `services/billing/migrations` are schema definitions), so the row is a data change made by hand: INT:rb:778. | empty | OWNER DECISION: tax registration status and the rate row; not determinable from the repo | Leave empty until the deployment is tax-registered. Then insert the `billing.tax_rates` row first (append-only: a new row), then set both keys and recreate billing. This is a data change plus a business decision, hence category E. | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Wrong tax on an invoice is a statutory error; a set code without a rate row stops invoicing rather than under-collecting. | Preflight structurally valid: empty allowed, no other check. Production value verified: no; read-only check on the box: `docker compose -f compose.prod.yml --env-file .env exec -T postgres psql -U opsgrids -d opsgrids -Atc "select code, jurisdiction, rate_bps, effective_from from billing.tax_rates order by code, effective_from"`. |
| `BILLING_TAX_JURISDICTION` | E | Line must exist; may be empty | no | billing (INT:cp:910) | String, optional. It is matched EXACTLY against the rate row: `None` (empty) matches only rows whose `jurisdiction IS NULL`, otherwise `jurisdiction = <value>` (INT:services/billing/app/selectors.py:181-184). So set it to whatever the rate row carries, or leave it empty if the row's jurisdiction is NULL. The old annotation `both empty or both set` was wrong. | empty (or e.g. `IN` if the rate row says so) | OWNER DECISION, set together with BILLING_TAX_CODE | Same as BILLING_TAX_CODE. | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | As BILLING_TAX_CODE. | Preflight structurally valid: empty allowed. Production value verified: no (same SQL as BILLING_TAX_CODE). |

### 3.6 WhatsApp and Engage

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `WHATSAPP_TRANSPORT` | C | Required, non-empty | no | notification, notification-worker (shared notification env, INT:cp:971) | Exactly `log` or `whatsapp_cloud`, case-sensitive. `whatsapp_cloud` without WHATSAPP_PHONE_NUMBER_ID and WHATSAPP_ACCESS_TOKEN raises at startup (INT:services/notification/app/transports.py:401-404; probes: `WhatsApp_Cloud` refused, cloud without credentials refused, cloud with fake credentials builds a `WhatsAppCloudTransport` with no network call). | `log` (committed default) | Choose | `log` until a Meta app exists AND the webhook route is confirmed reachable (section 9). | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | `whatsapp_cloud` sends real messages with the platform-level token. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `WHATSAPP_APP_SECRET` | B | Line must exist; may be empty | YES | notification, notification-worker: verifies `X-Hub-Signature-256` on Meta's webhooks | String; HMAC-SHA256 key over the raw request body (INT:docs/whatsapp/webhooks.md:20). Platform-wide: one Meta app signs every tenant's webhooks. With none configured the service REFUSES webhooks rather than accept unverified bodies (INT:docs/whatsapp/webhooks.md:137). | empty | Meta for Developers -> your app -> Settings -> Basic -> App secret (provider-side, not verified in repo) | Copy from the Meta app into `.env` on the box only when enabling WhatsApp webhooks. | no (issued by Meta) | Key exists on main; whether the box carries a value is not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | Forged webhooks are possible for anyone who knows it; rotate in Meta if exposed. | Preflight structurally valid: empty allowed. Production credential verified: NO. Proved only when a genuine Meta delivery is accepted, which needs the owner and a reachable route (section 9). |
| `WHATSAPP_VERIFY_TOKEN` | A | Line must exist; may be empty | YES | notification, notification-worker: answers Meta's `GET` subscription handshake | A non-empty ASCII string: compared with `hmac.compare_digest(token, expected_token)` on `str` arguments (INT:services/notification/app/webhooks.py:103), which raises on non-ASCII text. You choose it; Meta echoes it back. | empty | You generate it, then paste it into the Meta app's webhook configuration (provider-side, not verified in repo) | Use the fill helper; paste the same value into Meta's webhook form. | YES: `openssl rand -hex 32` | Key exists on main; whether the box carries a value is not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | App-level shared secret on an unauthenticated route. | Preflight structurally valid: empty allowed. Production credential verified: NO. Proved when Meta's subscribe handshake succeeds (needs the owner and a reachable route, section 9). |
| `WHATSAPP_PHONE_NUMBER_ID` | B | Line must exist; may be empty (required iff WHATSAPP_TRANSPORT=whatsapp_cloud) | no | notification, notification-worker (platform-level `whatsapp_cloud` transport) | A string id, no validation; used as the sender in `{graph-base}/{phone_number_id}/messages` (INT:services/notification/app/transports.py:306). The Graph base (`https://graph.facebook.com/v25.0`, INT:services/notification/app/config.py:62) is not forwarded by compose, so it cannot be changed from `.env`. | empty | Meta for Developers -> your app -> WhatsApp -> API Setup -> Phone number ID (provider-side, not verified in repo) | Copy from the API Setup page. | no (issued by Meta) | Not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | An identifier; not secret. | Preflight structurally valid: empty allowed. Production value verified: no; the read-only Graph API `GET /{phone_number_id}?fields=id` probe in the checklist (owner-run). |
| `WHATSAPP_ACCESS_TOKEN` | B | Line must exist; may be empty (required iff WHATSAPP_TRANSPORT=whatsapp_cloud) | YES | notification, notification-worker (platform-level `whatsapp_cloud` transport only) | String, no validation; sent as `Authorization: Bearer` (INT:services/notification/app/transports.py:330). Each TENANT's own token is not an env variable: it is stored sealed per tenant (INT:docs/whatsapp/webhooks.md:135). | empty | Meta Business Suite / business.facebook.com -> Business settings -> System users -> generate token for the WhatsApp app (provider-side, not verified in repo) | Create a system-user token with the WhatsApp permissions and paste it into `.env` on the box only when enabling the platform transport. | no (issued by Meta) | Not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | Can send WhatsApp messages as the business. Prefer a system-user token over a personal one (provider guidance, not verified in repo). | Preflight structurally valid: empty allowed. Production credential verified: NO; only the owner-run Graph API probe proves it (same request shape as INT:services/notification/app/whatsapp_provider.py:62). |
| `ENGAGE_UNSUBSCRIBE_SECRET` | A | Required, non-empty | YES | notification, notification-worker (campaign send gate, link minting, unsubscribe routes) | Any string; the HMAC-SHA256 key that signs unsubscribe links. No length floor. A `dev-only-`/`ci-only-` value in production/staging/uat turns campaign signing OFF at the feature level (`unsubscribe_key()` returns None) instead of stopping the service (INT:services/notification/app/services.py:2868-2879; probe: a production Settings object accepts the dev default). Rotating invalidates every link already sent. | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generate on the box (belongs to Engage itself; no external issuer) | Use the fill helper. | YES: `openssl rand -hex 32` (repo also suggests `python3 -c "import secrets; print(secrets.token_urlsafe(32))"`) | Key exists on main; NOT in the production ref 47143a0 (the RUNBOOK lists it as needing a real value). [Key in the production ref 47143a0's `.env.example`: no.] | A forgeable key lets anyone unsubscribe or re-subscribe any address. | Preflight structurally valid: present, non-empty, not a placeholder (proved: R3d). No length check anywhere. Production credential verified: NO (no external service verifies it). |
| `ENGAGE_PUBLIC_API_BASE` | C | Required, non-empty | no | notification, notification-worker | An absolute `https://` URL **whose path ends in `/api/v1`**. Links are `<base>/engage/unsubscribe/{token}` (a trailing slash is stripped, INT:services/notification/app/services.py:3969-3977) and the only public unsubscribe route at app-gateway is the prefix `/api/v1/engage/unsubscribe/` (INT:services/app-gateway/app/main.py:94), so a bare origin (`https://app.opsgrids.com`) passes preflight and the send gate but yields links outside the public prefix (Caddy proxies only `/api/*`). Write it lower case. Preflight and the send gate disagree on spelling (run Q11): preflight FAILs `localhost` and `127.0.0.1` case-sensitively and anything not starting with lower-case `https://` (INT:pf:604-610; run R3n); the send gate lower-cases first (INT:services/notification/app/services.py:2897-2898), so `https://Localhost/api/v1` passes preflight and is refused at the first send, while `HTTPS://app.opsgrids.com/api/v1` FAILs preflight and would pass the gate. `[::1]` and `0.0.0.0` pass both checks although they are not public. | `https://app.opsgrids.com/api/v1` (committed default) | Choose: the public API base, i.e. the origin that serves `/api/v1` through Caddy plus the `/api/v1` path (the `app.opsgrids.com` site imports the `edge` snippet whose `/api/*` proxies to the gateway) | Keep the default (`app.opsgrids.com` is in TLS_PLATFORM_HOSTS). | no | Key exists on main; NOT in the production ref 47143a0. Committed default. [Key in the production ref 47143a0's `.env.example`: no.] | Points recipients' browsers at the platform; must be HTTPS. | Preflight structurally valid: https and not localhost (proved: R3n). Production value verified: no; the by-hand check is a GET of the URL from outside the box. |

### 3.7 Platform

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `DB_POOL_SIZE` | C | Required, non-empty | no | every service (x-service-env) | An integer, connections per process; ~50 processes share Postgres `max_connections=400` (INT:cp:248). pydantic int: `abc` refused, `0` and `-1` accepted with no range check. Compose's own default is 3 (INT:cp:78), not the 5 in the example, and an empty value falls to compose's 3 (probe: a service Settings alone would use 10). | `5` (committed default) | Choose | Keep the default. | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | Too high exhausts Postgres connections across ~50 processes. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `DB_MAX_OVERFLOW` | C | Required, non-empty | no | every service (x-service-env) | An integer, extra connections per process (compose default 2). | `2` (committed default) | Choose | Keep the default. | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | As DB_POOL_SIZE. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `APP_BASE_URL` | C | Required, non-empty | no | every service (x-service-env); auth builds the password-reset link from it (INT:services/auth/app/services.py:984) | An absolute URL; no validator. Trailing slash stripped where used. | `https://app.opsgrids.com` (committed default) | Choose: the merchant-facing app origin | Keep the default. | no | Committed default. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | Emailed links point here: a wrong host sends users to the wrong site. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `CORS_ORIGINS` | C | Required, non-empty | no | every service (x-service-env, applied at INT:pc/app.py:280) | Comma-separated origins, split and stripped, empties dropped (INT:pc/config.py:182; probe). Must stay a fixed set: custom domains are never added. | `https://app.opsgrids.com` (committed default) | Choose | Keep the default. | no | Committed default. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | A wildcard or extra origin widens browser access to the API. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `LOG_LEVEL` | C | Required, non-empty | no | every service (x-service-env) | An exact UPPER-CASE logging level name: `DEBUG INFO WARNING WARN ERROR CRITICAL FATAL NOTSET`. Lower case, `TRACE` or a number raises `ValueError: Unknown level` at startup (probe; INT:pc/logging.py:43). | `INFO` (committed default) | Choose | Keep `INFO`. | no | Committed default. The key is present in the untracked local `.env` (presence checked with `grep -c`; the value was not read, so whether it is a development value is not determinable from the repo); never copy or promote it. [Key in the production ref 47143a0's `.env.example`: yes.] | `DEBUG` in production increases log volume and may log more request detail. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `STORE_SLUG_CACHE_SECONDS` | C | Required, non-empty | no | storefront (INT:services/storefront/app/config.py:55) | An integer number of seconds (pydantic int; no range check): the TTL of the Redis slug -> store lookup only (INT:services/storefront/app/selectors.py:259); a newly published store can 404 for up to this long (INT:services/storefront/app/config.py:50-55). It does NOT govern how long a removed custom domain keeps serving: that is `store_host_cache_seconds` (default 300, INT:services/storefront/app/config.py:65; used at INT:services/storefront/app/selectors.py:333), which compose does not forward (compose forwards only STORE_SLUG_CACHE_SECONDS, INT:cp:641), so `.env` cannot change it. | `300` (committed default) | Choose | Keep the default. | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | A long value delays a newly published store's first appearance. It does not change takedown latency (see the format cell); the RUNBOOK's 'take one merchant off the air' step attributes the takedown window to this key (INT:rb:1173), which is a mistake. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |

### 3.8 Backups (off-box, S3-compatible)

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `BACKUP_S3_BUCKET` | B | Required, non-empty | no | backup.sh (host cron / `make edge-backup`) | An existing bucket name at the provider. NOT the box-local MinIO. backup.sh exits 1 with `no off-box destination configured` if the bucket or the key id is empty (INT:bk:91-94). | `opsgrids-backups` (committed default; the repo does not prove it exists) | An off-box S3-compatible bucket the owner provides (the example names Backblaze B2 and Cloudflare R2 as options: INT:ex:228); which provider is in use is not determinable from the repo | Create the bucket in the provider's console; set a lifecycle rule for retention there (the box only prunes its local copies). The console steps are provider-side, not verified in repo. | no | Not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | Holds full database dumps and TLS certificates. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: no; `make edge-backup` uploads for real (owner-run). |
| `BACKUP_S3_PREFIX` | C | Required, non-empty | no | backup.sh | A key prefix inside the bucket; backup.sh falls back to `edge` when unset (INT:bk:112). | `edge/postgres` (committed default) | Choose | Keep the default. | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | None. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `BACKUP_S3_ENDPOINT` | B | Line must exist; may be empty (empty = AWS S3) | no | backup.sh | An endpoint URL passed verbatim as `aws s3 --endpoint-url` (INT:bk:101). | empty (AWS) or the provider's S3 endpoint URL | The backup provider (provider-side, not verified in repo) | Copy the S3 endpoint URL from the provider's bucket page. | no | Not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | Must be https. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67). Production value verified: no (owner-run `make edge-backup`). |
| `BACKUP_S3_REGION` | B | Required, non-empty | no | backup.sh | A region string exported as `AWS_DEFAULT_REGION`; backup.sh falls back to `us-east-1` (INT:bk:109). Some non-AWS providers ignore it. | `us-east-1` (committed default) | The provider's region for the bucket (provider-side, not verified in repo) | Copy from the provider's bucket page. | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | None. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `BACKUP_S3_ACCESS_KEY_ID` | B | Line must exist; may be empty, but only TOGETHER with the secret (both empty = WARN, exactly one set = FAIL) | YES | backup.sh (passed to a pinned aws-cli container, INT:bk:47) | Whatever the provider issues. Preflight refuses exactly one of the pair being set (INT:pf:289; runs R3k, R3l). | empty | The backup provider's key management page (provider-side, not verified in repo) | Create an application key scoped to the one bucket. The example asks for write-only, but the RUNBOOK's restore drill downloads with the same pair, so a write-only key cannot run the drill (only one pair is defined). | no (issued by the provider) | Not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | A credential that can also delete is one ransomware can use. | Preflight structurally valid: pair-consistency check. Production credential verified: NO; only a real `make edge-backup` upload proves it (owner-run: creates an object). |
| `BACKUP_S3_SECRET_ACCESS_KEY` | B | Line must exist; may be empty, but only TOGETHER with the key id | YES | backup.sh | Whatever the provider issues (issued with the key id). | empty | The backup provider (provider-side, not verified in repo) | Copy once at key creation straight into `.env` on the box. | no (issued by the provider; `openssl rand -hex 32` only if the provider lets you choose) | Not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | As the key id. | As BACKUP_S3_ACCESS_KEY_ID. |
| `BACKUP_LOCAL_KEEP` | C | Required, non-empty | no | backup.sh | A positive integer >= 1: nightly dumps kept on the box. backup.sh prunes with `head -n -"$keep" \| xargs -r rm -f` (INT:bk:170-173): `0` deletes EVERY local dump, the one just written included, and a negative or non-numeric value makes `head` fail so the script ends non-zero after the upload. | `7` (committed default) | Choose | Keep the default. | no | Committed default. [Key in the production ref 47143a0's `.env.example`: yes.] | None. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |

### 3.9 Object storage (MinIO and the document service)

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `MINIO_ROOT_USER` | A | Required, non-empty (even if the storefront profile is off) | YES | minio, minio-init (both `profiles: ['storefront']`) | No format is enforced anywhere, but it is embedded as the user part of `MC_HOST_og: http://${MINIO_ROOT_USER}:${MINIO_ROOT_PASSWORD}@minio:9000` (INT:cp:452), so it must be URL-userinfo-safe: hex is. MinIO's own length limit is not stated in the repo. | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generate on the box | Fill helper: `openssl rand -hex 16`. | YES: `openssl rand -hex 16` | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. (Added with ADR-0009.) [Key in the production ref 47143a0's `.env.example`: no.] | Root of the object store. Used only by `minio` itself and by `minio-init` at startup; nothing else authenticates as root. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production credential verified: NO. A preflight pass proves presence and not-a-placeholder only: there is NO length or strength check on this key (checklist step 2.5b prints its length), and it never proves that the running service holds this value; minio-init succeeding is the real check. |
| `MINIO_ROOT_PASSWORD` | A | Required, non-empty (even if the storefront profile is off) | YES | minio, minio-init | >= 8 characters: preflight FAILs shorter, counted in characters (INT:pf:299-306; run R3h). URL-userinfo-safe (hex). | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generate on the box | Fill helper: `openssl rand -hex 32`. | YES: `openssl rand -hex 32` | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | As MINIO_ROOT_USER. Rotation has no RUNBOOK procedure. | Preflight structurally valid: presence, placeholder, >= 8 chars (proved: R3h). Production credential verified: NO. A preflight pass proves presence, not-a-placeholder and an 8-character floor only, never strength or that the running service holds this value. |
| `DOCUMENT_S3_ACCESS_KEY` | A | Required, non-empty (even if the storefront profile is off) | YES | document, minio-init | A free-choice access key: minio-init CREATES a MinIO user with this name (INT:cp:509) and attaches a put/delete-only policy on the one bucket. Keep it different from MINIO_ROOT_USER and the orphan user (MinIO/mc behaviour, provider-side, not verified in repo: no repo code checks it; generating each value separately guarantees it). MinIO's length limits are not stated in the repo; hex-16 is the recommendation. The document API receives it as `S3_ACCESS_KEY` (INT:cp:459). | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generate on the box | Fill helper: `openssl rand -hex 16`. | YES: `openssl rand -hex 16` | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Scoped: can put and delete objects in one bucket; cannot list it or change its policy. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production credential verified: NO. A preflight pass proves presence and not-a-placeholder only: there is NO length or strength check on this key (checklist step 2.5b prints its length), and it never proves that the running service holds this value. |
| `DOCUMENT_S3_SECRET_KEY` | A | Required, non-empty (even if the storefront profile is off) | YES | document, minio-init | >= 8 characters (preflight FAILs shorter, same loop as the root password). The document API receives it as `S3_SECRET_KEY`. | empty in the example (a `CHANGE_ME_...` placeholder is deliberately NOT used: section 11, item 20) | Generate on the box | Fill helper: `openssl rand -hex 32`. | YES: `openssl rand -hex 32` | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | As DOCUMENT_S3_ACCESS_KEY. | Preflight structurally valid: presence, placeholder, >= 8 chars. Production credential verified: NO. A preflight pass proves presence, not-a-placeholder and an 8-character floor only, never strength or that the running service holds this value. |
| `DOCUMENT_S3_ORPHAN_ACCESS_KEY` | A | Line must exist; preflight allows empty, but generate it (see format) | YES | minio-init (creates the user); held by NO always-on container since the memory-headroom merge | Same shape as DOCUMENT_S3_ACCESS_KEY and different from it. minio-init runs `mc admin user add` with this pair UNCONDITIONALLY under `set -e` (INT:cp:538) while preflight allows both empty (INT:pf:64; run R3i passes with both empty). What `mc` does with two empty strings cannot be determined from the repo, so set both at the first deploy. | empty in the committed example; generate | Generate on the box | Fill helper: `openssl rand -hex 16`. | YES: `openssl rand -hex 16` | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | List + delete on the bucket: the one capability the write user lacks. Never given to an API container. Before the memory-headroom merge (`193b529`) it WAS given to the always-on `document-cron` (PRE:cp:1087); that container no longer exists, so no always-on process holds it now. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67) (no length check on this one). Production credential verified: NO. A preflight pass proves presence and not-a-placeholder only: there is NO length or strength check on this key (checklist step 2.5b prints its length), and it never proves that the running service holds this value. |
| `DOCUMENT_S3_ORPHAN_SECRET_KEY` | A | Line must exist; preflight allows empty, but generate it | YES | minio-init; by-hand orphan pass | >= 8 characters for MinIO; NOT length-checked by preflight. | empty in the committed example; generate | Generate on the box | Fill helper: `openssl rand -hex 32`. | YES: `openssl rand -hex 32` | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | As the orphan access key. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67). Production credential verified: NO. A preflight pass proves presence and not-a-placeholder only: there is NO length or strength check on this key (checklist step 2.5b prints its length), and it never proves that the running service holds this value. |
| `ASSET_PURGE_RETENTION_DAYS` | C | Required, non-empty | no | document (the in-API purge sweep) | An integer number of days (`int`: `abc` and `1.5` refused; `0` and `-5` accepted: no range check; probe). The document API receives it as `ASSET_PURGE_RETENTION_DAYS` (INT:cp:1054). | `30` (committed default; ADR-0009 section 8) | Choose | Keep 30. | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | A negative value would purge immediately: keep it positive. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `DOCUMENT_ORPHAN_SWEEP_ENABLED` | C | Required, non-empty | no | NO container reads it (`document-cron`, which did on `193b529`, no longer exists) | true\|false. On the final compose it is not interpolated at all (INT:cp has no `DOCUMENT_ORPHAN_SWEEP_ENABLED`); the by-hand orphan run sets `ORPHAN_SWEEP_ENABLED` on its own container instead (INT:rb:452). It stays a required key only because the release's tests build `.env` from the example. | `false` (committed default) | Choose: leave `false` | Leave `false`. Turn the orphan pass on by hand only after the orphan pair is set and dry runs look right (RUNBOOK). | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Inert since the memory-headroom merge. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `DOCUMENT_ORPHAN_SWEEP_DRY_RUN` | C | Required, non-empty | no | NO container reads it (`document-cron`, which did on `193b529`, no longer exists) | true\|false. Reporting-only while true. Same status as DOCUMENT_ORPHAN_SWEEP_ENABLED. | `true` (committed default) | Choose: keep `true` | Keep `true`. | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Inert since the memory-headroom merge. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259). Production value verified: not applicable (configuration, no provider) |
| `S3_PUBLIC_BUCKET` | C | Required, non-empty | no | caddy (path matcher `/{$S3_PUBLIC_BUCKET}/*`), minio-init (creates it), document | A bucket name. No repo code validates the characters: compose, the Caddyfile path matcher, `minio-init` and preflight only interpolate or compare the string (INT:cp:183,453,476; INT:cf:115-121; INT:pf:315). It lands in a Caddy path matcher, an S3 ARN and a URL path, so keep to lower-case letters, digits and hyphens (a convention; S3/MinIO's own naming rules are provider-side, not verified in repo). It must be the last path segment of PUBLIC_ASSET_BASE_URL (preflight compares the strings). The bucket is PUBLIC-READ in its entirety: anonymous `s3:GetObject` only, no ListBucket (INT:cp:490). | `opsgrids-public` (committed default) | Choose | Keep the default. | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Nothing that is not meant to be world-readable is ever written there. | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259); agreement with PUBLIC_ASSET_BASE_URL is checked (proved: R1, R3j). Production value verified: not applicable (configuration, no provider) |
| `PUBLIC_ASSET_BASE_URL` | C | Required, non-empty | no | document; baked into the storefront bundle by publish-web.sh | EXACTLY `https://<EDGE_HOST>/<S3_PUBLIC_BUCKET>`, no trailing slash: preflight compares the strings and FAILs on any difference (INT:pf:315-319; run R3j). Caddy proxies `/opsgrids-public/*` to MinIO only on EDGE_HOST's own site (INT:cf:124-154). publish-web.sh refuses a value that is not https (INT:pw:260). Republish the buyer and storefront bundles after changing it. publish-web.sh reads the line raw over ssh (the FIRST line; it strips only a CR and one pair of quotes and KEEPS an inline ` # comment`), so a comment is baked into `VITE_ASSET_BASE_URL` and still satisfies its `https://` test, while preflight (which `source`s the file) drops the comment and passes its equality check (INT:pw:239-262): write it bare, no quotes, no inline comment. | `https://edge.opsgrids.com/opsgrids-public` (committed default) | Derived from EDGE_HOST and S3_PUBLIC_BUCKET | Keep the default (it matches EDGE_HOST). | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Never point it at another host Caddy serves: that would serve uploaded objects with a platform-hostname cookie scope (comment at INT:pf:308). | Preflight structurally valid: line present, non-empty, not a placeholder (INT:pf:227-259) and equality with `https://$EDGE_HOST/$S3_PUBLIC_BUCKET` (proved: R3j). Production value verified: not applicable (configuration, no provider) |

### 3.10 Observability and ops-monitor

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `SENTRY_DSN` | B | Line must exist; may be empty | YES | every service passes it through (x-service-env); NOTHING consumes it | A Sentry DSN URL. INERT in this release: declared (INT:pc/config.py:171) but no code reads it and `uv.lock` contains no Sentry package (checked: no `sentry` entry). | empty | A Sentry project's Client Keys (provider-side, not verified in repo), if one exists | Leave empty; filling it changes nothing until an SDK is wired in. | no | Not determinable. [Key in the production ref 47143a0's `.env.example`: yes.] | A DSN is a write-only ingest credential. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67). Production value verified: not applicable (unused). |
| `OPS_ALERT_EMAIL` | C | Line must exist; may be empty (empty = alerts logged, nobody paged) | no | ops-monitor ONLY | One address, or several COMMA-separated with no spaces. The field is a plain `str \| None` with no parsing (INT:om/config.py:252); the value goes verbatim into the `To` header (INT:om/mailer.py:89) and `smtp.send_message(message)` derives the envelope recipients from it. Experiment (Python 3.12.6 and 3.13.2, offline capture subclass): `a,b` and `a, b` -> both recipients; `a;b`, `a and b`, `a b` -> ONLY the first, silently. No repo test sends to more than one address (the three tests that set it use one), so multi-recipient delivery is stdlib behaviour, not a pinned contract, and the SMTP provider's acceptance of two recipients is provider-side. Quote any value with a space (preflight FAILs it unquoted by name since `ce35c77`; on `193b529` it was exit 127). | empty in the example. The owner's stated value is `info@opsgrids.com,zionna@opsgrids.com` (from the owner's instruction; not in the repo), set by checklist step 5.1 after the SMTP probe printed OK | Choose: mailboxes a human reads | Set it in checklist step 5.1, only after the SMTP login probe printed OK: setting it is what makes ops-monitor start sending (it ignores EMAIL_TRANSPORT). | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Alerts name internal hosts and queues; both recipients see each other in `To`. | Preflight structurally valid: presence; prints `ok ops-monitor alerts email: <value>` when it and SMTP_HOST are set (proved: R5a), warns when either is empty (R5d). Production credential verified: NO; delivery is proved by ops-monitor `/status` showing `last_send_at`. |
| `OPS_MONITOR_API_CONTAINERS` | C | Line must exist; may be empty (the normal state) | no | ops-monitor ONLY | Comma-separated `name[:port]`, each name matching `^[a-z_][a-z0-9_.-]*$` (INT:om/config.py:32), spaces around commas stripped; `Auth`, `a b` and `9lives` are refused at startup (probes). A non-empty value REPLACES the derived list. Only the name half is validated at startup: the `:port` half is not (INT:om/config.py:301-307), and `parse_api_containers` runs `int(port)` while building the api probe argument, outside `_collect`'s try (INT:om/main.py:66-76,167-173), so `auth:80x` or `auth:` loads fine and then raises on every cycle: the api, disk, backup and cert checks after it never report (the loop logs `ops_monitor.cycle_failed` and continues; `/status` shows `last_cycle_error`, INT:om/main.py:458-469). The port must be a plain integer. Empty derives the list from COMPOSE_PROFILES (28 names for all ten; section 10). | empty | Derived from COMPOSE_PROFILES | Leave empty. | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | Narrowing it silently un-monitors containers. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67). Production value verified: not applicable (configuration, no provider) |
| `OPS_MONITOR_DLQ_QUEUES` | C | Line must exist; may be empty (the normal state) | no | ops-monitor ONLY | Comma-separated consumer queue names WITHOUT `.dlq`, each matching `^[a-z_][a-z0-9_.-]*$`; the monitor passively declares `{queue}.dlq` (INT:om/checks.py:195). A non-empty value REPLACES the derived list (12 names for all ten profiles; section 10). Bare consumer queue names only: an entry that already ends in `.dlq` (`account.events.dlq`) or is misspelt is accepted, checked as `<x>.dlq.dlq` and reported ok (`not declared yet`) forever, i.e. silently un-monitored (INT:om/checks.py:195,211-212). | empty | Derived from COMPOSE_PROFILES | Leave empty. | no | Not determinable from the repo. The key is not in the production ref 47143a0's `.env.example`, so nothing at that ref required it and no value is expected. [Key in the production ref 47143a0's `.env.example`: no.] | As OPS_MONITOR_API_CONTAINERS. | Preflight structurally valid: the LINE must exist, an empty value is allowed (INT:pf:42-67). Production value verified: not applicable (configuration, no provider) |


## 4. Process-only variables (not keys of `.env.example`)

28 variables (21 from the audit, plus `VITE_GOOGLE_MAPS_API_KEY`, added 2026-10-05 with the warehouse map, plus the six payment provider switches and Cashfree credentials added with ADR-0032, which are COMMENTED in `.env.example` on the same precedent) that are read from the process environment, the box's `.env` by a publish script rather than by preflight, computed by the tooling,
supplied to a one-off container, or interpolated by compose with a default. The other `make` arguments (`HOST`, `CONFIRM`, `REF`, `TO`, `PRODUCTS`, `APPLY`, `SERVICE`, `TAIL`) are turned into positional arguments by the Makefile,
not read as environment variables (INT:mk:537-606), and are out of scope of this table; `APP` and `PLACEHOLDER` are exported to publish-web.sh and are in it. **Do not add them to `.env` unless
the row says a `.env` line is read.** Same columns, same categories, kept in a separate table on purpose.

| Variable | Cat | Required? | Secret? | Service / component | Exact format (from the parser) | Example / placeholder | Source | How to obtain it | Must it be generated? | Already exists in the deployment environment? | Production security considerations | Verification status |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| `MFA_ACCEPT_LEGACY_PLAINTEXT` | E | Optional; NEVER a `.env` or compose value | no | auth, only the one-off container the deploy starts (`remote.sh migrate` -> lib/mfa-reseal.sh) | A pydantic boolean, default `false` (INT:services/auth/app/config.py:65). Probes: `true`, `1`, `yes` -> true; `false` and empty -> false; `maybe` refuses startup. The repo documents only `true`. | `true` on the one-off run only; otherwise unset | Set by the deploy with `-e` on one container (INT:mr:85-86) | Nothing to do by hand. Never add it to `.env` or compose: the release test forbids naming it in either (INT:tests/infra/test_mfa_reseal.py:213-218). | no | Not in compose or `.env.example` (repo facts, pinned by a release test). Whether the box's own `.env` or a shell carries it is not determinable from the repo, but nothing there is read: compose forwards no such variable. | Enabling it downgrades the trust rule for the seconds the one-off container runs (section 7). A persistent `true` would be the downgrade; nothing here can persist it. | No preflight row. The read-only check is the count statement (INT:mr:41), which must end at 0 after a deploy. |
| `BILLING_RUN_ENABLED` | E | Optional; commented in `.env.example` (not a required key) | no | subscription-worker | Write exactly `true`. Preflight treats only the literal `true` as on (INT:pf:520); the worker parses a pydantic boolean (INT:services/subscription/app/config.py:50), so `1`, `yes`, `on`, `t`, `y` also switch it ON in the container while preflight prints the OFF warning (run R6e; probes), and a value with a space inside it (` true`, `true `) refuses to parse and stops the worker. Compose passes `${BILLING_RUN_ENABLED:-false}` (INT:cp:891). | `true` | OWNER DECISION after `python -m app.billing_run --dry-run` (RUNBOOK 'The billing run') | Follow the RUNBOOK staging: deploy with the run OFF, dry-run against the chosen cutover, then append both lines and recreate `subscription-worker` (checklist). | no | Not determinable from the repo. A commented placeholder in the example only, so no value is expected on the box. | Turning it on converts every expired trial and bills every overdue period from the cutover; it is a money-moving switch and is not reversible for rows already billed. | Preflight: OFF -> warn (R6a); on with no or malformed cutover -> FAIL (R6b-R6d). Production value verified: no; `/health/billing-run` `overdue` is the running signal. |
| `BILLING_RUN_NOT_BEFORE` | E | Conditional: required only when BILLING_RUN_ENABLED=true; commented in `.env.example` | no | subscription-worker | Exactly `YYYY-MM-DDTHH:MM[:SS[.f]]` followed by `Z` or `+HH:MM`/`-HH:MM` (INT:services/subscription/app/config.py:85). Anything else is refused softly: the value becomes None with the reason in `billing_run_not_before_invalid`, the worker's other loops keep running and the run refuses to start (probes: naive time, date only, epoch seconds, `+0530`, a space instead of `T` all refused; `2026-09-31` matches the pattern and is then refused as not a real instant). Preflight uses the same pattern and also asks GNU `date` whether it is a real instant (INT:pf:531; a future cutover only warns; runs R6c, R6d2, R6g-R6j). On a laptop with BSD `date` the last check fails for a valid value (box-only row). Preflight is a necessary, not a sufficient, check: an offset of exactly `+24:00` or `-24:00` matches the pattern, GNU `date` accepts it and preflight prints `ok billing run enabled, cutover ...`, but the worker's `datetime.fromisoformat` refuses it (an offset must be strictly between -24h and 24h), so the value becomes None ('is not a real instant') and the billing run silently does not start (INT:services/subscription/app/config.py:85,102-107; run Q6). Use a normal offset (`+05:30`) or `Z`, and confirm the worker logs `billing_run.cutover` with the value it started with. | `2026-10-01T09:00:00+05:30` | OWNER DECISION: the moment billing goes live | Pick the instant you will turn the run on; preview with `--dry-run --not-before <value>`; set it once and never move it later. | no | Not determinable from the repo. A commented placeholder in the example only, so no value is expected on the box. | Periods that began before it are never invoiced and usage before it is never billed; moving it later leaves already-billed rows unrevisited. | Preflight structurally valid: shape + real instant (proved: R6b-R6j). Production value verified: no; the worker logs `billing_run.cutover` with the value it started with. |
| `EDGE_STATE_DIR` | C | Optional; process environment only | no | remote.sh, preflight.sh, backup.sh, compose (ops-monitor bind mounts) | An absolute writable directory, default `/srv/opsgrids/state` (INT:rs:115). remote.sh does NOT read `.env` for it, while preflight, backup.sh (both `source` the file) and compose (`--env-file`) would: a line in `.env` would split the box's state directory in two. | unset | Choose: leave unset | Leave unset. | no | Default only. | Holds `last-good-ref`, the backup marker and ops-monitor state. | n/a (process-only; not an `.env.example` key). |
| `EDGE_MEMINFO_FILE` | C | Optional; test seam, process environment only | no | preflight.sh only | A path to a file containing a `MemTotal: <N> kB` line; default `/proc/meminfo` (INT:pf:407). A missing or unreadable value is now a FAILED row, not a skip. | unset on the box | Test/dry-run seam | Never set it on the box. Used here only to run preflight on a Mac. | no | n/a | Setting it lies to the memory gate. | n/a (process-only; not an `.env.example` key). |
| `RUN_SWEEPS_IN_API` | C | Optional; commented in `.env.example`; compose default `true` | no | operations, storefront, document (INT:cp:656) | A pydantic boolean: `true false 1 0 yes no on off t f y n`, any case; a truthy value keeps the three sweeps in their API containers, a falsy one STOPS them and no `*-cron` container exists to run them, and anything else (`maybe`) is refused by the service at startup. Preflight reads it the same way since `0ff2954` (lower-cased; `true 1 yes on y t` are ok, INT:pf:570; run R8h: `True` is ok) and warns on anything else (runs R6f, R8i). | unset (compose default `true`) | Choose: leave it | Leave unset. | no | Default only. | Stopping the sweeps stops stock reconciliation, custom-domain verification and upload purging silently. | Preflight warns when not truthy (R6f, R8i); `True` is ok (R8h). |
| `EDGE_UP_PARALLEL_LIMIT` | C | Optional; commented in `.env.example` | no | remote.sh `up` only | A positive whole number; empty, `0` or anything with a non-digit silently falls back to 4 (INT:rs:322-330). Read from the process environment or from the `.env` through `dotenv_get` (INT:rs:325; a line that starts with the key at column 0): the last assignment wins and an inline ` # comment` is dropped, so `EDGE_UP_PARALLEL_LIMIT=2 # this deploy only` is 2 (before `0ff2954` it fell back to 4). `up` now prints the limit and the profiles it will use (INT:rs:341). `make deploy` runs over ssh and carries no shell environment, so use a `.env` line. | `2` for this release's one deploy | Choose (temporary throttle) | Append `EDGE_UP_PARALLEL_LIMIT=2` to the box's `.env` for the deploy that lands ~+540 MB at once (RUNBOOK), then remove the line. | no | Not determinable from the repo. Commented in the example (a temporary throttle), so no line is expected on the box. | None. | No preflight row. |
| `EDGE_RELAY_SCHEMAS` | C | Optional; compose interpolation, not in `.env.example` | no | edge-relay, ops-monitor | Comma-separated `schema` or `schema:producer` entries (INT:pc/edge_relay.py:89); ops-monitor additionally requires bare identifiers `^[a-z_][a-z0-9_]*$` (probe). The two compose defaults DIFFER: edge-relay's list contains `lead` (INT:cp:708), ops-monitor's does not (INT:cp:768), so its outbox-lag check does not cover the `lead` schema. | unset | Choose: leave unset | Leave unset. If you ever set it, it applies to BOTH containers: give it the full list including `lead`. | no | Default only. | A short list silently stops draining or watching a schema's outbox. | No preflight row. |
| `ROUTES_SHA` | D | Derived; never set by hand | no | app-gateway container key | 16 hex characters: `sha256sum routes.prod.json \| cut -c1-16`, exported by remote.sh (INT:rs:113). | computed | Computed by remote.sh at deploy time | Nothing to do. | no | Yes: computed by the deploy tooling on every run. | None. | n/a (process-only; not an `.env.example` key). |
| `EDGE_REPO` | C | Optional; laptop-side process environment | no | Makefile, ssh-deploy.sh | An absolute path on the box; default `/srv/opsgrids/repo` (INT:sd:34). | unset | Choose: leave unset | Leave unset. | no | Default only. | None. | n/a (process-only; not an `.env.example` key). |
| `HEALTH_TIMEOUT` | C | Optional; process environment on the box | no | remote.sh `health` | Seconds, default 600 (INT:rs:365). ssh does not carry your shell environment, so `make deploy` cannot set it; running `remote.sh deploy` by hand on the box can. | unset | Choose | Leave unset; a slow start on a tight box is expected (RUNBOOK). | no | Default only. | None. | n/a (process-only; not an `.env.example` key). |
| `FORCE_MIGRATE` | C | Optional; process environment on the box | no | remote.sh `migrate` | `1` forces `alembic upgrade head` for every service, otherwise a service whose migrations are unchanged since the last good ref is skipped (INT:rs:256). | unset | Choose | Leave unset. | no | Default only. | None. | n/a (process-only; not an `.env.example` key). |
| `FORCE_PULL` | C | Optional; process environment on the box | no | remote.sh `build` | `1` adds `--pull` (re-resolves the base image tags) and forces a full rebuild of every image (INT:rs:171). Not the default: the tags move weekly and a full rebuild on two cores is about twenty-five minutes. `ssh-deploy.sh` runs `remote.sh` over ssh with arguments only (INT:sd:69), so `make deploy` cannot set it; only a by-hand `remote.sh` run on the box can. | unset | Choose | Leave unset. | no | Default only. | None. | n/a (process-only; not an `.env.example` key). |
| `PLACEHOLDER` | C | Optional; a `make` argument on the operator's machine, not a `.env` key | no | publish-web.sh (`make edge-web`) | `1` publishes the committed "this address is not serving a store" page instead of a buyer bundle; refused for any other app (INT:pw:198). The Makefile passes it through (INT:mk:596). | unset | Choose | Leave unset unless publishing the placeholder while a buyer bundle is not ready (RUNBOOK). | no | Default only. | None. | n/a (process-only; not an `.env.example` key). |
| `APP` | C | Optional; a `make` argument on the operator's machine, exported to publish-web.sh; not a `.env` key | no | publish-web.sh (`make edge-web`, `edge-web-rollback`, `edge-releases`) | One of `buyer` (the default), `dashboard`, `operations`, `hrms`, `crm`, `pulseboard`, `projects`, `support`, `marketing`, `finance`, `itsm`, `storefront`, `engage` (INT:pw:49,147; INT:mk:596-606): it selects which bundle is published and where it lives on the box (`$EDGE_WEB_ROOT` for `buyer`, `$EDGE_WEB_ROOT/<app>` for any other). An unknown value is refused. It is an environment variable made from the make argument; nothing reads it from `.env`. | unset (`buyer`) | Choose | `APP=buyer` and `APP=storefront` for the image-upload republish (checklist step 9.9); otherwise leave unset. | no | Default only. | None. | n/a (process-only; not an `.env.example` key). |
| `VITE_GOOGLE_MAPS_API_KEY` | B | Optional; commented in `.env.example` (not a required key). A line in the box's `.env` that publish-web.sh reads raw over ssh | no (public by design: it ships inside the operations bundle) | publish-web.sh `APP=operations` ONLY (the warehouse map: sites page, location picker, "show worker location" pin); never `worker`, never any other app, no container | Letters, digits, `-` and `_`, 20 or more, written bare. publish-web.sh reads the LAST `^VITE_GOOGLE_MAPS_API_KEY=` line over ssh and parses it with `_dotenv_value` (a quoted value is what is between the quotes, an inline ` # comment` is dropped, a CR and spaces are stripped), then exports it for the one build. Absent, empty or a value that fails the shape is a loud warning on stderr and a map-less publish, never a refusal: the variable is then exported EMPTY (not left unset), so a key exported in the operator's own shell or in `frontend/product-operations/.env.local` cannot enable a map the box was not given a key for. Every other app is built with the variable `unset`. The value is never printed, only that one was found and how many characters it has. | none committed; absent is the supported state | EXTERNAL: an API key from a Google Cloud project, created by the owner (the project, billing and spend cap are the owner's, set up separately from this release) | In the Google Cloud console (provider-side, not verified in repo): create a browser key, restrict HTTP referrers to `https://ops.opsgrids.com/*`, restrict the API list to what the Operations map calls, set a budget and a quota cap. Then append `VITE_GOOGLE_MAPS_API_KEY=<key>` to the box's `.env` (keep mode 600) and republish: `make edge-web HOST=... CONFIRM=... APP=operations` (RUNBOOK, 'Warehouse map and storage put-away'). | no (issued by Google) | Not determinable from the repo: the box's `.env` was not read, and the key does not exist yet. [Key in the production ref 47143a0's `.env.example`: no.] | It is visible to every visitor of ops.opsgrids.com, so an unrestricted key is a billing exposure: the console referrer restriction and the spend cap are the control, not secrecy. Never a server-side key, never in any other bundle. The Caddyfile is NOT changed for the map: ops and worker send no Content-Security-Policy or Permissions-Policy today, so the map loads without an allow-list; a later CSP-hardening release must allow the Google Maps origins first (pinned by `tests/infra/test_publish_web_maps_key.py`). | No preflight row (not a required key, so a box without it deploys). Behaviour proved by `tests/infra/test_publish_web_maps_key.py`, which runs publish-web.sh against stub ssh/pnpm: operations gets the box's key, `worker` and every other app never do, a missing or malformed key publishes map-less with a warning and never echoes the value. Production credential verified: no. |
| `PAYMENT_PROVIDER` | C | Optional; commented in `.env.example` (not a required key). In the compose defaults as `${PAYMENT_PROVIDER:-razorpay}` | no | payment, payment-worker | `razorpay` or `cashfree` (`Literal`; anything else fails start-up). The provider NEW platform charges and new checkouts go to; an existing charge, mandate and webhook keep the provider they were created under for life. `cashfree` on this production box needs `CASHFREE_ENV=production`. | `razorpay` | OWNER DECISION | Set in the box's `.env` (approval needed: a production configuration edit). Rollback: `razorpay` (or unset) and restart `payment` and `payment-worker`; in-flight Cashfree rows keep resolving through per-row routing while the credentials remain. | no | Not determinable from the repo (not in the production ref's example). | Flipping it is not a migration. Until customers hold Cashfree mandates every un-mandated invoice goes `no_mandate`, then dunning, then checkout (ADR-0005 Q4): a behaviour change from the stub. | Preflight: value checked. Behaviour proved by `services/payment/tests/test_provider_routing.py` (a Razorpay charge resumes only through Razorpay while the default is Cashfree). |
| `PAYMENT_WIRE` | C | Optional; commented in `.env.example`. In the compose defaults as `${PAYMENT_WIRE:-stub}` | no | payment, payment-worker | `stub` or `live`. `stub`: the platform's Razorpay adapter never opens a socket, whatever keys are present. `live`: customer-present checkout against Razorpay (Phase A; there is no saved-mandate debit), needs both keys. | `stub` | OWNER DECISION | Set to `live` only after a test-mode run-through off the box. Rollback: `stub` and restart `payment` and `payment-worker`. A checkout still open at Razorpay stays payable there and may have been paid: while the wire is the stub the poller cannot ask (the stub answers that it holds no such Order) and expires nothing, and turning the wire back on reconciles it. Let open checkouts resolve (`CHECKOUT_TTL_SECONDS`, a day) first where you can, and check the provider's dashboard for money taken in the window (RUNBOOK 'Payments: turning a provider on'). | no | Not determinable from the repo. | `rzp_test_` keys on a live wire are refused on this box (a customer could pay a real invoice with a public test card). | Preflight: value and key rules checked. Proved by `tests/infra/test_payment_env_wiring.py` and `services/payment/tests/test_config_payment.py`. |
| `PAYMENT_ALLOW_LIVE_KEYS` | C | Optional; commented in `.env.example`. In the compose defaults as `${PAYMENT_ALLOW_LIVE_KEYS:-false}` | no | payment, payment-worker | `true` or `false`. `true` lets `rzp_live_` keys and `CASHFREE_ENV=production` start; `false` refuses every live-shaped key on any switch. | `false` | OWNER DECISION | Set only when real money is meant to move. | no | Not determinable from the repo. | The platform's own counterpart of `COLLECTIONS_ALLOW_LIVE_KEYS` (which is for tenants' keys). | Preflight and `payment` start-up both refuse a live-shaped key without it. |
| `CASHFREE_ENV` | C | Optional; commented in `.env.example`. In the compose defaults as `${CASHFREE_ENV:-stub}` | no | payment, payment-worker | `stub`, `sandbox` or `production`. `stub`: an in-process stateful fake, no network, whatever keys are present. `sandbox` is refused on this production box (sandbox accepts public test cards: real invoices payable for free). `production` needs both credentials and `PAYMENT_ALLOW_LIVE_KEYS=true` and refuses test-shaped keys. | `stub` | OWNER DECISION | Local sandbox runs only; production at go-live (RUNBOOK). | no | Not determinable from the repo. | The Cashfree webhook route answers 404 unless Cashfree is enabled (it is the default provider, or this is `sandbox`/`production`), so a public route could never run on an unconfigured box. | Preflight and `payment` start-up refuse the unsafe combinations (same rules, one library). |
| `CASHFREE_APP_ID` | B | Optional-empty; commented in `.env.example` (not a required key). Empty in the compose defaults | YES (held as a SecretStr; never logged) | payment, payment-worker | Cashfree's App ID (`x-client-id`); both-or-neither with the secret key. A sandbox App ID starts with `TEST`. | empty | EXTERNAL: Cashfree dashboard -> Payment Gateway -> API keys (provider-side, not verified in repo) | Create sandbox keys for a local run. Production keys only at go-live, after 2FA, in the box's `.env` (approval needed). | no (issued by the provider) | Not determinable from the repo. | A sandbox credential is not a secret in the sense a production one is, but is treated as one. | Preflight: both-or-neither; refused as a half-set pair. |
| `CASHFREE_SECRET_KEY` | B | Optional-empty; commented in `.env.example`. Empty in the compose defaults | YES | payment, payment-worker | Cashfree's Secret Key (`cfsk_ma_test_...` / `cfsk_ma_prod_...`). It also signs every Cashfree webhook (there is no separate webhook secret): `base64(HMAC-SHA256(secret, timestamp + body))`, verified in constant time and **fail-closed** (an unset key verifies nothing). | empty | EXTERNAL: Cashfree dashboard (provider-side, not verified in repo) | As the App ID. | no (issued by the provider) | Not determinable from the repo. | Rotating it is a flag day: it signs both the API calls and the webhooks. A production-shaped key is refused unless `PAYMENT_ALLOW_LIVE_KEYS=true`. | As the App ID; plus the secret-shape scan `tests/infra/test_payment_secret_shapes.py` keeps real-shaped keys out of tracked files. |
| `OPS_MONITOR_PORT` | C | Optional; a `.env` line read only by `remote.sh status` | no | remote.sh `status` (the `ops-monitor /status` block); NOT read by the container | A port number, default 9110. `remote.sh status` reads the first `^OPS_MONITOR_PORT=` line of `.env` (INT:rs:543) and queries that port inside the container, but compose fixes the container's own port at 9110 (INT:cp:792). A different value in `.env` therefore changes only where `status` looks: it would print `(unreachable ...)` while ops-monitor is healthy. | unset (default 9110) | Choose: leave unset | Leave unset. It is not in `.env.example` and nothing needs it. | no | Default only. | None. | n/a (process-only; not an `.env.example` key). |
| `BACKUP_LOCAL_DIR` | C | Optional; read by backup.sh after it sources `.env`, so a `.env` line works | no | backup.sh | An absolute writable directory, default `/srv/opsgrids/backups` (INT:bk:49). | unset | Choose | Leave unset. | no | Default only. | Holds unencrypted database dumps on the box: keep the default directory's permissions. | n/a (process-only; not an `.env.example` key). |
| `S3_ORPHAN_ACCESS_KEY` | C | One-off container variable for the by-hand orphan pass | YES | `docker compose run --rm --no-deps document ./bin/cron` (one container, one run) | A copy of DOCUMENT_S3_ORPHAN_ACCESS_KEY, exported for one command and passed with `-e`; it never appears in a compose file (INT:rb:450). | from `.env` | Copied by the operator from DOCUMENT_S3_ORPHAN_ACCESS_KEY at run time (a category-A key that is new in this release) | Only for the by-hand orphan pass; see RUNBOOK. | no | No: a one-off `-e` copy the operator makes from DOCUMENT_S3_ORPHAN_ACCESS_KEY for one run (INT:rb:450-453), never stored. | List + delete on the bucket; hand it to one container for one run. | n/a (process-only; not an `.env.example` key). |
| `S3_ORPHAN_SECRET_KEY` | C | One-off container variable for the by-hand orphan pass | YES | as S3_ORPHAN_ACCESS_KEY | A copy of DOCUMENT_S3_ORPHAN_SECRET_KEY, same handling. | from `.env` | Copied by the operator from DOCUMENT_S3_ORPHAN_SECRET_KEY at run time (a category-A key that is new in this release) | As above. | no | No: a one-off `-e` copy the operator makes from DOCUMENT_S3_ORPHAN_SECRET_KEY for one run (INT:rb:450-453), never stored. | As above. | n/a (process-only; not an `.env.example` key). |
| `ORPHAN_SWEEP_ENABLED` | C | One-off container variable for the by-hand orphan pass | no | the same one-off document container | `true` on the one command that runs the orphan pass; the API container is never given it (INT:rb:452). | `true` on that one run | Choose | Only for the by-hand orphan pass, dry-run first. | no | Not stored anywhere: passed with `-e` on one command for one run. | With DRY_RUN=false it deletes objects the bucket lists that have no database row. | n/a (process-only; not an `.env.example` key). |
| `ORPHAN_SWEEP_DRY_RUN` | C | One-off container variable for the by-hand orphan pass | no | the same one-off document container | `true` first (report only), `false` only after the reported candidates look right (INT:rb:453). | `true` | Choose | As above. | no | Not stored anywhere: passed with `-e` on one command for one run. | `false` calls DeleteObject. | n/a (process-only; not an `.env.example` key). |

## 5. (a) Category index and counts

| Cat | Category | `.env.example` keys | Process-only | Total | Variables |
|---|---|---:|---:|---:|---|
| A | Generate locally | 9 | 0 | 9 | `ADMIN_GATEWAY_HOP_KEY`, `WHATSAPP_VERIFY_TOKEN`, `ENGAGE_UNSUBSCRIBE_SECRET`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `DOCUMENT_S3_ACCESS_KEY`, `DOCUMENT_S3_SECRET_KEY`, `DOCUMENT_S3_ORPHAN_ACCESS_KEY`, `DOCUMENT_S3_ORPHAN_SECRET_KEY` |
| B | Obtain from an external provider or service | 15 | 3 | 18 | `EDGE_RESERVED_IP`, `SMTP_HOST`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `WHATSAPP_APP_SECRET`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `BACKUP_S3_BUCKET`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`, `SENTRY_DSN`, `VITE_GOOGLE_MAPS_API_KEY` (P) |
| C | Choose and configure ourselves | 29 | 21 | 50 | `EDGE_WEB_ROOT`, `ACME_EMAIL`, `TLS_PLATFORM_HOSTS`, `JWT_KID`, `JWT_ISSUER`, `EDGE_PROFILES`, `EMAIL_TRANSPORT`, `EMAIL_FROM`, `SMTP_PORT`, `SMTP_USE_TLS`, `SMTP_USE_SSL`, `WHATSAPP_TRANSPORT`, `ENGAGE_PUBLIC_API_BASE`, `DB_POOL_SIZE`, `DB_MAX_OVERFLOW`, `APP_BASE_URL`, `CORS_ORIGINS`, `LOG_LEVEL`, `STORE_SLUG_CACHE_SECONDS`, `BACKUP_S3_PREFIX`, `BACKUP_LOCAL_KEEP`, `ASSET_PURGE_RETENTION_DAYS`, `DOCUMENT_ORPHAN_SWEEP_ENABLED`, `DOCUMENT_ORPHAN_SWEEP_DRY_RUN`, `S3_PUBLIC_BUCKET`, `PUBLIC_ASSET_BASE_URL`, `OPS_ALERT_EMAIL`, `OPS_MONITOR_API_CONTAINERS`, `OPS_MONITOR_DLQ_QUEUES`, `EDGE_STATE_DIR` (P), `EDGE_MEMINFO_FILE` (P), `RUN_SWEEPS_IN_API` (P), `EDGE_UP_PARALLEL_LIMIT` (P), `EDGE_RELAY_SCHEMAS` (P), `EDGE_REPO` (P), `HEALTH_TIMEOUT` (P), `FORCE_MIGRATE` (P), `FORCE_PULL` (P), `PLACEHOLDER` (P), `APP` (P), `OPS_MONITOR_PORT` (P), `BACKUP_LOCAL_DIR` (P), `S3_ORPHAN_ACCESS_KEY` (P), `S3_ORPHAN_SECRET_KEY` (P), `ORPHAN_SWEEP_ENABLED` (P), `ORPHAN_SWEEP_DRY_RUN` (P) |
| D | Already available in deployment infrastructure | 8 | 1 | 9 | `EDGE_HOST`, `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD`, `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY`, `JWT_PRIVATE_KEY_HOST_PATH`, `RABBITMQ_PASSWORD`, `RAZORPAY_WEBHOOK_SECRET`, `ROUTES_SHA` (P) |
| E | Requires an architectural or migration decision | 5 | 3 | 8 | `PII_DECRYPT_KEYS`, `COMPOSE_PROFILES`, `EDGE_BUDGET_ACCEPTED_MB`, `BILLING_TAX_CODE`, `BILLING_TAX_JURISDICTION`, `MFA_ACCEPT_LEGACY_PLAINTEXT` (P), `BILLING_RUN_ENABLED` (P), `BILLING_RUN_NOT_BEFORE` (P) |
| | **Total** | **66** | **28** | **94** | |

**94 variables in all: 66 keys of `.env.example` plus 28 process-only.** Of the keys alone: 9 to generate (A), 15 to obtain externally (B), 29 to configure (C), 8 already on the box, inferred and not verified (D), 5 needing a decision (E). Of all 94: A 9, B 18, C 50, D 9, E 8.

**Re-check the counts yourself** from the tables above (each command should be run from the repository root). The first prints one line per category over
EVERY row, the row total and any duplicate; the second prints the same restricted to the keys of `.env.example` (its category counts must sum to 66); the third must print nothing (every key of the example has exactly one row):

```bash
awk -F'|' '/^\| `[A-Z][A-Z0-9_]*` \| [A-E] \|/ { v=$2; c=$3; gsub(/[ `]/,"",v); gsub(/ /,"",c); n[v]++; k[c]++; t++ }
  END { for (x in k) print "all rows, category", x, k[x]; print "all rows", t; for (v in n) if (n[v] > 1) print "DUPLICATE", v }' \
  docs/deploy/ENV_SOURCE_MATRIX.md | sort

KEYS=$(sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' infrastructure/edge/.env.example) awk -F'|' '
  BEGIN { n = split(ENVIRON["KEYS"], a, "\n"); for (i = 1; i <= n; i++) ok[a[i]] = 1 }
  /^\| `[A-Z][A-Z0-9_]*` \| [A-E] \|/ { v=$2; c=$3; gsub(/[ `]/,"",v); gsub(/ /,"",c); if (v in ok) { k[c]++; t++ } }
  END { for (x in k) print "example keys, category", x, k[x]; print "example keys", t }' docs/deploy/ENV_SOURCE_MATRIX.md | sort

comm -23 <(sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' infrastructure/edge/.env.example | sort -u) \
         <(awk -F'|' '/^\| `[A-Z][A-Z0-9_]*` \| [A-E] \|/ { v=$2; gsub(/[ `]/,"",v); print v }' docs/deploy/ENV_SOURCE_MATRIX.md | sort -u)
```

**What is 'new on the box' versus 'already there'.** Relative to the production ref `47143a0` (44 keys), the final
release adds **22 keys**: the 20 that `main` did not have plus the two Engage keys `main` gained (ADMIN_GATEWAY_HOP_KEY, ASSET_PURGE_RETENTION_DAYS,
BILLING_TAX_CODE, BILLING_TAX_JURISDICTION, DOCUMENT_ORPHAN_SWEEP_DRY_RUN, DOCUMENT_ORPHAN_SWEEP_ENABLED,
DOCUMENT_S3_ACCESS_KEY, DOCUMENT_S3_ORPHAN_ACCESS_KEY, DOCUMENT_S3_ORPHAN_SECRET_KEY, DOCUMENT_S3_SECRET_KEY,
EDGE_BUDGET_ACCEPTED_MB, EDGE_PROFILES, MINIO_ROOT_PASSWORD, MINIO_ROOT_USER, OPS_ALERT_EMAIL,
OPS_MONITOR_API_CONTAINERS, OPS_MONITOR_DLQ_QUEUES, PUBLIC_ASSET_BASE_URL, S3_PUBLIC_BUCKET, SMTP_USE_SSL;
plus ENGAGE_PUBLIC_API_BASE and ENGAGE_UNSUBSCRIBE_SECRET). Two of the 22 (`EDGE_PROFILES`,
`EDGE_BUDGET_ACCEPTED_MB`) may stay absent, so **20 lines must be added to the box's `.env`**. The RUNBOOK used to say
'20 keys the production ref's did not (18 of them must be present)' in one place and 'the 19 new keys' in another;
`0ff2954` corrected both to 22 and 20 (INT:rb:1397, INT:rb:1503),
which agrees with the count computed here from the files against the production ref it names.

## 6. (b) Deep dives on the variables you named

### 6.1 `ADMIN_GATEWAY_HOP_KEY`

- **What it is.** The HMAC-SHA256 key admin-gateway signs its hop header with and auth verifies. It is used as the
  string it is (`hmac.new(key.encode(), ...)`, INT:pc/internal_auth.py:175), not hex- or base64-decoded.
- **What the code enforces.** At least 32 UTF-8 bytes; not `dev-only-`/`ci-only-` in production/staging/uat;
  never equal to `INTERNAL_SERVICE_TOKEN` in any environment; auth refuses to start without it in
  production/staging/uat (INT:services/auth/app/config.py:67-90,
  INT:pc/config.py:271-281). An empty string
  is read as unset (INT:pc/config.py:191). Probed against the real classes: unset and
  empty refused; 31 bytes refused; 32 bytes accepted (also 16 two-byte characters = 32 bytes); equal to the internal
  token refused.
- **Where a bad value is caught: preflight, before anything changes.** `secret_strength_problems` checks
  `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY` and `ADMIN_GATEWAY_HOP_KEY` at >= 32 **bytes** (`wc -c`, matching
  `len(value.encode())`) and that the hop key differs from the token (INT:ss:23-32);
  an empty value is the generic missing/empty FAIL. Compose passes `${ADMIN_GATEWAY_HOP_KEY:-}` with an empty
  default **on purpose**: a `:?` would abort every compose command on a box without the key, including
  `backup.sh`'s `pg_dump` and a rollback (INT:rb:888).
- **Secure generation.** 256 random bits, hex-encoded (64 ASCII characters; shell-, dotenv- and URL-safe), in its
  own `openssl` invocation:

  ```bash
  openssl rand -hex 32        # never reuse the INTERNAL_SERVICE_TOKEN value; do not run it where the output is logged
  ```

  Use the fill helper in the checklist so the value goes into `.env` without being printed. `openssl rand -hex 16`
  passes the byte check but is only 128 bits: do not use it. Generate on the box, never copy from dev, another
  environment or a chat window.
- **Consumer.** auth only. **admin-gateway, the only signer, is not in `compose.prod.yml`**, so operator sign-in is
  unreachable on this box and the key exists so that stays true.
- **Ordering.** It must be in `.env` before `make deploy` (preflight is the first step and refuses). Nothing else
  is order-sensitive.
- **Verification.** Preflight structurally valid: proved by runs R3e (8 bytes -> FAIL), R3f (equal to the token ->
  FAIL) and R1 (a fresh 64-hex value passes). Production credential verified: no such state exists for a locally
  generated secret; the auth container starting is the only downstream signal.

### 6.2 `EDGE_PROFILES` and `COMPOSE_PROFILES`

**Preflight now checks every declared profile name against the compose file's own list** (INT:pf:214), added by memory-headroom's final fixup `0ff2954`: a name compose does not define is a named FAIL instead of a silently smaller box. Nothing else validates a name (`remote.sh`, docker compose and the catalogue advisory do not). The parsers, in the order they are hit:

| Where | What it does |
|---|---|
| `resolve_declared_profiles` (INT:ap:60-61) | non-empty `EDGE_PROFILES` **replaces** `COMPOSE_PROFILES` (never a union); both empty = empty = core only, never an error |
| preflight (INT:pf:187) | resolves it, expands `*` (`docker compose config --profiles`), **FAILs any name that list does not contain** (runs R4o, R8d, R8e), then `export COMPOSE_PROFILES=<resolved>` so every compose call sees it. It reads `.env` by `source`, so the LAST assignment of a key wins |
| remote.sh (INT:rs:84-109) | reads the keys with `dotenv_get` (INT:rs:56-74): the **LAST** assignment wins (as it does for `source` and for compose), a quoted value is what is between its quotes, an unquoted value's ` # comment` is dropped, spaces and CR are removed; it matches only a line that starts with the key at column 0, so `export KEY=...` or an indented line reads as empty (run Q5, and see section 11, item 5); then expands `*` and exports the result. Before `0ff2954` it took the FIRST line and kept an inline comment, so it could judge a different list than preflight had checked |
| docker compose | splits the comma list. Experiment on the audit machine's compose v2.35.1 (not verified in repo): `"inventory, storefront"` (quoted, with a space) priced as both profiles (run R4n); `hrms;crm` matched nothing and priced as core only with no error from compose itself; **preflight now stops it** (run R4o) |
| preflight catalogue advisory (INT:pf:673 area) | matches `,name,` literally against the raw string, so a space or upper case makes a sold product look unserved. Derived from the code; it only runs when Postgres is up, so not exercised here |
| ops-monitor (INT:om/config.py:311-321) | strips spaces, expands `*`, and REFUSES at startup any other entry not matching `^[a-z_][a-z0-9_]*$` (probes: `HRMS`, `hrms.crm`, `-hrms`, `hrms crm`, `hrms;drop` refused; `hrms, crm` and `*` accepted) |

**Exact syntax.** One line, `EDGE_PROFILES=<csv>` or `COMPOSE_PROFILES=<csv>`: lower-case profile names, comma
separated, **no spaces**, and no inline comment (preflight and `remote.sh` tolerate one, run R8j; docker compose's own reading of it was not tested here), empty allowed. Valid names are the ten compose profiles (also the
`profiles` list of `memory-budget.json`): `inventory storefront hrms crm pulseboard projects support marketing
finance itsm`. The Inventory product's catalogue key is `operations` but its profile is `inventory`
(INT:ap:29-36). An unknown name (well-formed or not) is a named FAIL in preflight; docker compose and ops-monitor
alone would ignore a well-formed one and ops-monitor stops at startup on a malformed one. Units: none. `*` (every profile) is valid
on the final release and is expanded by preflight and remote.sh (run R4j: expanded to the ten names). On `193b529` (before the
merge) it was **not** expanded, so a hand-written list was needed there.

**Results of the experiments (values are fake; section 13 lists them all).**

| Run | Setting | Result |
|---|---|---|
| R4j | `COMPOSE_PROFILES=*` | `ok COMPOSE_PROFILES=* expanded to: crm,finance,hrms,inventory,...`; priced 4581M |
| R4k | `COMPOSE_PROFILES=` (empty) | priced 2217M: core only |
| R4l | `EDGE_PROFILES=storefront` over a ten-profile `COMPOSE_PROFILES` | `COMPOSE_PROFILES overridden by EDGE_PROFILES: storefront`; priced 2473M |
| R4m | `EDGE_PROFILES=inventory, storefront` unquoted | named FAIL (`unquoted value containing whitespace`), nothing sourced; **exit 127 on `193b529`** (R7b) |
| R4n | `EDGE_PROFILES="inventory, storefront"` quoted | accepted; priced 2571M (both profiles) |
| R4o | `COMPOSE_PROFILES="hrms;crm"` | **FAIL** `profile 'hrms;crm' ... is not defined in compose.prod.yml, so it enables nothing` (it used to price as core only with no error) |
| R8d / R8e | `COMPOSE_PROFILES=hrm` / `EDGE_PROFILES=hrm` (a typo) | the same named FAIL, listing the ten defined names |
| R8m | `EDGE_PROFILES=` empty, then `EDGE_PROFILES=storefront` on a later line | the later line wins: `overridden by EDGE_PROFILES: storefront`, 2473M |
| R4i | the `EDGE_PROFILES` line deleted | `every key in .env.example is set` (absent is allowed) |

**What to write.** The repo records that the box's `COMPOSE_PROFILES` lists all ten and that it has no
`EDGE_PROFILES` (INT:adr13:668-671, 428-429; a read-only look during review, not re-verified). Keep that: leave `EDGE_PROFILES=` empty
(add the line, or omit it) and leave the box's `COMPOSE_PROFILES` as it is. Removing a profile is a decision (memory,
and preflight refuses if it has a live `trial`, `active` or `suspended` activation), and `docker compose up` does not
stop the containers of a profile you remove.

### 6.3 `EDGE_BUDGET_ACCEPTED_MB`

**Syntax and units.** A whole number, digits only: no unit (`4581MB` FAILs, run R4e), no comma (`4,581` FAILs, R4g),
no decimal, no sign, **no leading zero** (`04581` and `00` FAIL, R8f: INT:pf:396; a bare `0` is accepted and useless), and no more than seven digits
(a twenty-digit value would overflow bash's integer arithmetic and compare as a negative number, so it is refused as above the cap, R8g:
INT:pf:440). The unit is the `M` in preflight's own line: the sum of `memory-budget.json` `expected_rss_mb` over the
post-deploy container set, compared with `MemTotal` from `/proc/meminfo` in kB divided by 1024
(INT:mb:27): MiB in practice, though the code and file say "MB".

**How the verdict is reached.** Soft line 85% of MemTotal (warn), hard line 100% (refuse), absolute cap
`host.max_accepted_percent` = 125% of MemTotal (INT:mj:70-72).
`apply_accepted_ceiling` downgrades a `refuse` to a loud `warn` only when the deploy's total is at or below the
ceiling **and** at or below the cap (INT:mb:73-74). A ceiling above the cap is itself a
FAILED row (R4f: `45810` -> `is above the 125% cap (4827M of 3862M MemTotal)`), and the gate now fails closed on every branch: if
compose or docker cannot price the box the row is `FAIL memory budget could not be priced` (INT:pf:382); if
`memory-budget.json` or `python3` is missing, or `MemTotal` is not a positive whole number, the row is a FAIL that says the budget "cannot be judged"
(INT:pf:358, INT:pf:408). Before `0ff2954` the first two of those were a warning and a skip.

**The numbers** (my run of the repo's own `lib/memory_budget_totals.py` on the final tree, `mem_total_mb_reference`
3862; the RUNBOOK's table agrees):

| Declared profiles | Cumulative MB | of 3862 MB | Run |
|---|---:|---:|---|
| core only (21 containers) | 2217 | 57.4% | R4k |
| + storefront | 2473 | 64.0% | |
| + inventory | 2571 | 66.6% | R4n |
| + hrms | 3233 | 83.7% | |
| + crm | 3617 | 93.7% | |
| + pulseboard | 3911 | 101.3% (over the hard line from here) | |
| + projects | 4009 | 103.8% | |
| + support | 4107 | 106.3% | |
| + marketing | 4205 | 108.9% | |
| + finance | 4483 | 116.1% | |
| **+ itsm (all ten, what the box declares)** | **4581** | **118.6% (719 MB over MemTotal)** | R1, R4a-R4j |

The cap is `3862 * 125 / 100 = 4827`. A ceiling is worth writing only if the total is at or below it; **4581 is**.
The old figures `4112` (phase 1) and the RUNBOOK's older `3894` are superseded: the prices were re-measured
(api 98, background 90, ops-monitor 93 MiB, INT:mj:78-103).

**What the box is today, per the repo** (a read-only look recorded in the RUNBOOK, **not re-verified**): the ten
profiles at the production ref `47143a0`, 40 containers, priced at 4044 MB = 104.7%, 182 MB over MemTotal;
this release adds eight containers and removes two: net +537 MB
(INT:rb:1341-1348). The box is therefore *already* over the hard
line, which is why a ceiling is needed at all.

**The number to write is the one preflight prints on the box for the commit you are about to deploy.** The post-deploy
set is the union of *containers running today that the new compose still defines* and *the services the declared
profiles configure*, so it depends on live state; my scratch run had nothing running and printed 4581. Read it, do
not compute it: `make edge-preflight HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com` and look for
`warn memory budget (post-deploy: ...): 4581M against 3862M MemTotal: 719M over MemTotal (118.6%)` (or the FAIL that
names what to set). Write exactly that total: preflight tells you when a ceiling is larger than needed (R4d: `19M above
this deploy's total ... set it to 4581`) and when it is not needed at all.

**Read the box first: the RUNBOOK's rule.** The 4581 is priced from images built on a laptop (arm64); the box was not measured by this audit.
The RUNBOOK now gives a rule for whether to run at all (INT:rb:1298-1318).
On the box, at a quiet hour, on the current production ref: sum the `MemUsage` column of `docker stats --no-stream` (converted to MiB; the RUNBOOK gives the exact one-liner) and run `vmstat 5 3`.
The real sum is the measured cost of the same 40 containers preflight prices at **4044**. Then:

1. Scale the release: about `4581 x measured / 4044`.
2. **If that is above 4827 (the 125% cap), do not write a ceiling**: preflight refuses any total above the cap even with one, so resize or de-list.
   Do the same if `vmstat`'s `si`/`so` columns are above zero at a quiet hour (already thrashing before this release adds 537 MB).
3. Otherwise **write the number preflight printed** (`EDGE_BUDGET_ACCEPTED_MB=4581`), **not** the scaled one: the scaled figure only decides *whether* to run.

This is a rule the audit repeats and does not run: the measurement needs the box (a category-E owner step: checklist steps 2.7 and 6.1).

**The decision (category E).** Choose one, in writing, before the first deploy that needs it:

1. resize the VM (the only option that adds headroom; the owner previously declined, before this release added ~537 MB);
2. de-list products: drop their profiles from `COMPOSE_PROFILES` **after** confirming no live activation exists;
3. accept the overcommit: set `EDGE_BUDGET_ACCEPTED_MB` to the printed total. The working set then exceeds RAM by 719 MB and the box, already
   swapping (~2 GB in use per ADR-0013), swaps more.

Never raise `hard_budget_percent` in `memory-budget.json` to make a refuse go away.

**Related, for this release's deploy only.** `EDGE_UP_PARALLEL_LIMIT=2` (section 4) throttles `up` because the release lands
~+540 MB at once on a swapping box; append it to the box's `.env` for that deploy and remove it afterwards.

### 6.4 SMTP, `OPS_ALERT_EMAIL` and the ops-monitor variables

**Two mailers, different rules.** notification (transactional platform mail) and ops-monitor (alerts) read the same
`SMTP_*` keys but implement SMTP separately:

| | notification | ops-monitor |
|---|---|---|
| Sends when | `EMAIL_TRANSPORT=smtp` | `SMTP_HOST`, `OPS_ALERT_EMAIL` and `EMAIL_FROM` all non-empty; it ignores `EMAIL_TRANSPORT` (INT:om/config.py:268) |
| STARTTLS | `SMTP_USE_TLS` true -> `starttls()` with no TLS context: the stdlib default context verifies neither the certificate nor the host name (checked on Python 3.12.3; INT:services/notification/app/transports.py:245-251) | `SMTP_USE_TLS` true -> `starttls(context=ssl.create_default_context())` (verified: INT:om/mailer.py:92-106) |
| Implicit TLS (465) | none | `SMTP_USE_SSL=true` (and `SMTP_USE_TLS=false`) |
| Login | when username **and** password set, even over a plain connection (INT:services/notification/app/transports.py:247-250) | when a username is set; **refuses** to log in over an unencrypted connection (INT:om/mailer.py:110-116) |
| Both TLS flags true | (no `SMTP_USE_SSL` key) | raises at SEND time, not at startup (INT:om/mailer.py:92-96) |

`SMTP_USE_TLS` is one shared key: choosing SMTPS for ops-monitor (`SMTP_USE_TLS=false`) also switches off STARTTLS for
notification, which has no implicit-TLS path. For the committed Google Workspace setup: `SMTP_USE_TLS=true`,
`SMTP_USE_SSL=false`, port 587.

**`OPS_ALERT_EMAIL`: two recipients, exactly as the owner stated** (the owner's instruction; not in the repo). Value: `info@opsgrids.com,zionna@opsgrids.com`
(comma, no space). The example ships the key empty; the checklist sets it in step 5.1, after the SMTP login probe printed OK, because setting it is what makes ops-monitor start sending, whatever `EMAIL_TRANSPORT` says. The code does no parsing: the field is a plain `str | None`
(INT:om/config.py:252), the value is assigned verbatim to the `To` header
(INT:om/mailer.py:89) and `smtp.send_message(message)` is called with no
explicit recipients (INT:om/mailer.py:117), so `smtplib` derives the envelope recipients
from the header. **Verified at the standard-library level only**, with an offline capture subclass (no socket opened),
on Python 3.12.6 (the production images use 3.12) and 3.13.2:

| `OPS_ALERT_EMAIL` | envelope recipients |
|---|---|
| `info@opsgrids.com,zionna@opsgrids.com` | both |
| `info@opsgrids.com, zionna@opsgrids.com` | both |
| `info@opsgrids.com;zionna@opsgrids.com` | **only the first, silently** |
| `info@opsgrids.com and zionna@opsgrids.com` | **only the first, silently** |
| `info@opsgrids.com zionna@opsgrids.com` | **only the first, silently** |

**No repository test sends to more than one address**: the three tests that set the field use one address
(INT:libs/platform-core/tests/test_ops_monitor_mailer.py:59,
INT:libs/platform-core/tests/test_ops_monitor_integration.py:320) and their fake
`send_message` ignores recipients. So multi-recipient delivery is stdlib behaviour, not a pinned contract; whether the
SMTP provider accepts both recipients is provider-side. Both recipients see each other in `To`.

**The `.env` quoting trap.** `.env` is `source`d as bash: `OPS_ALERT_EMAIL=info@opsgrids.com and zionna@opsgrids.com`
unquoted is not one assignment; bash runs `and zionna@...` as a command. On `193b529` this is exit 127 with no
`preflight: FAILED` footer (run R7b: `./.env: line 64: and: command not found`); since `ce35c77` it is a named
FAIL and nothing is sourced (run R5b). Quoting makes it parse, but the value then reaches the `To` header as written
and only the first address would receive mail (run R5c passes preflight and prints the sentence): use the comma form.

**`OPS_MONITOR_API_CONTAINERS` and `OPS_MONITOR_DLQ_QUEUES`.** Leave them empty: empty means *derive from
`COMPOSE_PROFILES`*, not *check nothing*, and a non-empty value **replaces** the derived list. Each entry must match
`^[a-z_][a-z0-9_.-]*$` (`Auth`, `a b`, `9lives`, `a/b` refused at startup). That check covers the names only: a `:port` that is not a plain integer is not validated at startup and aborts every monitoring cycle after the DLQ check
(logged; `/status` shows `last_cycle_error`), and a DLQ entry that already ends in `.dlq` or is misspelt is accepted and reported ok forever (see the two rows in section 3.10). The derived lists and the exact names are
in section 10.

**Cadence** (defaults; not settable from `.env` because compose forwards none of them): a check needs 3 consecutive
failing polls (about 3 minutes) to open and one email is sent; reminders every 6 hours; at most 12 emails per hour; a
daily digest at 08:00 UTC (INT:om/config.py:176-190).
`/status` shows `email: enabled` (a structural fact) and `last_send_at` / `last_error` (the evidence that a real send
happened) (INT:om/mailer.py:41).

### 6.5 Exact safe generation commands

Every generated secret is one command (category A, plus the fresh-box values of category D). **Generate on the box; never from development, another environment or a chat window;
never print a value into a log.** Use the checklist's fill helper, which writes through a mode-600 temporary file and
prints only the key name.

| Variable | Command | Strength | Notes |
|---|---|---|---|
| `ADMIN_GATEWAY_HOP_KEY` | `openssl rand -hex 32` | 256 bits, 64 chars | its own invocation; must differ from `INTERNAL_SERVICE_TOKEN` |
| `RAZORPAY_WEBHOOK_SECRET` | `openssl rand -hex 32` | 256 bits | category D on this box (very probably already there; the fill helper keeps an existing value): for a fresh box or a deliberate rotation, then paste the same value into Razorpay's webhook settings |
| `WHATSAPP_VERIFY_TOKEN` | `openssl rand -hex 32` | 256 bits | ASCII only (compare_digest on `str`); then paste into the Meta app |
| `ENGAGE_UNSUBSCRIBE_SECRET` | `openssl rand -hex 32` | 256 bits | rotating breaks every unsubscribe link already sent |
| `MINIO_ROOT_USER` | `openssl rand -hex 16` | 128 bits | URL-userinfo-safe |
| `MINIO_ROOT_PASSWORD` | `openssl rand -hex 32` | 256 bits | preflight requires >= 8 characters |
| `DOCUMENT_S3_ACCESS_KEY` | `openssl rand -hex 16` | 128 bits | keep different from the root and orphan users (provider-side, not verified in repo) |
| `DOCUMENT_S3_SECRET_KEY` | `openssl rand -hex 32` | 256 bits | preflight requires >= 8 characters |
| `DOCUMENT_S3_ORPHAN_ACCESS_KEY` | `openssl rand -hex 16` | 128 bits | set both orphan values at the first deploy |
| `DOCUMENT_S3_ORPHAN_SECRET_KEY` | `openssl rand -hex 32` | 256 bits | |
| *fresh box only, never on this one:* `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD` (alphanumeric only), `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY`, `RABBITMQ_PASSWORD` | `openssl rand -hex 32` each | 256 bits | they are expected to exist on this box (category D: an inference, not determinable from the repo); regenerating breaks logins, tokens or every sealed value |
| *fresh box only:* signing key file | `openssl genpkey -algorithm ed25519 -out /srv/opsgrids/secrets/jwt-ed25519.pem && chmod 600 /srv/opsgrids/secrets/jwt-ed25519.pem` | Ed25519 | only auth holds it; then `chown` it to the auth container's uid (see the `JWT_PRIVATE_KEY_HOST_PATH` row) |

### 6.6 `BILLING_RUN_ENABLED`, `BILLING_RUN_NOT_BEFORE`, `BILLING_TAX_CODE`, `BILLING_TAX_JURISDICTION`

- **`BILLING_RUN_ENABLED`**: write exactly `true`. Preflight treats only the literal `true` as on
  (INT:pf:520); the worker parses a pydantic boolean, so `1`, `yes`, `on`,
  `t`, `y` (any case) also switch the run ON inside the container while preflight prints the OFF warning (run R6e), and a
  value with a stray space (` true`, `true `) fails to parse and stops the worker (probes). Off by default.
- **`BILLING_RUN_NOT_BEFORE`**: exactly `YYYY-MM-DDTHH:MM[:SS[.f]]` then `Z` or `+HH:MM`/`-HH:MM`
  (INT:services/subscription/app/config.py:85). The service refuses anything else *softly* (value None,
  reason in `billing_run_not_before_invalid`, other loops keep running, the run does not start). Preflight applies the
  same pattern and, on the box (GNU `date`), asks whether it is a real instant. Probe results, service vs preflight:
  `2026-10-01T09:00:00+05:30` accepted by both; `...Z` and `...:00.5+05:30` accepted by both; `+0530`, a space instead of
  `T`, a naive time, a date only, and epoch seconds (`1790000000`) refused by both; `2026-09-31T09:00:00+05:30` passes the
  pattern and is refused by the service and by preflight's `date` check (R6g). A future cutover only warns (R6d2). An offset of exactly `+24:00` or `-24:00` passes preflight but is refused by the worker (run Q6): use `+05:30` or `Z`.
- **`BILLING_TAX_CODE` / `BILLING_TAX_JURISDICTION`**: see their rows. The jurisdiction is matched exactly (empty = only
  rows whose jurisdiction is NULL); no migration inserts a rate row, so the row is a hand-made data change that must exist
  before the code is set.
- **Staging (RUNBOOK 'The billing run').** Deploy with the run off; preview with
  `docker compose ... run --rm -T subscription-worker python -m app.billing_run --dry-run --not-before <cutover>` (writes
  nothing); then append `BILLING_RUN_ENABLED=true` and `BILLING_RUN_NOT_BEFORE=<cutover>` to `.env` and
  `docker compose ... up -d subscription-worker`. Set it once and never move it later; rows already billed are not revisited.

### 6.7 What the real parsers accept (probe results)

Each was run against the real `Settings` class of the final tree in a clean-environment subprocess from an empty
directory (so no real `.env` was ever read), with fake values. Pydantic 2.13.4 (the repo's lock).

| Setting | Accepted | Refused / surprising |
|---|---|---|
| any bool (`SMTP_USE_TLS`, `SMTP_USE_SSL`, `BILLING_RUN_ENABLED`, the MFA flag, `RUN_SWEEPS_IN_API`, ...) | `true false 1 0 yes no on off t f y n`, any case | `maybe`, `enabled`, and values with leading or trailing spaces refuse to parse; an **empty** value is read as unset (the default) |
| any int (`SMTP_PORT`, `DB_POOL_SIZE`, `ASSET_PURGE_RETENTION_DAYS`) | digits, surrounding spaces | `587abc`, `1.5`, `abc` refused; `0` and negative numbers **accepted** (no range check) |
| `EMAIL_TRANSPORT` | exactly `log`, `smtp` | `SMTP`, `Log`, `ses` accepted by `Settings` and refused by `transports.build` at startup |
| `WHATSAPP_TRANSPORT` | exactly `log`, `whatsapp_cloud` | `WhatsApp_Cloud` refused; `whatsapp_cloud` without id and token refused |
| `LOG_LEVEL` | `DEBUG INFO WARNING WARN ERROR CRITICAL FATAL NOTSET` (exact upper case) | `info`, `Info`, `TRACE`, `20` raise `ValueError: Unknown level` at startup |
| `CORS_ORIGINS`, `PII_DECRYPT_KEYS` | comma list; each item stripped; empties dropped | none |
| `TLS_PLATFORM_HOSTS` | comma list; stripped and lower-cased by the container | preflight matches literally (spaces/case break it) |
| `env_ignore_empty` (every service, INT:pc/config.py:65) | an empty value behaves as unset | `SMTP_HOST=` becomes `localhost`; `RAZORPAY_WEBHOOK_SECRET=` becomes the public dev default; `DB_POOL_SIZE=` in `.env` reaches the service as compose's default 3 (`:-3` treats empty as unset) |


## 7. (c) `MFA_ACCEPT_LEGACY_PLAINTEXT`: the full trace

**Bottom line.** It is a service setting, not an `.env` key. Its secure production value is **unset** (false), always. The
deploy sets it itself, on **one one-off container**, between `migrate` and `up`, and never on any running container.
The owner has nothing to set. What the owner should do is understand the reseal (below) and accept three things.

### 7.1 What the legacy format is, and why the code has the flag

- **The legacy format.** On the code the production ref runs, a TOTP seed is stored **as raw base32 text**:
  `factor.secret_enc = secret.encode()` (main:services/auth/app/services.py:786), read
  back with `.decode()` (main:services/auth/app/services.py:806),
  for tenant members **and** staff alike, even though the model's own docstring says the column is
  "envelope-encrypted" (main:services/auth/app/models.py:185). So `auth.mfa_factors.secret_enc`
  holds 16-32 characters of `A-Z2-7` as UTF-8 bytes, with no MAC.
- **The new format.** New auth seals every seed with the platform PII envelope (AES-256-GCM under a key derived from
  `PII_ENCRYPTION_KEY`) and a per-row AAD (`_factor_aad(user_id)`), so a sealed row cannot be swapped to another user
  (INT:services/auth/app/services.py:1113-1175).
- **Why the flag exists.** Reading has three tiers: (1) sealed with the row's AAD: returned; (2) sealed with no AAD (an
  interim envelope, GCM-authenticated): accepted and resealed; (3) **raw base32 plaintext**: nothing authenticates it
  (anyone who can write that column chooses the seed), so it is honoured **only while the flag is on**
  (INT:services/auth/app/services.py:1155). The shape test is `fullmatch` on
  `[A-Z2-7]{16,32}` (INT:services/auth/app/services.py:1090). The
  flag exists solely so one maintenance run can convert rows the old code wrote.

### 7.2 The code path that consumes it

1. **Setting:** `mfa_accept_legacy_plaintext: bool = False` (INT:services/auth/app/config.py:65).
   Probed: `true`, `1`, `yes` -> true; unset, `false` and empty -> false; `maybe` refuses startup.
2. **Read path:** `_open_factor_secret` tier 3 (INT:services/auth/app/services.py:1155) and
   `_is_refused_plaintext` (INT:services/auth/app/services.py:1101).
   Every accepted legacy row is resealed in place and audited as `auth.mfa_factor_resealed`.
3. **Sign-in:** `_accept_second_factor` (INT:services/auth/app/services.py:1418). With the flag off and
   a confirmed factor that is still plaintext, a six-digit code raises the **503 "Multi-factor re-seal pending"**
   (INT:services/auth/app/services.py:1471) **only when no confirmed factor was readable** (so a miss is
   not counted against the member); if any confirmed factor is readable, a miss is a real, counted miss (fixed in
   `3e8fdbb`). Confirming a new factor drops a confirmed legacy factor the deployment refuses, audited as
   `auth.mfa_factor_legacy_dropped` (INT:services/auth/app/services.py:1279).
4. **The maintenance command:** `python -m app.mfa_maintenance reseal` forces every factor through the tolerant read; it
   prints `resealed=N` and `remaining=N` (counts only) and **exits 1 if `remaining` is above zero**
   (INT:services/auth/app/mfa_maintenance.py:53-67).
5. **Who sets the flag:** only the deploy, as `-e MFA_ACCEPT_LEGACY_PLAINTEXT=true` on a one-off
   `docker compose run --rm --no-deps -T auth python -m app.mfa_maintenance reseal`
   (INT:mr:85-86). It is not in compose
   (INT:cp has no `MFA_ACCEPT_LEGACY_PLAINTEXT`), not in the example (INT:ex has no `MFA_ACCEPT_LEGACY_PLAINTEXT`) and not in the audit branch's
   example (checked); a release test forbids naming it in either
   (INT:tests/infra/test_mfa_reseal.py:213-218).
   Putting it in `.env` would do nothing (compose forwards no such variable) but would also be a lie in the file preflight reads.

### 7.3 Does production require it? What lets it be disabled?

- **Required only if the production database holds pre-sealing rows.** The production ref stores plaintext (above), so any
  member or staff account that has enrolled MFA on that ref has such a row. **How many is not determinable from the
  repo.** Count them, read-only, on the box:

  ```bash
  cd /srv/opsgrids/repo/infrastructure/edge
  docker compose -f compose.prod.yml --env-file .env exec -T postgres psql -U opsgrids -d opsgrids -Atc \
    "select count(*) from auth.mfa_factors where secret_enc is not null and encode(secret_enc,'escape') ~ '^[A-Z2-7]{16,32}\$'"
  ```

  It is the statement the deploy itself runs (INT:mr:41), and a test
  pins the RUNBOOK's copy to it. **0 means nothing to reseal.** `auth.mfa_factors` does not exist on a brand-new box.
- **The "migration" is a data reseal, not an Alembic migration.** No Alembic revision touches `secret_enc` except the initial
  schema; the merge revision `f8e432a62a84` "changes no table" (INT:rb:788). The step that lets the flag be
  disabled is `python -m app.mfa_maintenance reseal`, which `remote.sh migrate` runs **after** alembic and **before** `up`
  (INT:rs:274). When it reports `remaining=0`, no row needs the flag; it is never set again, on
  any long-running container.

### 7.4 Does enabling it weaken the security posture?

**Yes, narrowly, and only while the one-off container runs.** With it on, the trust rule changes from "the bytes must be a
GCM envelope under our key" to "or any base32-looking string". Anyone who can *write* `auth.mfa_factors.secret_enc` in that
window (SQL injection, a compromised database role, a bad restore) can plant a seed of their choosing, generate valid codes
for that user, and the service then seals it under the platform key and audits it as an ordinary `auth.mfa_factor_resealed`
row: the plant is indistinguishable from a legitimate legacy reseal. TOTP verification strength and replay protection are
unchanged. The larger standing weakness is the plaintext itself (anyone with database *read* access holds every seed);
the reseal removes it, so one run is a **net gain**. A *persistent* `true` would be the downgrade, and nothing in the
deploy can persist it.

### 7.5 The deploy order, and the deploy-time procedure

```text
preflight -> build -> validate -> migrate (alembic per service, THEN the reseal) -> up -> health -> reseal again (warn-only) -> record_ref
```

(INT:rs:509-521). Precisely, from `lib/mfa-reseal.sh` (INT:mr:56-96):

1. Ask Postgres whether `auth.mfa_factors` exists. **If Postgres cannot answer, abort the deploy.** If the table does not
   exist (a brand-new box before its first migrate), skip.
2. Count plaintext rows with the statement above. **If the count cannot be read or is not a number, abort.** `0` -> skip;
   nothing runs, not even a container.
3. Otherwise run the one-off container **from the new image** with the flag on. If it fails, abort before `up`.
4. Re-count. **If it is not `0`, abort before `up`.** The deploy never serves a new auth against rows it refuses.
5. After `health`, run the same step again, **warn-only**: it catches a factor enrolled through the *old* container during
   the window; its failure prints a warning and does not fail an already-healthy deploy (INT:rs:520).

(This replaces phase 1's description: the step used to *skip and continue* when the count could not be read; since
`3e8fdbb` it aborts, and it now also runs once more after `health`.)

**What you accept when you deploy this release:**

- **The window.** Between the reseal finishing and `up` recreating auth, the **old** auth container is still serving and cannot
  read a sealed envelope: its `_accept_second_factor` decodes `secret_enc` as UTF-8 *before* it looks at recovery codes, and
  a sealed envelope is never valid UTF-8. So for those seconds **every** MFA sign-in (authenticator code **and** recovery
  code) for a member whose factor was just sealed ends in a **500**. (Phase 1's claim that a recovery code still works in that window was
  wrong; the release's own test now pins the corrected claim.) Nothing is counted against them (the old code has no MFA
  lockout).
- **Rollback is a one-way door for MFA.** Once factors are sealed, `make edge-rollback` to the production ref `47143a0` (which
  predates the reseal) puts back code that cannot read them, and *every* MFA sign-in is a 500 until you roll forward. There is
  no unseal tool. The RUNBOOK's advice: prefer rolling forward with a fix (INT:rb:1539).
- **Staff enrolment codes.** Staff accounts that predate mandatory operator MFA need an out-of-band enrolment code
  (`bin/issue-enrollment-codes`, INT:rb:855). admin-gateway, the only place operators sign in,
  is not deployed on this box, so whether this applies is the owner's call.

**Safe pre-check, changing nothing:** the count statement above (before the deploy) and again after (must be 0).


## 8. (d) MinIO and S3: what each thing is

There is **no external S3 for uploaded images**: production uses a box-local MinIO behind the `storefront` profile. The only
external bucket in the repo is the *backup* destination, which is a different set of variables. Preflight requires every
MinIO and `DOCUMENT_S3_*` key even on a box whose `storefront` profile is off (it does not special-case profiles).

| Aspect | Variable(s) | Value and syntax | Where it comes from | Who uses it, and how |
|---|---|---|---|---|
| **Endpoint** (server side) | none: hard-coded | `http://minio:9000` on the `back` network (INT:cp:1041) | compose, not `.env` | `document` writes and deletes over it. Never through Caddy. No host port is published: only caddy publishes ports (INT:cp:167) |
| **Endpoint** (public reads) | `EDGE_HOST`, `S3_PUBLIC_BUCKET` | `https://<EDGE_HOST>/<bucket>/...` | Caddy proxies `/{$S3_PUBLIC_BUCKET}/*` to `minio:9000` on EDGE_HOST's own site only (INT:cf:124-154) | Buyers' browsers, anonymous GET |
| **Bucket** | `S3_PUBLIC_BUCKET` | default `opsgrids-public`; ONE bucket; no repo code validates the characters (convention: lower-case letters, digits, hyphens; S3/MinIO's own rules are provider-side, not verified in repo) | `.env` (choose); created by `minio-init` (`mc mb --ignore-existing`) | caddy (path matcher), minio-init, document |
| **Region** | none from `.env` | the document service's default `us-east-1` (INT:services/document/app/config.py:37) ("MinIO ignores it, boto3 requires one"); compose forwards no `S3_REGION` (INT:cp has no `S3_REGION`) | not configurable in this deployment | boto3 |
| **Path style** | none: hard-coded | `S3_FORCE_PATH_STYLE: 'true'` (INT:cp:1042) | compose | boto3 (MinIO serves `host/bucket/key`) |
| **Public or private** | `S3_PUBLIC_BUCKET` | **public-read in its entirety**: anonymous `s3:GetObject` only, no `ListBucket` (INT:cp:490). The private half is "named and unbuilt" (INT:services/document/app/config.py:42) | minio-init on every deploy (idempotent) | anyone on the internet can read any object whose path they know; nothing not meant to be public may be uploaded |
| **Root access key / secret** | `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | user: no enforced format (URL-userinfo-safe, so hex); password >= 8 characters (preflight) | generate on the box | `minio` (server root) and `minio-init` via `MC_HOST_og`, an `http://` URL to `minio:9000` whose userinfo is the root user and root password (INT:cp:452); nothing else authenticates as root; console off (INT:cp:407) |
| **Document-specific credentials** | `DOCUMENT_S3_ACCESS_KEY`, `DOCUMENT_S3_SECRET_KEY` | free-choice access key (hex-16), secret >= 8 characters; keep it different from root (provider-side, not verified in repo) | generate on the box | `minio-init` creates a MinIO **user** with a policy allowing only `s3:PutObject` and `s3:DeleteObject` on the bucket's objects (INT:cp:522); `document` receives them as `S3_ACCESS_KEY` / `S3_SECRET_KEY` (INT:cp:459). It cannot list, read, touch another bucket, or change the bucket policy |
| **Orphan-sweep credential** | `DOCUMENT_S3_ORPHAN_ACCESS_KEY`, `DOCUMENT_S3_ORPHAN_SECRET_KEY` | as the document pair, different from it | generate on the box (both, at the first deploy) | `minio-init` creates a second user (`s3:ListBucket` on the bucket, `s3:DeleteObject` on objects) **unconditionally** (INT:cp:538) while preflight allows both empty (run R3i). Since the memory-headroom merge **no always-on container holds it**: the orphan pass runs only by hand, the credential handed to one container as `S3_ORPHAN_ACCESS_KEY` for one run (INT:rb:450). **On `193b529` `document-cron` still held it** (PRE:cp:1087); that container is gone |
| **Retention / purge** | `ASSET_PURGE_RETENTION_DAYS` | integer days, default 30; no range check | `.env` | the purge sweep in the `document` API (`RUN_SWEEPS_IN_API`) deletes soft-deleted objects older than this using the document credential |
| **Public URL** | `PUBLIC_ASSET_BASE_URL` | exactly `https://<EDGE_HOST>/<bucket>` | derived | `document` (preview URL); baked into the storefront bundle by `publish-web.sh` (`VITE_ASSET_BASE_URL`); republish the bundles after changing it |
| **The external S3 (backups)** | `BACKUP_S3_*` | see section 3.8 | the backup provider | `backup.sh` only, via a pinned aws-cli container; the *object storage* backup also tars MinIO's data volume and uploads it there |

MinIO's own credential length limits beyond "password >= 8" (which preflight enforces for the root and document secrets only)
are not stated in the repo, and neither is a rotation procedure.

## 9. (e) Engage and WhatsApp: who owns each credential

Only two `ENGAGE_*` values are environment variables an operator sets. Five more `ENGAGE_*` tunables exist as Settings fields
(`ENGAGE_TEST_SEND_RATE_LIMIT_PER_MINUTE`, `ENGAGE_CAMPAIGN_BATCH_SIZE`, `..._POLL_INTERVAL_SECONDS`,
`..._CLAIM_LEASE_SECONDS`, `..._PAUSE_RETRY_SECONDS`; INT:services/notification/app/config.py:85-116)
but compose forwards none of them, so they cannot be set from `.env`.

| Credential / value | Env var | Belongs to | Issued by / where obtained | Consumer and mechanism |
|---|---|---|---|---|
| Meta app secret | `WHATSAPP_APP_SECRET` | **Meta / WhatsApp app**; used for **webhook verification** (`X-Hub-Signature-256`) | Meta: your app -> Settings -> Basic (provider-side, not verified in repo) | notification verifies the HMAC over the raw body; with none configured it **refuses** webhooks (INT:docs/whatsapp/webhooks.md:137) |
| Webhook verify token | `WHATSAPP_VERIFY_TOKEN` | **Webhook verification** (Meta's subscribe handshake); **self-generated** | you generate it, then paste it into the Meta app's webhook configuration | `hmac.compare_digest(token, expected)` on the `GET` handshake (INT:services/notification/app/webhooks.py:89) |
| Access token | `WHATSAPP_ACCESS_TOKEN` | **Meta / WhatsApp Cloud API** (bearer for sending) | Meta Business settings -> System users (provider-side, not verified in repo) | only the platform-level `whatsapp_cloud` transport: `POST {graph}/{phone_number_id}/messages` with `Authorization: Bearer` (INT:services/notification/app/transports.py:306) |
| Phone number id | `WHATSAPP_PHONE_NUMBER_ID` | **Meta / WhatsApp** (the sender identifier, not a secret) | Meta: WhatsApp -> API Setup | same transport |
| Transport switch | `WHATSAPP_TRANSPORT` | platform configuration | choose | `log` or `whatsapp_cloud` (section 3.6) |
| Unsubscribe-link signing key | `ENGAGE_UNSUBSCRIBE_SECRET` | **Engage itself; signing/authentication** (HMAC-SHA256 over the recipient) | generated on the box; no external issuer | signs and verifies unsubscribe links before any tenant is known (INT:services/notification/app/config.py:88) |
| Public API base | `ENGAGE_PUBLIC_API_BASE` | **Engage configuration** (not a credential) | choose | prefix of the unsubscribe link in every campaign email |
| A tenant's own WhatsApp token | none: not env | **Meta**, per tenant | the tenant, through the app | stored sealed per tenant (`provider_accounts.access_token_enc`, INT:docs/whatsapp/webhooks.md:135) |
| A tenant's Engage sending connection | none: not env | **another external provider**, per tenant: Google Workspace, Microsoft 365 or the tenant's own SMTP | the tenant, through the app | the connection kinds are `google`, `microsoft`, `smtp`, `platform` (INT:services/notification/app/schemas.py:251); `platform` (platform-managed sending) is **refused** because the platform runs no MTA (INT:services/notification/app/services.py:1990) |
| Platform SMTP | `SMTP_*`, `EMAIL_FROM` | **another external provider** (Google Workspace mailbox) | Google | transactional platform mail and ops-monitor alerts. It is **not** how a tenant's Engage campaign is sent |

### 9.1 Do the provider webhooks reach the services on this box?

Two provider webhooks need to be reachable from the internet for the secrets above to mean anything: Meta's (WhatsApp) and Razorpay's.
**Reading the code, neither route is publicly reachable through the edge as deployed.** This is a static finding; the live
probe below decides it, and it needs no credential.

*WhatsApp.* notification mounts the webhook at the **root**, `/webhooks/whatsapp`
(INT:services/notification/app/main.py:34,
INT:services/notification/app/api/webhooks.py:69). The gateway's public-path list is an
exact-match set of *request paths* and contains `/webhooks/whatsapp` (root)
(INT:pc/gateway.py:334). Caddy proxies only `/api/*` to the gateway (INT:cf:84), so a root path is not
proxied to it. The production route table sends `/api/v1/webhooks/whatsapp` to notification (INT:rt:67),
but that path is **not** in the public set, and the routes file itself says "A prefix here does not make anything public — the
public paths are an exact-match frozenset" (INT:rt:15). The gateway forwards
`route.upstream + full_path` (INT:pc/gateway.py:715) and demands a bearer token unless the path is public
(INT:pc/gateway.py:483-498).
A third spelling, `/api/v1/notifications/webhooks/whatsapp`, is in `docs/whatsapp/audit.md`
(INT:docs/whatsapp/audit.md:133).

**Fixed for WhatsApp since this finding was written** (the paragraph above describes the state before): app-gateway declares
`/api/v1/webhooks/whatsapp` (exactly, `GET` and `POST` only) in `WEBHOOK_PUBLIC_PATHS`, notification mounts the router under `/api/v1`
as well as at the root, and the route carries a 3 MiB `max_body_bytes`; see `docs/whatsapp/webhooks.md`. Once that build is deployed
the probe below answers **`403`** for WhatsApp (the request reached notification and `hub.verify_token=x` did not match; `401` or `404`
would mean it did not deploy). Razorpay is **unchanged** and still `401` at the gateway.

*Razorpay.* payment mounts its webhook under `/api/v1` (`/api/v1/webhooks/razorpay`, INT:services/payment/app/api/webhooks.py:9), and the route table forwards it
(INT:rt:63). But it is made public **only by admin-gateway**
(INT:services/admin-gateway/app/main.py:67), which is **not deployed** here. app-gateway's public set is
`DEFAULT_PUBLIC_PATHS | STOREFRONT_PUBLIC_PATHS | ACCOUNT_PUBLIC_PATHS` plus the Engage unsubscribe prefix
(INT:services/app-gateway/app/main.py:105), none of which lists it.

**The consequence.** Even with perfect secrets, a provider's unauthenticated `POST` would get a 401 "Missing bearer token" from the
gateway (or a page from Caddy for the root path) instead of reaching the signature check. No test or doc in the repo shows a working
callback URL for this box. **Not determinable from the repo whether it works end to end**; the two calls below settle it and are safe:
they hit only our own endpoint with an empty body and no credential. A bad signature is a 400 and nothing is recorded (INT:services/payment/app/services.py:58). A verify-token mismatch would be a 403 at the service (INT:services/notification/app/api/webhooks.py:92), but the WhatsApp URL below can never get that far: the service mounts the route at the root while the gateway forwards `/api/v1/webhooks/whatsapp` with its prefix intact (INT:pc/gateway.py:715), so even a public path would 404, and Meta's callback would have to be `/webhooks/whatsapp` at the root of a host, which Caddy does not proxy.

```bash
# from any machine. Razorpay: 401 = stopped at the gateway (route not public); 400 = reached payment (bad signature, nothing recorded); 404 = no such route.
# WhatsApp (after the webhook fix is deployed): 403 = reached notification, verify token did not match (the expected answer); 401 = stopped at the gateway; 404 = no such path on notification.
curl -s -o /dev/null -w '%{http_code}\n' -X POST https://app.opsgrids.com/api/v1/webhooks/razorpay -H 'Content-Type: application/json' -d '{}'
curl -s -o /dev/null -w '%{http_code}\n' 'https://app.opsgrids.com/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=x&hub.challenge=1'
```

Until that is settled, keep `WHATSAPP_TRANSPORT=log`, do not configure a webhook URL in the Meta or Razorpay dashboards, and treat
`RAZORPAY_WEBHOOK_SECRET`, `WHATSAPP_APP_SECRET` and `WHATSAPP_VERIFY_TOKEN` as **unverifiable**: this is an open decision (section 12).


## 10. (f) Monitoring: the exact names the application expects

**What runs.** One always-on container, compose service **`ops-monitor`** in project `opsgrids-edge`
(INT:cp:40), image `opsgrids-edge/product`, command `python -m platform_core.ops_monitor`
(INT:cp:758), `mem_limit: 128m`, on the `front` and `back` networks. It serves
`/metrics` and `/status` on port **9110** (`OPS_MONITOR_PORT: '9110'`, INT:cp:792), never behind Caddy or a gateway,
and keeps state under `/srv/opsgrids/state/ops-monitor/` (`ops-monitor.json` and `ops-monitor.heartbeat`), reading the backup
marker `/srv/opsgrids/state/backup.last-ok` read-only (INT:cp:795).
Its healthcheck is the heartbeat file being younger than three minutes. `make edge-status` prints its `/status`.

**Checks and their ids** (every 60 s): `outbox.<schema>` (per schema in `EDGE_RELAY_SCHEMAS`, INT:om/checks.py:154),
`dlq.<queue>` (a passive declare of **`<queue>.dlq`**, durable, INT:om/checks.py:195; a queue not yet declared is
ok), `api.<container>` (`GET http://<container>:<port>/health/ready`, port 8000 by default, INT:om/checks.py:285),
`disk` (path `/`, alert under 15% free), `backup` (the marker, alert older than 26 h), `cert` (a TLS handshake to `caddy:443` with SNI
`EDGE_HOST`, alert under 14 days) (INT:om/config.py:236-247).

**API containers it expects** (`OPS_MONITOR_API_CONTAINERS` empty = derived from `COMPOSE_PROFILES`, tables in
INT:om/config.py:48-79). The names are compose service names, resolved by Docker DNS on `back`:

| Profile | API containers checked |
|---|---|
| always (core) | `auth`, `account`, `product`, `app-gateway`, `catalog`, `subscription`, `billing`, `metering`, `payment`, `notification` (10) |
| `storefront` | `storefront`, `document` |
| `inventory` | `operations` |
| `hrms` | `approval`, `employee`, `attendance`, `leave` |
| `crm` | `customer`, `deal`, `lead` |
| `pulseboard` | `project`, `timesheet`, `standup` |
| `projects` | `projects` |
| `support` | `support` |
| `marketing` | `marketing` |
| `finance` | `finance` |
| `itsm` | `itsm` |
| **all ten declared** | **28 names** (10 + 18); probed on the real settings class: exactly these, in this order |

**Dead-letter queues it expects** (`OPS_MONITOR_DLQ_QUEUES` empty = derived; a check is `{queue}.dlq`;
INT:om/config.py:63-97):

| Profile | Queues (each checked as `<queue>.dlq`) |
|---|---|
| always (core) | `account.events`, `product.events`, `subscription.events`, `payment.events`, `notification.events`, `notification.dispatch` (6) |
| `hrms` | `approval.events`, `attendance.events`, `leave.events` |
| `crm` | `customer.events` |
| `finance` | `finance.events`, `scheduler.events` |
| `storefront`, `inventory`, `pulseboard`, `projects`, `support`, `marketing`, `itsm` | none (no consumer today) |
| **all ten declared** | **12 queues** |

`COMPOSE_PROFILES=*` derives the same 28 and 12 (probe), in a different order. Entries you set by hand are only half-validated (a bad `:port` aborts every cycle after the DLQ check; a `.dlq` suffix or a typo is silently un-monitored): see the two rows in section 3.10. Outbox lag is checked for the schemas in
`EDGE_RELAY_SCHEMAS` (ops-monitor's own default list has 27 names and omits `lead`; section 4).

**Every compose service name, by profile** (from `memory-budget.json`, 47 containers; a service missing here has no price and preflight prices it as
`kinds.background` with a warning):

| Profile | Containers |
|---|---|
| core (21) | `caddy`, `postgres`, `rabbitmq`, `redis`, `auth`, `account`, `product`, `app-gateway`, `catalog`, `subscription`, `billing`, `metering`, `payment`, `notification`, `edge-relay`, `ops-monitor`, `account-worker`, `product-worker`, `subscription-worker`, `payment-worker`, `notification-worker` |
| `storefront` (4) | `minio`, `minio-init` (a one-shot labelled `opsgrids.oneshot=true`, expected to exit 0), `storefront`, `document` |
| `inventory` (1) | `operations` |
| `hrms` (7) | `approval`, `approval-worker`, `employee`, `attendance`, `attendance-worker`, `leave`, `leave-worker` |
| `crm` (4) | `customer`, `customer-worker`, `deal`, `lead` |
| `pulseboard` (3) | `project`, `timesheet`, `standup` |
| `projects`, `support`, `marketing`, `itsm` (1 each) | `projects`, `support`, `marketing`, `itsm` |
| `finance` (3) | `finance`, `finance-worker`, `scheduler` |

**Read-only ways to see them on the box:** `make edge-status HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com`;
`docker compose -f compose.prod.yml --env-file .env ps`; on a box running `ce35c77` or later `docker compose ... ps | grep -c -- '-cron'` should print 0
(INT:rb:1528).

## 11. (g) Known gotchas

1. **Phase-1's `env-reference.md` is out of date on these points**, all corrected here: the MFA reseal is now fail-closed (it
   aborts when it cannot count; it used to skip) and runs once more after `health`; during the reseal-to-`up` window
   recovery codes fail too (not only authenticator codes); the ten-profile price is 4581 MB (not 4112) with a 125% cap on any
   ceiling; `COMPOSE_PROFILES=*` is valid (memory-headroom is merged); `RAZORPAY_WEBHOOK_SECRET` is guarded **only by preflight**;
   `BILLING_TAX_JURISDICTION` is optional and matched exactly; `SENTRY_DSN` and the Razorpay key pair were inert (at that point; the pair is used when `PAYMENT_WIRE=live` since ADR-0032); the staff
   console backend, the billing run, the Engage UI, memory-headroom (`ce35c77`) and io-outside-txn (`5320848`) are all merged into integration; the TLS row and the exit-127 defect are fixed there; and
   the final fixup `0ff2954` changed how `.env` is read (items 2, 4 and 5 below).
2. **The two preflight defects (the owner's gotchas). Fixed on integration `ce35c77`; still live on any ref before it.** They are what
   a deploy of `193b529` or earlier hits, and `make edge-preflight` runs the preflight of whatever ref is checked out on the box (item 18), so
   confirm the ref before you trust either behaviour.
   (a) *The Caddyfile catch-all row.* The catch-all site line `https:// {` (INT:cf:429) was read by preflight's `sed` as an empty
   site name, so `FAIL the Caddyfile serves  but TLS_PLATFORM_HOSTS does not list it` appeared on **every** run of `193b529` whatever the values
   are (INT:pf:615-621). **Reproduced on `193b529`** (run R7a); **fixed on
   integration since `ce35c77`** (pattern `[a-z0-9.-]\{1,\}`, INT:pf:621; run R7c: the row is gone). `make deploy` refuses at its first step, so a ref without that fix cannot be deployed.
   Do not edit preflight on the box: deploy a ref that contains the fix.
   (b) *Unquoted whitespace exits 127.* `.env` is `source`d as bash, so `EDGE_PROFILES=inventory, storefront` or
   `OPS_ALERT_EMAIL=a and b` runs the words after the first space as a command: on `193b529` `./.env: line 64: and: command not found`,
   exit 127, **no `preflight: FAILED` footer** (run R7b, on `193b529`). Since `ce35c77` it is a named FAIL before anything is sourced
   (INT:pf:101-154; runs R4m, R5b, R5e), and the same pre-check now also refuses an unquoted shell metacharacter
   (`; & | < > ( )`, a backtick, `$(`: R8a, R8c), text after a closing quote (R8b) and CRLF line endings (R8l). Quote a value that contains a space or
   one of those in double quotes (single quotes if it also has `$`, a backtick or a backslash: item 4), or avoid them.
3. **Which ref you deploy decides both.** `193b529` (integration before the memory-headroom merge) still has both defects **and** would not tolerate the two
   keys `.env.example` gained with memory-headroom (`EDGE_PROFILES`, `EDGE_BUDGET_ACCEPTED_MB`: `FAIL missing or empty in .env: EDGE_PROFILES
   EDGE_BUDGET_ACCEPTED_MB`, run R7a). Deploy `origin/release/backlog-complete` at `5320848` or later, the final release (`git merge-base --is-ancestor 5320848 <ref>` must succeed; the preflight fixes alone need `ce35c77`).
   `slice/env-audit` is documentation plus `.env.example` annotations, so it does not need to be in the deployed ref. Merging it into integration (checked with a tree-level merge of `origin/release/backlog-complete` and the fix-pass tree) conflicts in `infrastructure/edge/.env.example` only; the docs merge cleanly. Resolve by taking the audited file: all 66 of its uncommented `KEY=` lines are identical to integration's (the required secrets are empty in both, on purpose: item 20), so only comments change (14 integration comment lines are reworded or superseded, among them the store-slug cache note that item 12 and the `STORE_SLUG_CACHE_SECONDS` row show to be wrong).
4. **`.env` special characters: the rule is not "double-quote everything".** `.env` is `source`d by bash (preflight and backup.sh: INT:pf:166-169, INT:bk:38-41) and parsed separately by docker compose (`--env-file`); the two agree on the rules below (each was run: section 13.6, runs Q1-Q8).
   - A plain value (letters, digits, `. _ - / : @ , = +`) needs no quotes. Hex from `openssl rand -hex` is one, which is one more reason to use it.
   - A value with a **space** or any of `; & | < > ( )`: **double** quotes.
   - A value with **`$`, a backtick or a backslash**: **single** quotes. Double quotes do **not** neutralise them: bash expands `$VAR`, `$(cmd)` and backticks inside double quotes, and compose expands `$VAR` there too. Measured:
     a `.env` value of `"ab$(touch MARKER)cd"` (set on the `SMTP_PASSWORD` key) passed preflight's pre-check and **executed the command** (the marker file was created; run Q1); `"ab$cd"` and unquoted `ab$cd` aborted preflight with `./.env: line N: cd: unbound variable` and no `FAILED` footer
     (a plain `$VAR` is silent only when the variable happens to be defined; run Q2); compose read `"ab$cd"` as `ab` with the warning `The "cd" variable is not set` (run Q8). `'ab$cd'` is literal in both. A value that also holds a single quote: regenerate it.
   - `#` starts a comment only after whitespace: compose read `ab#cd` literally and cut `ab #cd` to `ab` (run Q8).
   - Every quote must be closed.

   What preflight's pre-check (INT:pf:101-154) does and does not do. It refuses an **unquoted** value with whitespace, `; & | < > ( )`, a backtick or `$(`, text after a closing quote, and CRLF; it accepts `export KEY=...`, a trailing space and an inline
   ` # comment` (runs R8j, R8k). It looks at **quoted** values only for text after the closing quote, and skips a quoted value that contains a backslash entirely (INT:pf:124,130). It does **not** flag `$VAR`, `$(...)` or a backtick inside double quotes,
   and it does **not** flag an **unbalanced quote** or a bare quote inside a value (`abc"def`, `"abc def`): the loop `continue`s when it finds no closing quote, and the later `source` dies with `unexpected EOF while looking for matching`, exit 2, no `FAILED`
   footer, the same failure shape as the exit-127 defect (run Q7). The checklist's step 2.2 has a read-only check for unbalanced quotes.
5. **Several programs read `.env`, and they disagree in six places.** preflight and backup.sh `source` it and compose parses it with its own parser (both: a repeated key, the last wins); remote.sh reads selected keys through `dotenv_get` (INT:rs:56-74);
   two more helpers read the FIRST line. The differences that bite:
   - **`export` and indentation.** `dotenv_get` reads EDGE_HOST, EDGE_PROFILES, COMPOSE_PROFILES and `EDGE_UP_PARALLEL_LIMIT`: the **last** line that **starts with `KEY=` at column 0** wins, a quoted value is what is between its quotes, an unquoted value's
     ` # comment` is dropped, spaces and CR are removed (run R8m for last-wins). It does **not** see `export KEY=value` or an indented `  KEY=value`, which preflight accepts (INT:pf:110): the real function returned an empty value for both (run Q5).
     Consequence: `export COMPOSE_PROFILES=inventory,...` is priced as ten profiles by preflight, but remote.sh resolves an empty list and exports `COMPOSE_PROFILES=""`, and an exported-empty value overrides the env-file in docker compose (measured with compose v2.35.1:
     21 services instead of 47; run Q5b), so `migrate` and `up` would run core only after a green preflight. `export EDGE_HOST=...` makes assert-host report `<unset>` and refuse (fails safe). **Write these four as plain `KEY=value` at column 0.**
   - **First-line readers in remote.sh:** `OPSGRIDS_DB_PASSWORD` (INT:rs:213) and `OPS_MONITOR_PORT` (INT:rs:543), the FIRST `^KEY=` line with quotes, spaces and CR deleted.
   - **First-line raw readers in publish-web.sh** (laptop side, over ssh): `EDGE_WEB_ROOT` (`sed -n 's/^EDGE_WEB_ROOT=//p' | head -1`, INT:pw:157) with no unquoting and no comment or CR handling, so `EDGE_WEB_ROOT="/srv/opsgrids/web"` becomes a path beginning with a double quote
     and publishes to the wrong place while Caddy serves the real one; and `PUBLIC_ASSET_BASE_URL` (INT:pw:239-250), which strips only a CR and one pair of quotes and KEEPS an inline ` # comment`, so preflight (which drops the comment and passes its equality check) and
     the bundle (which bakes the comment into `VITE_ASSET_BASE_URL`, still satisfying the `https://` test at INT:pw:257-265) disagree. **Write `EDGE_WEB_ROOT` and `PUBLIC_ASSET_BASE_URL` bare: no quotes, no inline comment, no CRLF.**
   - **`EDGE_PROFILES` is invisible to compose.** Hand-run `docker compose ... --env-file .env` commands see only `COMPOSE_PROFILES`; only preflight.sh and remote.sh honour `EDGE_PROFILES` (INT:ap:60-67), so with a non-empty value those commands address a different profile set than the deploy.
   Before `0ff2954` remote.sh took the FIRST line and kept an inline comment for all four keys, so an operator who appended a corrected line at the bottom made it judge a different list than preflight had checked. Keep every key exactly once.
6. **Empty means unset.** Every service ignores empty environment values (`env_ignore_empty`, INT:pc/config.py:65): `SMTP_HOST=`
   becomes `localhost`; an empty `RAZORPAY_WEBHOOK_SECRET` becomes the public development default; compose's `:-default` also treats
   empty as unset.
7. **"Required" means two things.** The line must exist for every key; the value must be non-empty unless the key is in
   `optional_empty`; and `EDGE_PROFILES` / `EDGE_BUDGET_ACCEPTED_MB` may even be absent. Appending missing keys from the example is safe:
   each carries a placeholder or empty value and preflight refuses placeholders.
8. **Defaults differ between the example and compose.** `DB_POOL_SIZE` is 5 in the example and 3 in compose; `EMAIL_TRANSPORT` and
   `WHATSAPP_TRANSPORT` default to `log` in both. The value in `.env` wins whenever the line is non-empty.
9. **Inert or unused settings** (filling them changes nothing): `JWT_KID`; `SENTRY_DSN` (no SDK); `DOCUMENT_ORPHAN_SWEEP_ENABLED` / `_DRY_RUN` (no container reads them since the memory-headroom merge).
10. **Settings fields compose does not forward, so `.env` cannot set them:** the five `ENGAGE_*` tunables; WhatsApp's Graph base
    (`whatsapp_api_base`, pinned to v25.0); the ops-monitor thresholds (poll interval, alert caps, 26 h backup age, 15% disk, 14-day
    cert warning); the document service's upload limits and orphan tunables; and, since the io-outside-txn merge, payment's
    `charge_reclaim_after_seconds`, `charge_sweep_poll_interval_seconds`, `charge_max_attempts`, `provider_call_timeout_seconds`, `charge_final_lookup_attempts` and
    finance's `portal_call_timeout_seconds`, `filing_reclaim_poll_interval_seconds` (INT:services/payment/app/config.py:44).
    Both services also refuse to start when a reclaim window is shorter than twice the provider call timeout plus a minute (INT:services/payment/app/config.py:61);
    the defaults satisfy it and compose forwards none of these names, so `.env` cannot trip it. Changing any of them means editing compose.
11. **Two `EDGE_RELAY_SCHEMAS` defaults differ:** edge-relay's includes `lead`, ops-monitor's does not, so ops-monitor's outbox check does not
    watch the `lead` schema.
12. **The RUNBOOK has stale spots.** Its 'Secrets' step lists only some keys to generate (INT:rb:164-171):
    it omits `RABBITMQ_PASSWORD`, `RAZORPAY_WEBHOOK_SECRET`, `ENGAGE_UNSUBSCRIBE_SECRET` and every `DOCUMENT_S3_*` key, and its table says `document` "reuses the same credential" as MinIO's root
    (INT:rb:201), while compose gives `document` its own scoped user. It says the orphan pair 'may be empty' (INT:rb:1417) and that the next deploy's `minio-init` creates the scoped user after you generate them (INT:rb:461-465), while compose runs
    `mc admin user add` on them unconditionally on every deploy (INT:cp:538): set both at the first deploy (item 19). Its 'take one merchant off the air' step attributes the removed-domain window to `STORE_SLUG_CACHE_SECONDS` (INT:rb:1173), which is a
    different cache (row `STORE_SLUG_CACHE_SECONDS`). Use the tables in this document.
13. **Sweeps need `RUN_SWEEPS_IN_API` to stay truthy** (the default is `true`): a falsy value silently stops stock reconciliation, custom-domain
    verification and upload purging, with every container healthy; preflight warns (R6f). It now reads the flag as the services do (`True`, `1`, `yes`, `on`, `y`, `t` count as on: R8h);
    an unparsable value (`maybe`) makes preflight warn "OFF" while the API containers refuse to start (R8i).
14. **Provider webhooks probably do not reach the services** (section 9.1): settle it with the two unauthenticated probes before configuring a callback URL.
15. **Notification's STARTTLS does not verify the server certificate.** `SMTP_USE_TLS=true` makes notification call `starttls()` with no TLS context (INT:services/notification/app/transports.py:245-251), and the stdlib default context verifies neither
    the certificate nor the host name (`ssl._create_stdlib_context()`: `verify_mode` CERT_NONE, `check_hostname` False; checked on Python 3.12.3, the production images' minor version; run Q12). An on-path attacker between the box and the SMTP host could read the app
    password on that path. ops-monitor's own STARTTLS verifies (INT:om/mailer.py:92-106). Notification also logs in over a plain connection when `SMTP_USE_TLS=false` (ops-monitor refuses). Keep `SMTP_USE_TLS=true` on 587, and treat the fix
    (pass `ssl.create_default_context()`) as a code change, not an env value.
16. **A dev `.env` exists locally** (untracked, repo root of the main checkout) holding 12 of these keys (`APP_BASE_URL CORS_ORIGINS INTERNAL_SERVICE_TOKEN JWT_ISSUER JWT_KID LOG_LEVEL PII_DECRYPT_KEYS PII_ENCRYPTION_KEY SMTP_HOST SMTP_PASSWORD SMTP_PORT SMTP_USE_TLS`).
    Only key PRESENCE was checked (`grep -c`); no value was read, so whether any of them is a development value is not determinable from the repo. Never copy or promote any of it. Also at audit time an untracked private-key file (`*.pem`) sat at the root
    of the main checkout and was not read; `*.pem` and `*.key` are ignored on integration and on this branch (commit `cdd1e14c`) but not yet on `origin/main`, so in a checkout based on main it is not ignored: never `git add -A` there.
17. **Laptop-only preflight rows.** On macOS `df -Pi` reports a different column, so `inodes 100% used` FAILs; low free disk FAILs; and
    the cutover row's `date -u -d` needs GNU `date`. They are box-only rows; run preflight on the box (`make edge-preflight`), not on a laptop.
18. **`make edge-preflight` runs whatever ref is checked out on the box.** To preflight a specific ref that is not the one checked out use
    `infrastructure/edge/ssh-deploy.sh deploy@20.219.18.84 edge.opsgrids.com <ref> preflight`, which first checks that ref out (detached) on the box.
19. **Empty orphan credentials.** Preflight lets `DOCUMENT_S3_ORPHAN_*` be empty (run R3i passes) but `minio-init` runs `mc admin user add`
    with them unconditionally under `set -e`; what `mc` does with two empty strings is not determinable from the repo. Set both.
20. **The example's required secrets are empty on purpose.** An earlier draft of the annotated example carried `CHANGE_ME_generate_...` placeholders. Preflight refuses those (it refuses an empty value too), but the services do not: measured against
    integration `5320848` with the real `Settings` classes and `ENVIRONMENT=production`, `auth`, `notification` and `payment` all **started** with the placeholders in place (`platform_core` refuses only the `dev-only-`/`ci-only-` prefixes and values under 32 bytes,
    and the placeholders are 55-65 bytes), and notification then had Engage link signing ON with a repo-public string; with the values empty, the same three **refused** to start (an empty value falls back to the `dev-only-` default, which they refuse; run Q9).
    Any path that skips preflight (a hand-run `docker compose up`, `remote.sh up`, a forgotten key) would therefore have started on a public key. The example keeps them empty, exactly as integration does, so a verbatim copy fails closed in preflight **and** in the services.


## 12. Unresolved, and not determinable from the repository

Nothing below is a guess; each is either an owner decision or something the repo cannot tell.

| # | Item | Why it is open | What settles it |
|---|---|---|---|
| 1 | What the box's `.env` actually contains, and which ref it last deployed | The VM and its `.env` were not touched. The RUNBOOK names `47143a0` from a read-only look during review; not re-verified. Every category-D row is an inference | the read-only inventory in the checklist (part 2) |
| 2 | Whether any production MFA row is still plaintext, and how many | It depends on data in the production database | the count statement (section 7.3), run read-only before deploying |
| 3 | **Whether the Razorpay and WhatsApp webhooks are reachable** | By static reading neither route is public through the edge (section 9.1); no test or doc gives a working callback URL | the two unauthenticated probes in section 9.1; if 401, it is a code/route decision (category E), not an env value |
| 4 | Whether any real Razorpay credential can ever be verified by the platform | The adapter's only outbound call is a stub in this release | an owner-run call made directly to Razorpay (provider-side); or the adapter being implemented |
| 5 | **The memory decision** (`EDGE_BUDGET_ACCEPTED_MB`, `COMPOSE_PROFILES`) | Ten profiles price at 4581 MB (118.6%) on a 3862 MB box; the box is already over the hard line. Resize, de-list, or accept the overcommit is the owner's call | preflight's printed total on the box, then a written choice (section 6.3) |
| 6 | The exact number preflight will print on the box | It prices running-and-still-defined containers plus the declared services, so it depends on live state; my scratch run had nothing running (4581) | read it on the box |
| 7 | The billing cutover (`BILLING_RUN_ENABLED`, `BILLING_RUN_NOT_BEFORE`) | An irreversible money decision; also whether/when to turn the run on at all | the RUNBOOK staging (dry run first) |
| 8 | Tax registration and the `billing.tax_rates` row (`BILLING_TAX_*`) | No migration seeds a rate; registration status is not in the repo | the owner; then insert the row before setting the code |
| 9 | Whether `20.219.18.84` is a static/reserved address (`EDGE_RESERVED_IP`) | Provider-side | Azure portal |
| 10 | Which provider holds the off-box backups, whether the bucket exists, whether its credential is write-only | Not in the repo; and the RUNBOOK's restore drill needs the same pair to read | the provider's console; a real `make edge-backup` and the restore drill |
| 11 | Whether the Google Workspace app password exists and works; whether `info@opsgrids.com` and `zionna@opsgrids.com` exist and accept mail (the two addresses are from the owner's instruction, not the repo) | Provider-side | the SMTP login probe (checklist), then the first real alert or digest |
| 12 | `mc admin user add` with two empty strings (empty `DOCUMENT_S3_ORPHAN_*`); MinIO's own key-length limits; MinIO credential rotation | External binary behaviour; no RUNBOOK procedure | set both orphan values at the first deploy; a rotation procedure is a decision (E) |
| 13 | docker compose's profile-name parsing on the box's own version | Observed only on the audit machine's compose v2.35.1 | none needed if you write the list with no spaces |
| 14 | Which ref to deploy | `193b529` and earlier have both preflight defects and reject the two new keys; `ce35c77` (the memory-headroom merge) fixes them; integration `5320848` also merges io-outside-txn and is the final release. io-outside-txn needs no env key | deploy a ref for which `git merge-base --is-ancestor 5320848 <ref>` succeeds |
| 15 | The staff-MFA enrolment step | admin-gateway (where operators sign in) is not deployed on this box | whether any staff sign-in is intended here |
| 16 | Whether the box's Python/pydantic behave exactly like the audit machine's | The images use Python 3.12 (`uv:python3.12-bookworm-slim`); the offline SMTP experiment ran on 3.12.6 and 3.13.2 and the parser probes on the lock's pydantic 2.13.4 | none needed; noted for completeness |
| 17 | Merging `slice/env-audit` into integration | It conflicts with `release/backlog-complete` in `infrastructure/edge/.env.example` only (both edited it); docs do not conflict | the owner: take the audited `.env.example` (all 66 uncommented `KEY=` lines are identical to integration's; only comments change), then re-run `make secrets` |
| 18 | The box's real memory use | The 4581 is priced from images measured on a laptop (arm64); the RUNBOOK gives a measurement rule (section 6.3) that only the box can run | the read-only `docker stats` and `vmstat 5 3` commands in checklist step 2.7, then the rule in step 6.1 |
| 19 | Notification's STARTTLS does not verify the server certificate | A code behaviour (no TLS context is passed), not an env value (section 11, item 15) | a code change (`ssl.create_default_context()`) and a deploy; until then treat the SMTP app password as exposed to an on-path attacker on that path |
| 20 | RUNBOOK statements this audit found wrong | It attributes the removed-domain window to `STORE_SLUG_CACHE_SECONDS` (INT:rb:1173), says `document` reuses MinIO's root credential (INT:rb:201) and that the orphan pair may be empty (INT:rb:1417) | a RUNBOOK edit, outside this branch |
| 21 | `.gitignore` on `origin/main` has no `*.pem` / `*.key` rule | The rule is on integration and on this branch (`cdd1e14c`) but not on main, so a main-based checkout shows a private-key file as untracked, not ignored | bring `cdd1e14c` to main |
| 22 | Whether the box's `awk` and `bash` behave like the audit machine's | The checklist snippets were run under macOS awk and bash 3.2 and 5 only | run the read-only steps 2.2-2.5b on the box first: a syntax error there is loud |

## 13. The preflight run: placeholders and fake values only

**Setup.** Two scratch trees outside the repository: the final release (integration `5320848`, which contains memory-headroom `0ff2954` and io-outside-txn `ed15f27`) and a plain checkout of
`PRE` = `193b529` (integration before the memory-headroom merge). Preflight was run from each with the audited `.env.example` beside it and a **throwaway `.env`** written
into the scratch tree only and deleted afterwards (no real `.env` was created or modified anywhere).

- Every secret in every run is random hex made for that run and discarded; the provider values are fake (`rzp_test_FAKE...`, a
  made-up phone number id, a quoted fake app password with spaces, a made-up backup key pair, `https://s3.fake.invalid`); the
  signing key is a throwaway Ed25519 file with mode 600. **Each run was leak-checked: no throwaway value appeared in its output.**
- The environment was scrubbed (`env -i`); `EDGE_MEMINFO_FILE` pointed at a file saying `MemTotal: 3954688 kB` (3862 MiB, the box's figure);
  `EDGE_STATE_DIR` pointed at a path that does not exist, which skips preflight's one container-starting call
  (INT:pf:586).
- The Docker calls preflight made were read-only: `docker info` (INT:pf:346),
  `docker ps` (INT:mb:166), `docker compose config --services` and `--profiles`, and
  `docker compose ps --status running postgres` (nothing runs under project `opsgrids-edge` here, so the database checks were skipped and
  said so). No container was created and no Docker state changed.
- **Outbound IP networking was denied at the operating-system level for every run** (`sandbox-exec` with
  `(deny network-outbound (remote ip))`; Docker's local unix socket still worked). A control in the same sandbox confirmed a socket to `1.1.1.1:443` fails with
  `PermissionError`.
- Static check, on the final tree: neither `preflight.sh` nor the four files it sources or runs (`lib/active-profiles.sh`, `lib/secret-strength.sh`,
  `lib/memory-budget.sh`, `lib/memory_budget_totals.py`) invokes `curl`, `wget`, `nc`, `ssh`, `aws`, `dig` or any other network tool at a command
  position outside comments (the one word the scan matched was prose in a docstring), and the Python helper imports only `argparse`, `json`, `sys`,
  `pathlib` and `typing`.

### 13.1 Runs

Flagged rows name keys, never values. "Laptop rows" = `only NG free` (disk) and `inodes 100% used` (BSD `df` column): they belong to this machine, not to the values.

| Run | What changed | Exit | Flagged |
|---|---|---|---|
| **R0** | the example verbatim (empty secrets, the committed `ops@example.com`); re-run in the fix pass on the corrected example | 1 | **FAIL** `missing or empty in .env:` the 12 required secrets (`POSTGRES_PASSWORD OPSGRIDS_DB_PASSWORD INTERNAL_SERVICE_TOKEN ADMIN_GATEWAY_HOP_KEY PII_ENCRYPTION_KEY RABBITMQ_PASSWORD RAZORPAY_WEBHOOK_SECRET ENGAGE_UNSUBSCRIBE_SECRET MINIO_ROOT_USER MINIO_ROOT_PASSWORD DOCUMENT_S3_ACCESS_KEY DOCUMENT_S3_SECRET_KEY`); **FAIL** placeholder `ACME_EMAIL` (the committed `ops@example.com`); FAIL no signing key at the default path; laptop rows. warn: no off-box backup, `EMAIL_TRANSPORT=log`, billing run OFF, Razorpay empty, no ops-monitor alert address, route/upstream notes. memory ok 2217M (core only). A verbatim copy can never deploy, and (run Q9) the services would not start on it either |
| **R1** | **structurally valid FAKE values everywhere**, all ten profiles, ceiling 4581 | 1 | 15 `ok` rows incl. no placeholders, secrets long enough and distinct, off-box backup credentials present, signing key mode 600, payment provider keys present, email transport smtp, ops-monitor alerts email, engage base. **Only the two laptop rows FAIL.** warn: memory `4581M against 3862M MemTotal: 719M over MemTotal (118.6%)` (accepted by the ceiling), billing OFF, state dir absent, Postgres not running |
| R2 | R1 with `.env` mode 644 | 1 | FAIL `.env is mode 644` |
| R3a | `POSTGRES_PASSWORD` line deleted | 1 | FAIL `missing or empty in .env: POSTGRES_PASSWORD` |
| R3b | `INTERNAL_SERVICE_TOKEN=` empty | 1 | FAIL `missing or empty in .env: INTERNAL_SERVICE_TOKEN` |
| R3c | `RABBITMQ_PASSWORD=replace-me-x` | 1 | FAIL placeholder: `RABBITMQ_PASSWORD` |
| R3d | `ENGAGE_UNSUBSCRIBE_SECRET=CHANGE_ME_...` | 1 | FAIL placeholder: `ENGAGE_UNSUBSCRIBE_SECRET` |
| R3e | `ADMIN_GATEWAY_HOP_KEY` 8 bytes | 1 | FAIL `is 8 bytes: it must be at least 32` |
| R3f | hop key equal to the internal token | 1 | FAIL `must differ from INTERNAL_SERVICE_TOKEN` |
| R3g | `PII_ENCRYPTION_KEY` 31 bytes | 1 | FAIL `is 31 bytes: it must be at least 32` |
| R3h | `MINIO_ROOT_PASSWORD` 7 characters | 1 | FAIL `shorter than MinIO's 8-character minimum` |
| R3i | `DOCUMENT_S3_ORPHAN_*` both empty | 1 | **no FAIL from them** (laptop rows only): preflight allows it although `minio-init` would run `mc admin user add` with them |
| R3j | `PUBLIC_ASSET_BASE_URL` on another host | 1 | FAIL `must be exactly https://$EDGE_HOST/$S3_PUBLIC_BUCKET` |
| R3k | only one of the `BACKUP_S3_*` pair set | 1 | FAIL `one of ... is set and the other is empty` |
| R3l | both `BACKUP_S3_*` empty | 1 | warn only: `NO OFF-BOX BACKUP` |
| R3m | signing key path missing | 1 | FAIL `no signing key at ...` |
| R3n | `ENGAGE_PUBLIC_API_BASE=http://localhost:8080/api/v1` | 1 | FAIL `must be https` |
| R3o | `ACME_EMAIL=ops@example.com` | 1 | FAIL placeholder: `ACME_EMAIL` |
| R3p | `EDGE_RESERVED_IP=203.0.113.9` | 1 | warn only: RFC 5737 documentation address |
| R4a | ten profiles, **no** ceiling | 1 | FAIL memory `4581M ... 719M over MemTotal (118.6%) — over the hard budget, and no EDGE_BUDGET_ACCEPTED_MB is set` |
| R4b | ceiling 4581 | 1* | memory becomes a **warn** (accepted); *exit 1 is the laptop rows |
| R4c | ceiling 4580 | 1 | FAIL `over the hard budget AND over the owner-accepted ceiling (... 1M short)` |
| R4d | ceiling 4600 | 1* | warn + info `19M above this deploy's total ... set it to 4581` |
| R4e | ceiling `4581MB` | 1 | FAIL `must be a whole number of megabytes`; memory FAIL `the ... in .env was rejected` |
| R4f | ceiling `45810` | 1 | FAIL `is above the 125% cap (4827M of 3862M MemTotal)` |
| R4g | ceiling `"4,581"` | 1 | FAIL `must be a whole number of megabytes` |
| R4h | `EDGE_BUDGET_ACCEPTED_MB` line absent | 1 | `every key ... is set` still ok; memory FAIL (no ceiling) |
| R4i | `EDGE_PROFILES` line absent | 1* | `every key ... is set` ok |
| R4j | `COMPOSE_PROFILES=*` | 1* | `COMPOSE_PROFILES=* expanded to: crm,finance,hrms,inventory,itsm,marketing,projects,pulseboard,storefront,support`; priced 4581M |
| R4k | `COMPOSE_PROFILES=` empty | 1* | priced 2217M (core only), ok |
| R4l | `EDGE_PROFILES=storefront` over the ten | 1* | `COMPOSE_PROFILES overridden by EDGE_PROFILES: storefront`; 2473M |
| R4m | `EDGE_PROFILES=inventory, storefront` unquoted | 1 | FAIL named: `EDGE_PROFILES in .env has an unquoted value containing whitespace`; `.env` not sourced |
| R4n | `EDGE_PROFILES="inventory, storefront"` quoted | 1* | accepted; 2571M |
| R4o | `COMPOSE_PROFILES="hrms;crm"` | 1 | **FAIL** `profile 'hrms;crm' ... is not defined in compose.prod.yml` (before `0ff2954`: no error, priced as core only) |
| R5a | `OPS_ALERT_EMAIL=info@opsgrids.com,zionna@opsgrids.com` | 1* | `ok ops-monitor alerts email: info@opsgrids.com,zionna@opsgrids.com` |
| R5b | `OPS_ALERT_EMAIL=... and ...` unquoted | 1 | FAIL named (whitespace) |
| R5c | same, quoted | 1* | passes; prints the sentence (only the first address would receive mail) |
| R5d | `OPS_ALERT_EMAIL` empty | 1* | warn `OPS_ALERT_EMAIL/SMTP_HOST not fully set` |
| R5e | `SMTP_PASSWORD` with spaces unquoted | 1 | FAIL named (whitespace) |
| R6a | billing run unset | 1* | warn `BILLING_RUN_ENABLED is unset: the billing run is OFF` |
| R6b | enabled, no cutover | 1 | FAIL `BILLING_RUN_ENABLED=true but BILLING_RUN_NOT_BEFORE is empty` |
| R6c | enabled + naive cutover | 1 | FAIL `must be YYYY-MM-DDTHH:MM:SS±HH:MM` |
| R6d | enabled + valid cutover, **BSD date (laptop)** | 1 | FAIL `is not a real instant`: laptop-only (needs GNU `date`) |
| R6d2 | same with a GNU `date` shim (what the box has) | 1* | warn `is in the future: the run rolls periods but nothing is invoiced until then` |
| R6e | `BILLING_RUN_ENABLED=1` | 1* | warn `is 1: the billing run is OFF` (but the container would parse it as true) |
| R6f | `RUN_SWEEPS_IN_API=false` | 1* | warn: the three sweeps are OFF |
| R6g-R6j | cutover `2026-09-31...`, `+0530`, past, fractional seconds (GNU date) | 1 / 1 / 1* / 1* | FAIL `not a real instant`; FAIL `must be ...`; ok; warn future |
| **R7a** | **`PRE` = `193b529`** (before the memory-headroom merge) with the audited example | 1 | FAIL `missing or empty in .env: EDGE_PROFILES EDGE_BUDGET_ACCEPTED_MB`; FAIL `the Caddyfile serves  but TLS_PLATFORM_HOSTS does not list it` (**the known defect**); memory check skipped (no `/proc/meminfo` seam on that ref) |
| **R7b** | `PRE` = `193b529`, `OPS_ALERT_EMAIL=... and ...` unquoted | **127** | `./.env: line 64: and: command not found`, no footer (**the known exit-127 gotcha**) |
| **R7c** | final release, valid values | 1* | the Caddyfile row is **gone** (fixed by the memory-headroom merge) |
| R8a | `OPS_ALERT_EMAIL=a;b` unquoted | 1 | FAIL named: `unquoted value containing a shell metacharacter`; `.env` not sourced |
| R8b | `SMTP_HOST="x" extra` | 1 | FAIL named: `text after its closing quote` |
| R8c | `SMTP_USERNAME=$(id)` unquoted | 1 | FAIL named: `shell metacharacter` (the command was not run: the file is never sourced) |
| R8d | `COMPOSE_PROFILES=hrm` | 1 | FAIL `profile 'hrm' ... is not defined in compose.prod.yml, so it enables nothing`; priced 2217M meanwhile |
| R8e | `EDGE_PROFILES=hrm` | 1 | the same FAIL |
| R8f | ceiling `04581` | 1 | FAIL `must be a whole number of megabytes (no sign, unit or leading zero)` |
| R8g | ceiling of 20 digits | 1 | FAIL `is above the 125% cap` (the length guard, not a wrapped integer) |
| R8h | `RUN_SWEEPS_IN_API=True` | 1* | `ok in-API sweeps enabled` |
| R8i | `RUN_SWEEPS_IN_API=maybe` | 1* | warn: the sweeps are OFF (the API containers would refuse the value at startup) |
| R8j | `EDGE_HOST=edge.opsgrids.com # the box` | 1* | `ok PUBLIC_ASSET_BASE_URL matches` (an inline comment is dropped) |
| R8k | an `export RUN_SWEEPS_IN_API=true` line | 1* | accepted by preflight (it `source`s the file); remote.sh's `dotenv_get` would NOT see an `export` line (run Q5, section 11 item 5) |
| R8l | the whole `.env` with CRLF endings | 1 | FAIL named: `Windows (CRLF) line endings`; `.env` not sourced |
| R8m | `EDGE_PROFILES=` then `EDGE_PROFILES=storefront` | 1* | the later line wins: `overridden by EDGE_PROFILES: storefront`, 2473M |

`1*` = exit 1 only because of the two laptop rows; the rows listed are the ones the run was about.

### 13.2 What this proves, and what it does not

> **Preflight validates the presence and the shape of the environment. It does not contact any provider and does not verify any credential.**

Proved by experiment, not asserted:

1. **Structurally valid fake values pass** (R1): a fake Razorpay id and secret, a fake Meta phone number id, access token and app
   secret, a fake quoted SMTP app password, a fake backup key pair and endpoint all satisfy every presence/shape/length row. A value
   is "valid" to preflight if it is present, is not `replace-me*`, `CHANGE_ME*`, `dev-only-*`, `ci-only-*` or `*example.com*`, and (for
   the three platform secrets) is at least 32 bytes.
2. **Too short, placeholder, missing or empty values fail** (R3a-R3h, R3o), each with the row that names the key.
3. **It did this with all outbound IP networking denied by the operating system**, so it could not have contacted a provider even if a
   row tried to; and a static scan found no network tool in it.

So a green `ok` on a placeholder or fake value **never** means the variable is resolved. **Production credential verified** is a separate state that
preflight cannot reach; the checklist gives, for each provider credential, the one safe live call that would, and marks it as needing the owner.

### 13.3 Rows that need the box

These cannot be settled on a laptop and are the only failures in R1: **free disk** (< 5 GB fails), **inodes** (>= 90% fails; the BSD `df` column misreads), the
**GNU `date`** real-instant check for the billing cutover, **the docker daemon and the live container set** (the memory total is priced from what
is running), **the Postgres catalogue/activation checks** (they run only when Postgres is up: a sold product with no profile, a live
activation whose profile is missing, and the profile advisory), **the signing key file and its mode** (the box's real path), the
**`ops-monitor` state directory ownership** (a throwaway container `id -u` call), **the buyer bundle** (`warn no buyer bundle at /srv/opsgrids/web/current`, present on every scratch run), and **`/proc/meminfo`**.

### 13.4 The repo's own tests

`tests/infra` on the same scratch tree with the audited `.env.example` in place, at the last run (2026-09-24, integration `5320848`, re-run in the fix pass with the corrected example, whose 66 `KEY=` lines are now identical to integration's): **381 collected: 381 passed, 0 failed**.
That includes `test_preflight_memory.py`, `test_mfa_reseal.py` and `test_planned_headroom.py` (whose RUNBOOK ceiling example failed on the earlier snapshot and was fixed by
`0ff2954`), and io-outside-txn's own `test_io_outside_transaction.py` and `test_sweeps_run_somewhere.py`. Nothing here depends on `.env`. One history point, from my earlier scratch merge of
the slice at `d0b3dab`: its sweeps test knew only two shapes for a sweep (its own `<service>-cron` container, or folded into the worker), and memory-headroom had added a third (folded into the API
container, `RUN_SWEEPS_IN_API`), so `test_every_sweep_runs_in_a_container` failed for document, operations and storefront on that scratch tree, and the transaction guard named three payment files
where my scratch merge had taken integration's side. The slice's later commit `ed15f27` taught the test the third shape (it checks `RUN_SWEEPS_IN_API`), and integration `5320848` passes both.

### 13.6 Fix-pass experiments (Q runs)

Run on 2026-09-24 in throwaway trees outside the repository (the same setup as section 13: integration `5320848`'s `infrastructure/edge`, throwaway `.env`, random fake values, scrubbed environment, outbound IP networking denied at the OS level; the Python probes used the
real `Settings` classes of `5320848` from an empty directory on Python 3.12.3 and the lock's pydantic). No real `.env` was created, read or modified. A green preflight here is structural only, as everywhere in this document.

| Run | What was done | Result |
|---|---|---|
| Q1 | the value `"ab$(touch MARKER)cd"` (command substitution inside double quotes) set on the `SMTP_PASSWORD` key, real preflight | no pre-check FAIL; the marker file **was created**: the command ran |
| Q2 | the values `"ab$cd"` and unquoted `ab$cd` on the `SMTP_PASSWORD` key | `./.env: line N: cd: unbound variable`, exit 1, no `FAILED` footer |
| Q3 | the value `'ab$cd'` (single quotes) on the `SMTP_PASSWORD` key | accepted |
| Q4 | a backtick command inside double quotes (``"ab`touch MARKER`cd"``) | the marker file **was created** |
| Q5 | remote.sh's real `dotenv_get` (extracted verbatim) on `EDGE_PROFILES=storefront`, `export EDGE_PROFILES=storefront`, an indented line, a quoted value, a value with an inline comment | `storefront`, **empty**, **empty**, `storefront`, `storefront` |
| Q5b | docker compose v2.35.1 `config --services` on the ten-profile `.env` with `COMPOSE_PROFILES` unset vs exported empty | 47 services vs 21 |
| Q6 | cutover `2026-10-01T09:00:00+24:00` and `-24:00`: GNU `date` 9.5 vs `datetime.fromisoformat` | `date` accepts both (prints an epoch); Python refuses ("offset must be a timedelta strictly between -timedelta(hours=24) and timedelta(hours=24)") |
| Q7 | an unbalanced double quote, an unbalanced single quote, a bare quote inside a value | exit 2, `unexpected EOF while looking for matching`, no `FAILED` footer |
| Q8 | docker compose on `A="ab$cd"`, `B='ab$cd'`, `C=ab#cd`, `D=ab #cd`, `E="ab\$cd"` | `ab` plus the warning `The "cd" variable is not set`; literal `ab$cd`; `ab#cd`; `ab`; literal `ab$cd` |
| Q9 | the real `auth`, `notification` and `payment` `Settings` with `ENVIRONMENT=production`: `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY`, `ADMIN_GATEWAY_HOP_KEY`, `ENGAGE_UNSUBSCRIBE_SECRET`, `RAZORPAY_WEBHOOK_SECRET` empty vs the `CHANGE_ME_generate_...` placeholders | empty: all three **refuse** to start; placeholders: all three **start**, and notification's `unsubscribe_key()` returns bytes (Engage link signing ON) |
| Q10 | `TLS_PLATFORM_HOSTS` with `edge.opsgrids.com` and `api.opsgrids.com` removed | preflight's Caddyfile row passes (no `the Caddyfile serves` FAIL) |
| Q11 | `ENGAGE_PUBLIC_API_BASE` spellings through preflight and the notification send gate (`_check_public_base`) | see the table below |
| Q12 | `smtplib.SMTP.starttls()` with no context, Python 3.12.3 | it calls `ssl._create_stdlib_context()`: `verify_mode` 0 (CERT_NONE), `check_hostname` False |
| Q13 | the checklist's `fill_if_unset` helper (14 cases: absent key, repeated key, empty, quoted-empty, four placeholders, real value kept, no trailing newline, prefix-sharing names, missing `.env`, other lines with `\|`, `&`, `=`, output leak check) under bash 3.2 and 5, and the other new snippets (17 cases) | all pass |
| Q14 | preflight on the corrected example: R0 (the example verbatim) and R1 (structurally valid FAKE values, all ten profiles, ceiling 4581) | R0 as in section 13.1; R1 identical to before (15 `ok` rows, only the two laptop rows FAIL, memory `4581M ... (118.6%)` accepted by the ceiling) |

Q11, by spelling (preflight is the real script; the gate is the real service function):

| `ENGAGE_PUBLIC_API_BASE` | preflight | send gate |
|---|---|---|
| `https://app.opsgrids.com/api/v1` | ok | accepted |
| `https://app.opsgrids.com` (no path) | ok | accepted (but the links fall outside the public `/api/v1/engage/unsubscribe/` prefix) |
| `https://Localhost/api/v1` | ok | **refused** |
| `HTTPS://app.opsgrids.com/api/v1` | **FAIL** (`must be https`) | accepted |
| `https://[::1]/api/v1`, `https://0.0.0.0/api/v1` | ok | accepted |
| `http://app.opsgrids.com/api/v1` | **FAIL** | **refused** |
| `https://app.opsgrids.com/api/v1/` | ok | accepted (trailing slash stripped) |

### 13.5 To reproduce

```bash
# scratch only; never in the repository, never with a real .env
d=$(mktemp -d); git archive origin/release/backlog-complete infrastructure/edge | tar -x -C "$d"
cp infrastructure/edge/.env.example "$d/infrastructure/edge/.env.example"
# write "$d/infrastructure/edge/.env" from the example with throwaway values (chmod 600), then:
printf 'MemTotal:        3954688 kB\n' > "$d/meminfo"
cd "$d/infrastructure/edge" && env -i PATH="$PATH" HOME="$HOME" EDGE_STATE_DIR="$d/no-state" EDGE_MEMINFO_FILE="$d/meminfo" \
  sandbox-exec -p '(version 1)(allow default)(deny network-outbound (remote ip))' bash ./preflight.sh
```
