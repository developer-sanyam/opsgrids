# Manual deployment checklist: environment, secrets and the deploy

For the **manual** deployment of the final release to the production box. The owner ssh's to the box as `azureuser` for
admin steps and deploys from a laptop clone with

```bash
make deploy HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com REF=<ref>
```

(GitHub Actions is billing-blocked, so nothing here relies on a workflow.) Every variable, its exact syntax and its source is in
[`ENV_SOURCE_MATRIX.md`](ENV_SOURCE_MATRIX.md); this file is the order to do things in. Citations use the same branch
abbreviations and are defined in that file's section 1 (`INT` = `release/backlog-complete` at `5320848`, the final release, which contains memory-headroom and io-outside-txn; `PRE` = `193b529`, integration before the memory-headroom merge).

## How to read this checklist

- **Where.** `[laptop]` = your clone of the repo. `[box: azureuser]` = an ssh session as `azureuser`. `[box: deploy]` = as the `deploy` user, which owns
  `/srv/opsgrids` and the box's `.env` (INT:rb:133-139): from `azureuser`, `sudo -iu deploy`
  (if `azureuser` has sudo, as Azure's default user does: provider-side, not verified in repo), or `ssh deploy@20.219.18.84`, which is how `make deploy` itself connects.
- **Status tags, kept strictly apart.**
  **`[STRUCTURAL]`** = a presence/shape check passed: preflight, a shape grep. It proves a value has the right *form*; a placeholder or fake value
  can pass it, and it never means a credential works.
  **`[READ-ONLY]`** = a look at the live box (a listing, a count, a query) that changes nothing and proves nothing about any credential.
  **`[LIVE: owner]`** = a real call to a provider or to the live box that would prove a credential or a behaviour in production. Each is safe (it sends no message and
  changes nothing) but **only the owner should run it**, and nothing in this file has been run against production.
- **Never** paste a secret into chat, a ticket, a screenshot or a shared terminal. **Never** `cat`, `less`, `echo` or `diff` a `.env`. Values go from a command
  straight into the file (the helpers below) or from the provider's dashboard straight into the file.
- Do not `git add` anything in the repo root that you did not write. At audit time an untracked file named `py-microservices-vm_key.pem` sat in the main checkout's root,
  **not gitignored**; never `git add -A` there.
- Run a `Do` block exactly as written. Where a step says **[OWNER]** it is a decision only the owner can make; write the answer in the blank.

## The order, in twelve lines

1. Decide the **ref** to deploy (part 0). 2. Take the **read-only inventory** of the box (part 2). 3. **Back up** `.env` (3.1). 4. **Append** the missing keys (3.2).
5. **Generate** the ten secrets (3.3). 6. **Obtain** the provider values (part 4). 7. **Configure** the plain values (part 5). 8. Make the **owner decisions**, memory first (part 6).
9. Run **preflight** until it prints `preflight: ok` (part 7). 10. **Deploy** in a quiet window (part 8). 11. **Verify** (part 9). 12. Know your **rollback** (part 10).

## Part 0. Decisions before you start (laptop)

- [ ] **0.1 Choose the ref.** **[OWNER]** The two preflight fixes (the Caddyfile catch-all row; the unquoted-whitespace exit 127), the new keys `EDGE_PROFILES` / `EDGE_BUDGET_ACCEPTED_MB` and the hardened
  `.env` reading all arrived with the memory-headroom merge (`ce35c77`); `PRE` (`193b529`) and anything older still fails preflight on every run (matrix section 11, item 2; run R7a). Integration `5320848` is the final release: it also merges
  `io-outside-txn`, which adds no key. So `REF` must be `origin/release/backlog-complete` at `5320848` **or later**: on your laptop `git merge-base --is-ancestor 5320848 <ref> && echo ok` must print `ok`. Do **not** deploy `origin/slice/env-audit`: it is documentation
  plus `.env.example`, based on an older integration head. `make deploy` defaults to `origin/main`, so **always pass `REF=`** (INT:mk:537). The ref must exist **on the box's remote**:
  the box runs `git fetch --prune --tags origin` and `git checkout --detach <ref>` (INT:sd:64-65), so push it first, and the box's checkout must
  be clean or the deploy refuses.
  REF chosen: `______________________`
- [ ] **0.2 Load your ssh key.** `make deploy` uses `ssh -o BatchMode=yes` (INT:sd:52), so it can never prompt for a password or passphrase: `ssh-add <your key>` first.
  The key file is not in the repo; keep it that way.
- [ ] **0.3 The decisions this file will ask for, so you can think about them now** (each has its own step):
  memory (`EDGE_BUDGET_ACCEPTED_MB`, step 6.1); which products run (`COMPOSE_PROFILES`, 6.2); the billing run stays OFF for this deploy (6.4); accepting the one-way MFA reseal (6.5); whether provider
  webhooks may be used (6.6); tax registration (6.7).
- [ ] **0.4 Pick a quiet window.** This release lands ~+540 MB of containers on a box already swapping; there is no automatic rollback (part 10).
- [ ] **0.5 Off-box copy of `PII_ENCRYPTION_KEY` exists?** **[OWNER]** Losing it loses every sealed PII value and every sealed MFA secret. This checklist never prints it; confirm your own off-box record. Yes / No: `_____`

## Part 1. Two sessions

- [ ] **1.1** `[laptop]` a terminal for `make ...` commands. `[box: deploy]` a second terminal, open before the deploy, for `vmstat 5` and log greps.

## Part 2. Read-only inventory of the box (`[READ-ONLY]`: names and counts, never values)

All of this runs `[box: deploy]` in `/srv/opsgrids/repo/infrastructure/edge`. Nothing changes.

- [ ] **2.1 Which ref is on the box, and is the checkout clean?**

  ```bash
  cd /srv/opsgrids/repo && git rev-parse --short HEAD && git status --short | head
  cd infrastructure/edge
  ```

  Expect a clean status (the deploy refuses a checkout with local modifications: INT:sd:59). The RUNBOOK says the production ref is `47143a0` (not re-verified).
  Box ref: `_______`. Note that the box's `make edge-preflight` runs **that** checkout's preflight until a deploy moves it (step 7.1 explains how to preflight the target ref instead).
- [ ] **2.2 `.env` exists, is yours, and is mode 600.**

  ```bash
  stat -c '%U %a %n' .env      # expect: deploy 600 .env
  ```

  Preflight fails the deploy if the mode is not 600 or 400 (INT:pf:30-32). Two more read-only shape checks on the raw file, counts and names only (preflight refuses the file, before reading it, if the first is nonzero; a repeated key is read differently by remote.sh in two places):

  ```bash
  grep -c $'\r' .env                                                  # 0: a CRLF file is a preflight FAIL
  sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env | sort | uniq -d       # prints nothing: no key defined twice
  ```
- [ ] **2.3 Set the ref you are about to deploy and fetch it** (so the next two commands read that release's example):

  ```bash
  REF=origin/release/backlog-complete       # <- your REF from step 0.1 (5320848 or later)
  git fetch --prune origin
  ```
- [ ] **2.4 Which required keys are missing from `.env`?** (names only; the same rule preflight uses, INT:pf:37)

  ```bash
  comm -23 \
    <(git show "$REF:infrastructure/edge/.env.example" | sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' | sort -u) \
    <(sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env | sort -u)
  ```

  Against the production ref's 44 keys, **22 keys are new and 20 of them must be added** (`EDGE_PROFILES` and `EDGE_BUDGET_ACCEPTED_MB` may stay absent). Count of missing lines: `_____`
- [ ] **2.5 Which keys are present but empty (and not allowed to be) or still placeholders?** (names only)

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

  (`$allowed` should hold 26 names on this release.) A `PLACEHOLDER` line for `ACME_EMAIL` is the committed `ops@example.com`.
- [ ] **2.6 Count legacy plaintext MFA rows** (read-only; decides whether the deploy's reseal will run, section 7 of the matrix):

  ```bash
  docker compose -f compose.prod.yml --env-file .env exec -T postgres psql -U opsgrids -d opsgrids -Atc \
    "select count(*) from auth.mfa_factors where secret_enc is not null and encode(secret_enc,'escape') ~ '^[A-Z2-7]{16,32}\$'"
  ```

  Count: `_____` (a `relation ... does not exist` error means a box with no auth schema yet). It is the statement the deploy itself runs (INT:mr:41).
- [ ] **2.7 Read the box's memory, the RUNBOOK's way** (read-only; do it **before** the deploy, at a quiet hour, on the current production ref; you need the numbers for step 6.1) (INT:rb:1298-1318):

  ```bash
  free -m
  docker stats --no-stream --format '{{.MemUsage}}' | awk '{ u=$1; v=u+0;
    if (u ~ /GiB$/) v*=1024; else if (u ~ /KiB$/) v/=1024; else if (u !~ /MiB$/) v/=1048576;
    s+=v } END { printf "%.0f MiB\n", s }'
  vmstat 5 3        # the si / so columns: swap-in / swap-out per second
  ```

  MemTotal `_____` MB, swap used `_____` MB, **measured sum** `_____` MiB, `vmstat` si/so `_____ / _____`. The RUNBOOK says the box is priced today at 4044 MB for its 40 containers (104.7%; not re-verified), 3862 MB MemTotal and ~2 GB of swap in use.

## Part 3. Generate the secrets on the box (category A)

**Generate here, never anywhere else; never from development, another environment or a chat window.** The helper below writes through a mode-600 temp file, prints only the key
name, and **never overwrites a real value** (it acts only on an empty value or a `CHANGE_ME*` / `replace-me*` placeholder). It was exercised in a scratch directory before
this file was written: a real value was kept, a placeholder was generated, the mode stayed 600 and no temp file was left.

- [ ] **3.1 Back up `.env` before editing it** (the copy holds every secret: same mode, same directory, deleted in part 11):

  ```bash
  ( umask 077; cp -p .env ".env.bak.$(date -u +%Y%m%dT%H%M%SZ)" ); ls -l .env.bak.* | awk '{print $1, $NF}'
  ```

  **Proves:** a 600-mode copy exists. `[STRUCTURAL]`
- [ ] **3.2 Append the missing keys without overwriting anything** (each line comes from the example, so it is a placeholder or an empty value, never a secret):

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

  **Proves:** re-running step 2.4 prints nothing (or only the two optional keys). `[STRUCTURAL]` Preflight will still refuse the placeholders: that is the point.
- [ ] **3.3 Define the helper, then generate.** Paste the function, then the calls. **Generate only these ten keys. Never run it on `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD`,
  `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY` or `RABBITMQ_PASSWORD` of this box: they exist (category D) and regenerating them breaks logins, tokens or every sealed value.**

  ```bash
  fill_if_unset() {   # usage: fill_if_unset KEY BYTES   (never overwrites a real value; never prints one)
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
  fill_if_unset ADMIN_GATEWAY_HOP_KEY          32   # a separate openssl call from INTERNAL_SERVICE_TOKEN's
  fill_if_unset ENGAGE_UNSUBSCRIBE_SECRET      32
  fill_if_unset RAZORPAY_WEBHOOK_SECRET        32   # paste the same value into Razorpay only once the webhook is reachable (part 4.3)
  fill_if_unset MINIO_ROOT_USER                16
  fill_if_unset MINIO_ROOT_PASSWORD            32
  fill_if_unset DOCUMENT_S3_ACCESS_KEY         16
  fill_if_unset DOCUMENT_S3_SECRET_KEY         32
  fill_if_unset DOCUMENT_S3_ORPHAN_ACCESS_KEY  16   # set BOTH orphan values now: minio-init adds both users unconditionally
  fill_if_unset DOCUMENT_S3_ORPHAN_SECRET_KEY  32
  # only if you will enable WhatsApp webhooks (matrix section 9): fill_if_unset WHATSAPP_VERIFY_TOKEN 32
  ```

  Why hex: `openssl rand -hex N` is N random bytes as 2N hex characters, which contain nothing `.env` needs quoted and satisfy every length rule (>= 32 bytes for the three platform secrets;
  MinIO's >= 8). The commands are exactly the ones in the matrix, section 6.5. **Proves:** each line prints `generated:` (or `kept:` if a real value was already there). `[STRUCTURAL]`
- [ ] **3.4 Check the shape without printing:**

  ```bash
  shape() { if grep -q "^$1=$2\$" .env; then echo "  ok   $1"; else echo "  BAD  $1"; fi; }
  for k in ADMIN_GATEWAY_HOP_KEY ENGAGE_UNSUBSCRIBE_SECRET RAZORPAY_WEBHOOK_SECRET MINIO_ROOT_PASSWORD DOCUMENT_S3_SECRET_KEY DOCUMENT_S3_ORPHAN_SECRET_KEY; do shape "$k" '[0-9a-f]\{64\}'; done
  for k in MINIO_ROOT_USER DOCUMENT_S3_ACCESS_KEY DOCUMENT_S3_ORPHAN_ACCESS_KEY; do shape "$k" '[0-9a-f]\{32\}'; done
  ```

  **Proves:** the value is 64 (or 32) lower-case hex characters: right form. `[STRUCTURAL]` It proves nothing about anyone having seen it: that is why steps 3.1-3.3 avoid printing.
- [ ] **3.5 Category D keys are untouched.** Nothing to do; this line exists so you notice if you did something. `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD`, `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY`,
  `RABBITMQ_PASSWORD`, the signing key file and `EDGE_HOST` already exist. If step 2.4/2.5 reported one of them missing, **stop**: that means this box is not what the repo describes
  (matrix section 12, item 1), and the answer is not to regenerate it.


## Part 4. Obtain the provider values (category B)

Paste each value **from the provider's page straight into `.env` on the box** with an editor (`[box: deploy]`, `nano .env` or `vi .env`), never through the shell history, chat or a ticket. Quote any value
that contains a space. After editing: `chmod 600 .env`, then re-run step 2.5 (names only). Nothing in this part has been run against a provider.

- [ ] **4.1 Google Workspace SMTP** (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `SMTP_USE_TLS`, `SMTP_USE_SSL`). **[OWNER]**
  *Where:* the Google Account that owns the `info@opsgrids.com` mailbox -> Security -> 2-Step Verification -> App passwords (repo text: INT:ex:143; provider-side, not verified in repo). Create one and copy it once.
  *Set:* `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME=info@opsgrids.com`, `SMTP_PASSWORD="<the app password, quoted>"`, `SMTP_USE_TLS=true`, `SMTP_USE_SSL=false`, `EMAIL_FROM=info@opsgrids.com`
  (the last six are the appended defaults; only the password is new). One `SMTP_USE_TLS` serves both notification and ops-monitor (matrix section 6.4).
  `[STRUCTURAL]` preflight accepts the password's presence, not its correctness (a fake value passes: run R1).
  **`[LIVE: owner]` verify it, sending no mail** (`[box: deploy]`, in `infrastructure/edge`). This mirrors what ops-monitor does (`starttls` with a verified context, then login) and prints only the exception class:

  ```bash
  ( set -a; . ./.env; set +a
    python3 - <<'PY'
  import os, smtplib, ssl
  host, port = os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])
  user, pw = os.environ.get("SMTP_USERNAME", ""), os.environ.get("SMTP_PASSWORD", "")
  ctx = ssl.create_default_context()
  ssl_mode = os.environ.get("SMTP_USE_SSL", "false").lower() in ("1", "true", "yes", "on")
  tls_mode = os.environ.get("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes", "on")
  try:
      s = smtplib.SMTP_SSL(host, port, timeout=15, context=ctx) if ssl_mode else smtplib.SMTP(host, port, timeout=15)
      with s:
          if tls_mode and not ssl_mode:
              s.starttls(context=ctx)
          s.login(user, pw)
      print("SMTP login OK (no message sent)")
  except Exception as exc:  # the class and the SMTP code only, never the message
      print("SMTP login FAILED:", type(exc).__name__, getattr(exc, "smtp_code", ""))
  PY
  )
  ```

  `SMTP login OK` proves the mailbox accepts this login. It does **not** prove the mailbox may send as `EMAIL_FROM` or that `zionna@opsgrids.com` accepts mail: the first real alert or the daily 08:00 UTC digest does
  (ops-monitor `/status` then shows a non-null `last_send_at`; `make edge-status` prints it). Only after `OK`, set `EMAIL_TRANSPORT=smtp` (step 5); until then `log` is safe.
  Result: `_____`
- [ ] **4.2 Off-box backups** (`BACKUP_S3_BUCKET`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`; `BACKUP_S3_PREFIX` keeps its default). **[OWNER]**
  *Where:* the backup provider's console (the example names Backblaze B2 and Cloudflare R2 as options: INT:ex:228; **which provider is in use is not determinable from the repo**). Create the bucket, a lifecycle rule for
  retention (the box only prunes its local copies), and an application key scoped to that one bucket. Copy the endpoint URL, region, key id and secret.
  *Rule:* set both credentials or neither: exactly one is a preflight FAIL, both empty is only a warning and `backup.sh` then refuses to upload and exits non-zero every night
  (INT:bk:91-94).
  *Scope caution:* the example asks for a write-only key, but the RUNBOOK's restore drill downloads with the same pair, so a write-only key cannot run the drill. Decide which you want.
  `[STRUCTURAL]` preflight checks the pair is consistent. **`[LIVE: owner]` verify it after the deploy** with `make edge-backup HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com`: it really uploads (creates objects in the bucket) and prints
  `uploaded database + certificates to s3://...`; only then does it write the marker ops-monitor's `backup` check reads. Then do the RUNBOOK's restore drill once (INT:rb:1557). Result: `_____`
- [ ] **4.3 Razorpay** (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`; `RAZORPAY_WEBHOOK_SECRET` was generated in 3.3). **[OWNER]**
  **Important:** in this release the payment adapter's only outbound call is a stub that never opens a socket; the key id and secret are stored and used by nothing (INT:services/payment/app/providers/razorpay.py:103).
  So there is **nothing the platform can verify** about the key pair, and filling it in does not enable live charges. Recommended: leave both **empty** (payments inert; preflight prints a warning).
  If you store them anyway: Razorpay Dashboard -> Account & Settings -> API Keys (provider-side, not verified in repo); paste into `.env`. **`[LIVE: owner]`** the only proof is an authenticated request you make directly to Razorpay
  (provider-side; outside this repo); the platform's own behaviour will not change either way.
  **Webhook secret:** do **not** paste `RAZORPAY_WEBHOOK_SECRET` into Razorpay's dashboard (Webhooks) until step 9.7 shows the route is reachable; by static reading it is not (matrix section 9.1). Rotating it later means changing it in `.env` and in Razorpay together.
- [ ] **4.4 Meta / WhatsApp** (`WHATSAPP_TRANSPORT`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`). **[OWNER]**
  **Recommended for this deploy: change nothing.** `WHATSAPP_TRANSPORT=log` sends nothing, the other four stay empty (allowed).
  If and when you enable it: *where:* Meta for Developers -> your app -> Settings -> Basic (**App secret**); WhatsApp -> API Setup (**Phone number ID**); Business settings -> System users -> generate a token (**access token**)
  (all provider-side, not verified in repo). `WHATSAPP_VERIFY_TOKEN` you generate (`fill_if_unset WHATSAPP_VERIFY_TOKEN 32`) and paste into the app's webhook configuration. Set `WHATSAPP_TRANSPORT=whatsapp_cloud` **last**
  (it refuses to start without the phone number id and token: INT:services/notification/app/transports.py:404).
  **`[LIVE: owner]` verify the token and number id** with the same read-only Graph request the platform makes when a tenant connects (INT:services/notification/app/whatsapp_provider.py:62).
  The token is fed through `curl --config` on stdin so it never appears in the process list:

  ```bash
  ( set -a; . ./.env; set +a
    printf 'header = "Authorization: Bearer %s"\n' "$WHATSAPP_ACCESS_TOKEN" \
      | curl -sS -o /dev/null -w '%{http_code}\n' -K - "https://graph.facebook.com/v25.0/${WHATSAPP_PHONE_NUMBER_ID}?fields=id" )
  ```

  `200` proves Meta accepts the token for that number id (provider-side behaviour; not verified in repo); `401`/`400` means it does not. The app secret and verify token can only be proved by Meta's own handshake and a signed delivery, which need a
  reachable route (step 9.7). Result: `_____`
- [ ] **4.5 Sentry (`SENTRY_DSN`).** Leave empty: it is **inert** in this release (nothing reads it; no Sentry package is locked). Filling it in changes nothing.
- [ ] **4.6 Reserved IP (`EDGE_RESERVED_IP`).** **[OWNER]** Azure portal -> the VM's public IP address resource: is it **Static**? The deploy target `20.219.18.84` may or may not be a static reservation (not determinable from the repo). It is the
  address merchants put in DNS, so set it only to a real reserved address (`set_kv EDGE_RESERVED_IP <ip>` after step 5's helper). Empty is valid. Static? Yes / No: `_____`
- [ ] **4.7 The two alert mailboxes exist.** **[OWNER]** `info@opsgrids.com` and `zionna@opsgrids.com` (your mail admin). Preflight cannot check that they exist.

## Part 5. Configure the plain values (category C)

Everything here is non-secret. Define the helper once (it was exercised in a scratch directory: it edits in place through a mode-600 temp file, appends a missing key, and left no duplicate). **`VALUE` must not contain `|`, `&`, `\`
or a newline; quote it yourself if it needs quotes; do not use it for passwords (use an editor).**

```bash
set_kv() {   # usage: set_kv KEY VALUE   (non-secret values only)
  local k="$1" v="$2" tmp
  tmp=$(umask 077 && mktemp ./.env.XXXXXX)
  if grep -q "^${k}=" .env; then sed "s|^${k}=.*|${k}=${v}|" .env > "$tmp"
  else { cat .env; [ -z "$(tail -c1 .env)" ] || echo; printf '%s=%s\n' "$k" "$v"; } > "$tmp"; fi
  mv "$tmp" .env; chmod 600 .env; echo "set: $k"
}
```

- [ ] **5.1 The values that must change from what step 3.2 appended:**

  ```bash
  set_kv ACME_EMAIL info@opsgrids.com                                  # any inbox a human reads; the committed ops@example.com is refused
  set_kv OPS_ALERT_EMAIL info@opsgrids.com,zionna@opsgrids.com         # comma, NO spaces (an unquoted space is a named preflight FAIL; exit 127 on 193b529 and earlier)
  # only after step 4.1's probe printed OK:
  set_kv EMAIL_TRANSPORT smtp
  ```

  `OPS_ALERT_EMAIL` accepts several recipients as a comma-separated list; that is standard-library behaviour verified offline on Python 3.12.6 and 3.13.2, **not a pinned repo contract: no repo test sends to more than one address**
  (matrix section 6.4).
- [ ] **5.2 Everything else keeps the committed default** that step 3.2 appended: `EDGE_HOST` (exists), `EDGE_WEB_ROOT=/srv/opsgrids/web`, `TLS_PLATFORM_HOSTS` (the 14 names), `JWT_KID=prod-1`, `JWT_ISSUER=https://auth.opsgrids.com`,
  `COMPOSE_PROFILES` (the box's own; step 6.2), `EDGE_PROFILES=` (empty), `SMTP_PORT=587`, `SMTP_USE_TLS=true`, `SMTP_USE_SSL=false`, `WHATSAPP_TRANSPORT=log`, `ENGAGE_PUBLIC_API_BASE=https://app.opsgrids.com/api/v1`,
  `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=2`, `APP_BASE_URL=https://app.opsgrids.com`, `CORS_ORIGINS=https://app.opsgrids.com`, `LOG_LEVEL=INFO` (**exact upper case**), `STORE_SLUG_CACHE_SECONDS=300`, `BACKUP_S3_PREFIX=edge/postgres`,
  `BACKUP_LOCAL_KEEP=7`, `ASSET_PURGE_RETENTION_DAYS=30`, `DOCUMENT_ORPHAN_SWEEP_ENABLED=false`, `DOCUMENT_ORPHAN_SWEEP_DRY_RUN=true`, `S3_PUBLIC_BUCKET=opsgrids-public`,
  `PUBLIC_ASSET_BASE_URL=https://edge.opsgrids.com/opsgrids-public` (must equal `https://<EDGE_HOST>/<S3_PUBLIC_BUCKET>`), `OPS_MONITOR_API_CONTAINERS=` and `OPS_MONITOR_DLQ_QUEUES=` (empty: derived),
  `BILLING_TAX_CODE=` and `BILLING_TAX_JURISDICTION=` (empty). Do **not** add `BILLING_RUN_ENABLED`, `BILLING_RUN_NOT_BEFORE`, the MFA flag, or `RUN_SWEEPS_IN_API`.
- [ ] **5.3 Duplicate and stray-line check** (preflight and remote.sh take the LAST assignment of a key, but remote.sh still takes the FIRST for `OPSGRIDS_DB_PASSWORD` and `OPS_MONITOR_PORT`, and compose has its own parser; keep every key exactly once; matrix section 11, item 5):

  ```bash
  sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env | sort | uniq -d      # must print nothing
  stat -c '%U %a' .env                                                 # deploy 600
  ```

## Part 6. The owner decisions (category E)

- [ ] **6.1 Memory: `EDGE_BUDGET_ACCEPTED_MB`.** **[OWNER]** On the merged tree the ten profiles price at **4581 MB against a 3862 MB box (118.6%; 719 MB over)**; the box is already over the hard line by the RUNBOOK's own read (104.7%).
  **First apply the RUNBOOK's rule to what step 2.7 measured** (matrix section 6.3): scale the release, `4581 x measured / 4044`:

  ```bash
  awk -v m=<measured MiB from 2.7> 'BEGIN { printf "%.0f\n", 4581 * m / 4044 }'
  ```

  Scaled figure: `______`. **If it is above 4827 (the 125% cap), or if `vmstat` showed `si`/`so` above zero at a quiet hour, do not write a ceiling: choose (a) or (b).** Otherwise
  write the number preflight prints (not the scaled one: the scaled figure only decides whether to run at all). Choose one, in writing:
  (a) resize the VM (the only option that adds headroom; the earlier "no resize" predates this release's +537 MB); (b) de-list products (drop their profiles from `COMPOSE_PROFILES` after checking they have no live activation);
  (c) accept the overcommit. **Do not guess the number: run part 7 first and read the total preflight prints for the commit you will deploy**, then write exactly that:

  ```bash
  set_kv EDGE_BUDGET_ACCEPTED_MB 4581      # <- the number preflight printed, digits only; never above 4827 (125%)
  ```

  Choice: `a / b / c`   Number written: `______`   Date: `______`   By: `______`
  **Also, for this one deploy:** `set_kv EDGE_UP_PARALLEL_LIMIT 2` (starts two containers at a time; remove it in part 11).
- [ ] **6.2 Which products run: `COMPOSE_PROFILES`.** **[OWNER]** Keep the box's existing value (all ten per ADR-0013: `inventory,storefront,hrms,crm,pulseboard,projects,support,marketing,finance,itsm`); leave `EDGE_PROFILES=` empty.
  Names are lower case, comma separated, no spaces. Confirm it prints exactly that (profile names are not secret): `grep '^COMPOSE_PROFILES=' .env`. Removing a profile is coupled to 6.1 and to live activations.
- [ ] **6.3 Ceiling vs cap sanity.** If you chose (c), confirm `EDGE_BUDGET_ACCEPTED_MB` <= 4827 and equals the printed total. A larger number is "slack nobody decided on"; preflight says so.
- [ ] **6.4 The billing run stays OFF for this deploy.** **[OWNER]** Do not add `BILLING_RUN_ENABLED` / `BILLING_RUN_NOT_BEFORE`. Turning it on converts every expired trial and bills every overdue period from the cutover and is not reversible for rows already billed.
  When you are ready, follow the RUNBOOK's staging (step 9.10) with a **dry run first**. Cutover you will use later: `______________` (format `YYYY-MM-DDTHH:MM:SS+05:30` or `Z`; set once, never move it).
- [ ] **6.5 The MFA reseal.** **[OWNER]** From step 2.6 the count is `_____`. The deploy reseals automatically between `migrate` and `up` (and once more after `health`); you accept: (i) for those seconds the old auth container returns a **500 to every MFA sign-in, recovery codes included**, for the sealed members;
  (ii) a count that cannot be read aborts the deploy; (iii) **rolling back to the production ref `47143a0` afterwards is a one-way door for MFA** (every MFA sign-in is a 500 until you roll forward; there is no unseal tool). Accept / not yet: `_____`
  Staff enrolment codes (`bin/issue-enrollment-codes`) apply only if staff sign in on this box (admin-gateway is not deployed here): needed? `_____`
- [ ] **6.6 Provider webhooks.** **[OWNER]** Do not configure any webhook URL in Razorpay or Meta until step 9.7 shows the routes are reachable. If they are not, fixing that is a code/route decision, not an environment value (matrix section 9.1). Decision after 9.7: `______`
- [ ] **6.7 Tax.** **[OWNER]** Leave `BILLING_TAX_CODE` and `BILLING_TAX_JURISDICTION` empty until the deployment is tax-registered **and** a `billing.tax_rates` row exists (no migration seeds one). Registered? `_____`

## Part 7. Preflight until it says `preflight: ok` (`[STRUCTURAL]`)

- [ ] **7.1 Run it against the release you are about to deploy** (`[laptop]`). `make edge-preflight` runs whatever ref is *currently checked out on the box* (it takes no `REF`); to preflight the target ref use `ssh-deploy.sh`, which first checks that ref out (detached) on the box, exactly as `make deploy` would:

  ```bash
  REF=origin/release/backlog-complete       # the same REF as step 0.1 (5320848 or later; this is your laptop shell, not the box's)
  infrastructure/edge/ssh-deploy.sh deploy@20.219.18.84 edge.opsgrids.com "$REF" preflight
  ```

  Preflight is read-only against your configuration; the only thing it starts is one throwaway container (`id -u`) to check the ops-monitor state directory's owner (INT:pf:586).
  It contacts **no** provider and verifies **no** credential (matrix section 13). Expect the last line `preflight: ok`.
- [ ] **7.2 Read the memory line** (this is where the number for step 6.1 comes from):
  `warn  memory budget (post-deploy: running that the new compose still defines, plus declared profiles): 4581M against 3862M MemTotal: 719M over MemTotal (118.6%)` with the ceiling set, or `FAIL memory budget ... no EDGE_BUDGET_ACCEPTED_MB is set` without it.
  Printed total: `______` MB.
- [ ] **7.3 What each FAIL means:**

  | Row | Fix |
  |---|---|
  | `missing or empty in .env: KEY ...` | the line is absent, or empty where empty is not allowed: step 3.2 / the provider step |
  | `still carrying a placeholder value: KEY ...` | `CHANGE_ME*`, `replace-me*`, `dev-only-*`, `ci-only-*` or `*example.com*`: generate (3.3) or set (5.1) |
  | `KEY is N bytes: it must be at least 32`, `ADMIN_GATEWAY_HOP_KEY must differ from INTERNAL_SERVICE_TOKEN` | regenerate the **new** key (`ADMIN_GATEWAY_HOP_KEY`) only; if a category-D key is short, stop (matrix section 12, item 1) |
  | `.env is mode NNN` | `chmod 600 infrastructure/edge/.env` |
  | `PUBLIC_ASSET_BASE_URL must be exactly https://$EDGE_HOST/$S3_PUBLIC_BUCKET` | set it to exactly that |
  | `EDGE_BUDGET_ACCEPTED_MB ... whole number` / `above the 125% cap` / `over the owner-accepted ceiling` | digits only, no unit, sign or leading zero (`4581`, not `04581`); the printed total; not above 4827 |
  | `KEY in .env has an unquoted value containing whitespace` (or `... a shell metacharacter`) | double-quote the whole value; nothing was sourced or run |
  | `KEY in .env has text after its closing quote` | put the whole value inside the quotes |
  | `.env has Windows (CRLF) line endings` | `sed -i 's/\r$//' .env` |
  | `profile 'x' (from EDGE_PROFILES/COMPOSE_PROFILES) is not defined in compose.prod.yml` | a typo: use only `inventory storefront hrms crm pulseboard projects support marketing finance itsm`, comma separated, no spaces |
  | `no memory-budget.json`, `no python3`, `could not read MemTotal` | the box's checkout or tooling is not what the repo describes: stop and look (the gate now fails closed) |
  | `no signing key at ...` | category D: the file must already exist (mode 400/440/600/640); if missing, stop |
  | `the Caddyfile serves  but TLS_PLATFORM_HOSTS does not list it` | **the known defect**: your `REF` predates `ce35c77` (Appendix A). Choose a ref that contains it |
  | `only NG free`, `inodes NN% used` | real box problems: free disk (`docker image prune -af && docker builder prune -af`), see the RUNBOOK |

- [ ] **7.4 Repeat until `preflight: ok`.** **Proves:** every required key is present and well formed; no placeholder remains; the three platform secrets are long enough and distinct; the memory decision is recorded. `[STRUCTURAL]`
  **It does not prove** that any credential works.

## Part 8. Deploy

- [ ] **8.1 Deploy** (`[laptop]`, in your quiet window; `[box: deploy]` second terminal running `vmstat 5`):

  ```bash
  make deploy HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com REF=<ref>
  ```

  `up` prints `up: starting N containers at a time, profiles: ...` (INT:rs:341): read that line, it is the profile list remote.sh will actually use. What runs, in this order (INT:rs:509-521): the wrong-box interlock (`CONFIRM` must equal the box's `EDGE_HOST`) -> the ref is checked out on the box -> **preflight** -> **build** (5-10+ minutes; a failed build leaves the site as it was) ->
  **validate** the Caddyfile with the pinned Caddy -> **migrate** (each service's `alembic upgrade head`, **then the MFA reseal if the count is above 0**) -> **up** (`--remove-orphans`, `EDGE_UP_PARALLEL_LIMIT` at a time; brief downtime per changed container) -> Caddy reload -> **health** (up to `HEALTH_TIMEOUT`, 600 s) ->
  the reseal once more (warn-only) -> record the ref. **There is no automatic rollback**: if health does not settle the deploy exits non-zero with the containers as they are, and no ref is recorded.
- [ ] **8.2 Watch the swap columns.** `si`/`so` climbing and staying high means thrashing: wait, do not interrupt; the box is expected to be slow while ~50 interpreters import.
- [ ] **8.3 If it fails**, read the failing step's output. Nothing was rolled back for you. Go to part 10 only after you understand what failed.
- [ ] **8.4 Remove the temporary throttle** once it succeeded (`[box: deploy]`): `sed -i '/^EDGE_UP_PARALLEL_LIMIT=/d' .env && chmod 600 .env`.

## Part 9. Verify

- [ ] **9.1 `make edge-status HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com`** `[READ-ONLY]`. Read the `ops-monitor` block at the bottom. On a freshly deployed box expect `backup: "no marker yet"` until step 9.6 and everything else `ok` or still `pending`
  (fewer than three polls old); `firing` this early usually means a profile is listed in `COMPOSE_PROFILES` whose API is not up yet (INT:rb:259).
- [ ] **9.2 The edge answers** `[READ-ONLY]`:

  ```bash
  curl -si http://20.219.18.84/_edge/health | head -1          # plain HTTP, no TLS: 200 "ok"
  curl -si https://edge.opsgrids.com/_edge/health | head -1    # TLS on a platform name
  curl -si https://edge.opsgrids.com/api/v1/auth/health | head -1   # through Caddy -> gateway -> a service
  ```
- [ ] **9.3 The reseal left nothing behind** `[READ-ONLY]`: re-run step 2.6; it must print `0`. Count: `_____`
- [ ] **9.4 The merged sweeps started, and the crons are gone** `[READ-ONLY]` (INT:rb:1519):

  ```bash
  cd /srv/opsgrids/repo/infrastructure/edge
  C="docker compose -f compose.prod.yml --env-file .env"
  $C logs --tail 200 notification-worker | grep -E 'notification.worker.starting|notification.cron.started'
  $C logs --tail 200 operations | grep inventory.reconcile.started
  $C logs --tail 200 storefront | grep storefront.domain.sweep.started
  $C logs --tail 200 document   | grep document.sweep.started
  $C ps | grep -c -- '-cron'      # 0
  ```

  A missing line means that container's `RUN_SWEEPS_IN_API` is falsy (anything but `true`, `1`, `yes`, `on`, `y`, `t`), its sweep's precondition failed (look for `<sweep>.not_started`), or the container restarted since.
- [ ] **9.5 Memory after** `[READ-ONLY]`: `free -m` and `docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'`; compare with step 2.7. After: swap used `_____` MB.
- [ ] **9.6 `[LIVE: owner]` The first real backup** (if step 4.2 was done): `make edge-backup HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com`. Success is the `uploaded database + certificates to s3://...` line and `backup: ok`; then `make edge-status` shows the `backup` check `ok`.
  Result: `_____`
- [ ] **9.7 Are the provider webhooks reachable?** `[READ-ONLY]`: these hit only our own edge, with an empty body and no credential. **Read the codes carefully** (matrix section 9.1):

  ```bash
  curl -s -o /dev/null -w 'razorpay:  %{http_code}\n' -X POST https://app.opsgrids.com/api/v1/webhooks/razorpay -H 'Content-Type: application/json' -d '{}'
  curl -s -o /dev/null -w 'whatsapp:  %{http_code}\n' 'https://app.opsgrids.com/api/v1/webhooks/whatsapp?hub.mode=subscribe&hub.verify_token=x&hub.challenge=1'
  ```

  `401` = stopped at the gateway (not reachable); Razorpay `400` = reached payment (a bad signature is a 400 and nothing is recorded); WhatsApp `403` = reached notification. Results: `_____ / _____`.
  Only if both reach their service is it sensible to configure the callback URLs at the providers and then **`[LIVE: owner]`** confirm with a real provider delivery. If `401`, leave webhooks unconfigured and raise it as a code decision.
- [ ] **9.8 `[LIVE: owner]` SMTP end to end** (if not done in 4.1): run the probe; then wait for the first alert or the 08:00 UTC digest and confirm both recipients received it and `/status` shows `last_send_at`.
- [ ] **9.9 Image uploads only (MinIO + document).** If this box sells Storefront images, the RUNBOOK's steps apply (INT:rb:277): reconcile the `core` permissions **dry run first**
  (`make edge-reconcile HOST=... CONFIRM=... PRODUCTS=core`, then `... APPLY=1`), republish the bundles (`make edge-web HOST=... CONFIRM=... APP=buyer` and `... APP=storefront`) so `PUBLIC_ASSET_BASE_URL` is baked in,
  and verify an upload end to end (RUNBOOK 'Verify an upload end to end'). `minio-init` completing (exit 0) is expected; it is a one-shot.
- [ ] **9.10 Billing run, when you are ready (not part of this deploy)** (INT:rb:595): deploy with the run off (done); pick the cutover; preview with
  `docker compose -f compose.prod.yml --env-file .env run --rm -T subscription-worker python -m app.billing_run --dry-run --not-before <cutover>` (writes nothing); then append
  `BILLING_RUN_ENABLED=true` and `BILLING_RUN_NOT_BEFORE=<cutover>` and `docker compose -f compose.prod.yml --env-file .env up -d subscription-worker`; watch `/health/billing-run` (`overdue`, `failing`, `failing_permanent`). Preflight must pass with both set (it checks the cutover's shape and that it is a real instant).
- [ ] **9.11 Staff enrolment codes** (only if staff sign in on this box): `docker compose ... run --rm -T auth python -c "from app.bootstrap import issue_codes_main; issue_codes_main()"` prints `email code` pairs once; hand each over out of band (INT:rb:855).

## Part 10. Rollback

- [ ] **10.1 The application:** `make edge-rollback HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com [TO=<sha>]` redeploys the previous ref that actually served (same preflight, build, validate, recreate; as slow as a deploy). **Prefer rolling forward with a fix.**
- [ ] **10.2 MFA is the one-way door.** After the reseal a ref older than this release cannot read sealed factors: every MFA sign-in is a 500 until you roll forward (INT:rb:1539). There is no unseal tool.
- [ ] **10.3 `.env`** is not in git: to undo `.env` edits, restore the backup from step 3.1 (`cp -p .env.bak.<stamp> .env`; then `stat -c '%a' .env` = 600). The previous ref's preflight reads the previous ref's example.
- [ ] **10.4 Migrations are not undone** by a rollback (`alembic upgrade head` at the older ref is a no-op). A downgrade can destroy data and the last backup may be 24 hours old: think before running one (INT:rb:997).
- [ ] **10.5 A broken storefront page only:** `make edge-web-rollback HOST=... CONFIRM=...` (one symlink).

## Part 11. Clean up and record

- [ ] **11.1** Delete the `.env` backups once the deploy is healthy: `shred -u .env.bak.* 2>/dev/null || rm -f .env.bak.*` (`[box: deploy]`).
- [ ] **11.2** `EDGE_UP_PARALLEL_LIMIT` removed (8.4). `stat -c '%U %a' .env` is `deploy 600`.
- [ ] **11.3** Keep a private record (not in the repo, not in chat): the REF deployed and its short SHA; the `EDGE_BUDGET_ACCEPTED_MB` value, its date and who decided; the MFA counts before/after; the off-box location of the `PII_ENCRYPTION_KEY` backup.
- [ ] **11.4** Re-read `ENV_SOURCE_MATRIX.md` section 12 (unresolved items) and decide who owns each open one.

## Appendix A. The two preflight gotchas (fixed on `ce35c77`, still live on any earlier ref)

The owner's two known defects. Both are **fixed on integration since `ce35c77`** (the memory-headroom merge) and **present on `193b529` and earlier**; which ref you deploy decides. They are reproduced in the matrix, section 13 (runs R7a-R7c).

1. **The Caddyfile catch-all row fails every run.** On `193b529` and earlier the catch-all site line `https:// {` is read by preflight's `sed` as an empty site name, so `FAIL the Caddyfile serves  but TLS_PLATFORM_HOSTS does not list it` appears whatever the values are and `make deploy` refuses at its first step.
   No `.env` value fixes it. Workaround: deploy a ref that contains the fix (`git merge-base --is-ancestor ce35c77 <ref>`); do not edit preflight on the box.
2. **Unquoted whitespace in `.env` exits 127.** `.env` is `source`d as bash, so `OPS_ALERT_EMAIL=info@opsgrids.com and zionna@opsgrids.com` or `EDGE_PROFILES=inventory, storefront` runs the words after the first space as a command: on `193b529`
   `./.env: line N: and: command not found`, exit 127, no `preflight: FAILED` footer. Workaround: **quote any value containing a space** (`SMTP_PASSWORD="<app password with spaces>"`) or avoid spaces; use the comma form for `OPS_ALERT_EMAIL`.
   Since `ce35c77` it is a named FAIL, and the same pre-check refuses an unquoted `; & | < > ( )`, a backtick or `$(`, text after a closing quote, and CRLF line endings, before anything is sourced.
3. Also: a `make edge-preflight` runs the *checked-out* ref (use `ssh-deploy.sh ... <ref> preflight`, step 7.1); a laptop cannot run preflight meaningfully (disk, inode and `date` rows are machine-specific: matrix section 13.3).

## Appendix B. How each provider credential is verified, and by whom

Nothing in the "live" column has been run. **A green preflight never appears in the "verified in production" column.**

| Credential | Structural check (preflight / shell) | Live call that would verify it (safe) | Who | Sends or changes anything? |
|---|---|---|---|---|
| `SMTP_PASSWORD` (+ username, host, port) | line present; empty allowed; not a placeholder | the SMTP login probe (4.1); then ops-monitor `/status` `last_send_at` after a real alert or the digest | owner | the probe sends no mail; a real alert sends mail to `OPS_ALERT_EMAIL` |
| `BACKUP_S3_*` | pair both-or-neither | `make edge-backup` (9.6) and the RUNBOOK restore drill | owner | uploads objects to the bucket (creates data) |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | empty allowed; presence prints an `ok` | **none exists in the platform** (the adapter is a stub); only a direct request to Razorpay | owner (provider-side) | n/a |
| `RAZORPAY_WEBHOOK_SECRET` | present, not `dev-only-*` | a Razorpay-signed delivery accepted by the service, after step 9.7 shows the route is reachable | owner | Razorpay would deliver a test event |
| `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` | empty allowed | the read-only Graph `GET /{id}?fields=id` (4.4) | owner | read-only call to Meta |
| `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` | empty allowed | Meta's subscribe handshake and a genuine signed delivery, after 9.7 | owner | Meta calls our endpoint |
| `MINIO_*`, `DOCUMENT_S3_*` | present, >= 8 chars where required | `minio-init` exits 0 and an upload round trip (RUNBOOK 'Verify an upload end to end') | operator | creates a test asset |
| `ADMIN_GATEWAY_HOP_KEY`, `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY`, `ENGAGE_UNSUBSCRIBE_SECRET`, passwords | present, >= 32 bytes, distinct | none: locally generated secrets have no external verifier; the services starting is the signal | n/a | n/a |
