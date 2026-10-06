# Manual deployment checklist: environment, secrets and the deploy

For the **manual** deployment of the final release to the production box. The owner ssh's to the box as `azureuser` for
admin steps (from the owner's instruction; the repo only records that Azure's provisioning account is `azureuser`: INT:rb:1106) and deploys from a laptop clone with

```bash
make deploy HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com REF=origin/release/backlog-complete
```

(`REF` must be a ref that contains `5320848`: step 0.1. Write a real ref, never the `<ref>` placeholder: pasted literally it is a shell redirection.)

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
  straight into the file (the helpers below) or from the provider's dashboard straight into the file. (One sanctioned exception, step 4.3: a value goes from the box
  to your clipboard, never to a terminal.)
- Do not `git add` anything in the repo root that you did not write. At audit time an untracked private-key file (`*.pem`) sat in the root of the main checkout. The ignore rule
  for `*.pem` and `*.key` is on integration and on this branch (commit `cdd1e14c`) but **not yet on `origin/main`**, so in a checkout based on main such a file is not ignored: never `git add -A` there.
- Run a `Do` block exactly as written. Where a step says **[OWNER]** it is a decision only the owner can make; write the answer in the blank.

## The order

1. Decide the **ref** to deploy (part 0). 2. Take the **read-only inventory** of the box (part 2). 3. **Back up** `.env` (3.1). 4. **Append** the missing keys (3.2).
5. **Generate** the nine secrets (3.3). 6. **Obtain** the provider values (part 4). 7. **Configure** the plain values (part 5). 8. Run **preflight once** to read the memory total (7.1-7.2):
   with no ceiling yet it FAILs on the memory row only, and that row prints the number you need. 9. Make the **owner decisions**, memory first (part 6).
10. Run **preflight** until it prints `preflight: ok` (part 7). 11. **Deploy** in a quiet window (part 8). 12. **Verify** (part 9). 13. Know your **rollback** (part 10).

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
  webhooks may be used (6.6); tax registration (6.7); and, only if this box sells Storefront images, the permission reconcile that signs every member out (9.9).
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

  Preflight fails the deploy if the mode is not 600 or 400 (INT:pf:30-32). Three more read-only shape checks on the raw file, counts, names and line numbers only (preflight refuses the file, before reading it, if the first is nonzero; a repeated key is read differently by remote.sh in two places; an unbalanced quote is the one quoting mistake preflight's pre-check does NOT catch, Appendix A item 2):

  ```bash
  grep -c $'\r' .env                                                  # 0: a CRLF file is a preflight FAIL
  sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env | sort | uniq -d       # prints nothing: no key defined twice
  awk -v q="'" '/^[A-Z][A-Z0-9_]*=/ { d = gsub(/"/, "&"); s = gsub(q, "&"); if (d % 2 || s % 2) print "check the quotes on line " NR }' .env   # prints nothing
  ```

  (A line number from the last command means: look at that line's quotes; a legitimate `"it's"` also prints.)
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
  awk -v allowed="$allowed" -v q="'" '
  BEGIN { n = split(allowed, a, " "); for (i = 1; i <= n; i++) ok[a[i]] = 1 }
  /^[A-Z][A-Z0-9_]*=/ {
    i = index($0, "="); k = substr($0, 1, i - 1); v = substr($0, i + 1)
    gsub("[\"" q " \r]", "", v)
    if (v == "" && !(k in ok)) print "EMPTY (must be set)  " k
    else if (v ~ /^(replace-me|CHANGE_ME|dev-only-|ci-only-)/ || v ~ /example\.com/) print "PLACEHOLDER          " k
  }' .env
  ```

  (`$allowed` should hold 26 names on this release. The awk builds its quote character with `-v q="'"` and uses only POSIX awk features; it was exercised under macOS awk only, and on any other awk a failure is a loud syntax error, not a silent miss.)
  A `PLACEHOLDER` line for `ACME_EMAIL` is the committed `ops@example.com`. Re-run this after each of parts 3, 4 and 5: the new secrets step 3.2 appends are `EMPTY` on purpose until step 3.3 fills them.
- [ ] **2.5b Length of the secrets that have no length check** (bytes only, never a value). Preflight length-checks only `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY` and `ADMIN_GATEWAY_HOP_KEY` (>= 32 bytes) and the two MinIO secrets (>= 8 characters);
  every other secret gets a presence and placeholder check only. For the ones the box is expected to have (category D, inferred):

  ```bash
  for k in POSTGRES_PASSWORD OPSGRIDS_DB_PASSWORD RABBITMQ_PASSWORD RAZORPAY_WEBHOOK_SECRET; do
    printf '%-26s %s bytes\n' "$k" "$(sed -n "s/^${k}=//p" .env | tail -n 1 | tr -d "\"' \r\n" | wc -c | tr -d ' ')"
  done
  ```

  A value under 32 bytes is legal but weak. Note it, and do **not** regenerate `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD` or `RABBITMQ_PASSWORD` in `.env` alone (they take effect only on a fresh volume: matrix section 3.2);
  a weak `RAZORPAY_WEBHOOK_SECRET` is rotated together with Razorpay's webhook settings (an owner decision). Lengths: `______`
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
name, and **never overwrites a real value** (it acts only on an empty value or a `CHANGE_ME*`, `replace-me*`, `dev-only-*` or `ci-only-*` placeholder). It stops without writing when the key is absent
or repeated, and it passes the new value through the environment rather than a command line, so `ps` never shows it. It was exercised in a scratch directory before this file was written (14 cases, bash 3.2 and 5): a real value was kept,
a placeholder was generated, an absent or repeated key stopped it, the mode stayed 600 and no temp file was left.

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

  **Proves:** re-running step 2.4 prints nothing. `[STRUCTURAL]` This appends **every** missing key, the two optional ones (`EDGE_PROFILES`, `EDGE_BUDGET_ACCEPTED_MB`) included, so up to 22 lines are added (20 are required).
  Each line is the release's own example line: the new secrets (`ADMIN_GATEWAY_HOP_KEY`, `ENGAGE_UNSUBSCRIBE_SECRET`, `MINIO_ROOT_*`, `DOCUMENT_S3_*`) and `OPS_ALERT_EMAIL` arrive **empty**, and preflight refuses the empty secrets until step 3.3: that is the point.
  **Keys the box already has are never touched by this step**: `ACME_EMAIL`, `EDGE_RESERVED_IP`, the SMTP keys, `EDGE_WEB_ROOT`, `TLS_PLATFORM_HOSTS`, `JWT_*`, `DB_*`, `APP_BASE_URL`, `CORS_ORIGINS`, `LOG_LEVEL` and the rest of the production ref's 44 keys keep whatever the box holds (which this file cannot see).
- [ ] **3.3 Define the helper, then generate.** Paste the function, then the calls. **Run it on exactly these nine keys.** Eight are category A and new (`ADMIN_GATEWAY_HOP_KEY`, `ENGAGE_UNSUBSCRIBE_SECRET`, `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, the four `DOCUMENT_S3_*`);
  the ninth, `RAZORPAY_WEBHOOK_SECRET`, is category D (very probably on the box already, inferred): the helper fills it only if it is empty or a placeholder and otherwise keeps it. A tenth, `WHATSAPP_VERIFY_TOKEN` (category A), only if you will enable WhatsApp webhooks.
  **Never run it on `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD`, `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY` or `RABBITMQ_PASSWORD` of this box: they are category D (expected to exist: an inference from the production ref, not determinable from the repo) and regenerating them breaks logins, tokens or every sealed value.**

  ```bash
  fill_if_unset() {   # usage: fill_if_unset KEY BYTES   (never overwrites a real value; never prints one)
    local k="$1" n="$2" cnt cur new tmp
    cnt=$(grep -c "^${k}=" .env 2>/dev/null || true)
    if [ "${cnt:-0}" -ne 1 ]; then echo "STOP:      $k is on ${cnt:-0} lines of .env (need exactly 1: steps 2.2 and 3.2)"; return 1; fi
    cur=$(sed -n "s/^${k}=//p" .env | tr -d "\"' \r")
    case "$cur" in
    '' | CHANGE_ME* | replace-me* | dev-only-* | ci-only-*)
      new=$(openssl rand -hex "$n") && [ -n "$new" ] || { echo "STOP:      openssl gave no value for $k"; return 1; }
      tmp=$(umask 077 && mktemp ./.env.XXXXXX) || return 1
      # the value travels in the environment, not on a command line, so `ps` never shows it
      if NEWVAL="$new" KEYNAME="$k" awk 'BEGIN { FS = OFS = "=" } $1 == ENVIRON["KEYNAME"] { $0 = ENVIRON["KEYNAME"] "=" ENVIRON["NEWVAL"] } { print }' .env > "$tmp"; then
        mv "$tmp" .env; chmod 600 .env; echo "generated: $k"
      else
        rm -f "$tmp"; echo "STOP:      could not rewrite .env for $k"; return 1
      fi ;;
    *) echo "kept:      $k" ;;
    esac
  }
  fill_if_unset ADMIN_GATEWAY_HOP_KEY          32   # a separate openssl call from INTERNAL_SERVICE_TOKEN's
  fill_if_unset ENGAGE_UNSUBSCRIBE_SECRET      32
  fill_if_unset RAZORPAY_WEBHOOK_SECRET        32   # category D: kept if the box has one; paste into Razorpay only once the webhook is reachable (part 4.3)
  fill_if_unset MINIO_ROOT_USER                16
  fill_if_unset MINIO_ROOT_PASSWORD            32
  fill_if_unset DOCUMENT_S3_ACCESS_KEY         16
  fill_if_unset DOCUMENT_S3_SECRET_KEY         32
  fill_if_unset DOCUMENT_S3_ORPHAN_ACCESS_KEY  16   # set BOTH orphan values now: minio-init adds both users unconditionally
  fill_if_unset DOCUMENT_S3_ORPHAN_SECRET_KEY  32
  # only if you will enable WhatsApp webhooks (matrix section 9): fill_if_unset WHATSAPP_VERIFY_TOKEN 32
  ```

  Why hex: `openssl rand -hex N` is N random bytes as 2N hex characters, which contain nothing `.env` needs quoted and satisfy every length rule (>= 32 bytes for the three platform secrets;
  MinIO's >= 8). The commands are exactly the ones in the matrix, section 6.5. **What each output line means:** `generated:` = it replaced an empty or placeholder value; `kept:` = it left a real value alone; `STOP:` = the key is absent or
  on more than one line, or `openssl` failed, and **nothing was written** (fix that first: steps 2.2 and 3.2). Only step 3.4 proves the new value has the right shape. `[STRUCTURAL]`
- [ ] **3.4 Check the shape without printing:**

  ```bash
  shape() { if grep -q "^$1=$2\$" .env; then echo "  ok   $1"; else echo "  BAD  $1"; fi; }
  for k in ADMIN_GATEWAY_HOP_KEY ENGAGE_UNSUBSCRIBE_SECRET RAZORPAY_WEBHOOK_SECRET MINIO_ROOT_PASSWORD DOCUMENT_S3_SECRET_KEY DOCUMENT_S3_ORPHAN_SECRET_KEY; do shape "$k" '[0-9a-f]\{64\}'; done
  for k in MINIO_ROOT_USER DOCUMENT_S3_ACCESS_KEY DOCUMENT_S3_ORPHAN_ACCESS_KEY; do shape "$k" '[0-9a-f]\{32\}'; done
  ```

  **Proves:** the value is 64 (or 32) lower-case hex characters: right form. `[STRUCTURAL]` A `BAD` on a key that printed `kept:` in 3.3 is a value the box already had that is not helper-shaped (for example a `RAZORPAY_WEBHOOK_SECRET` of another form):
  that is fine if preflight is ok. It proves nothing about anyone having seen it: that is why steps 3.1-3.3 avoid printing.
- [ ] **3.5 Category D keys are untouched.** Nothing to do; this line exists so you notice if you did something. `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD`, `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY`,
  `RABBITMQ_PASSWORD`, `RAZORPAY_WEBHOOK_SECRET`, the signing key file and `EDGE_HOST` are **expected to exist**: that is an inference from the production ref's example, that ref's preflight and ADR-0013, and the box's `.env` was not read, so it is not determinable from the repo
  (steps 2.4 and 2.5 check presence only). If step 2.4/2.5 reported one of them missing, **stop**: that means this box is not what the repo describes
  (matrix section 12, item 1), and the answer is not to regenerate it.


## Part 4. Obtain the provider values (category B)

Paste each value **from the provider's page straight into `.env` on the box** with an editor (`[box: deploy]`, `nano .env` or `vi .env`), never through the shell history, chat or a ticket. A value with a space goes in **double** quotes;
a value with `$`, a backtick or a backslash goes in **single** quotes (Appendix A item 2). After editing: `chmod 600 .env`, then re-run steps 2.2 and 2.5 (names only). Nothing in this part has been run against a provider.

- [ ] **4.1 Google Workspace SMTP** (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`, `SMTP_USE_TLS`, `SMTP_USE_SSL`). **[OWNER]**
  *Where:* the Google Account that owns the `info@opsgrids.com` mailbox -> Security -> 2-Step Verification -> App passwords (repo text: INT:ex:143; provider-side, not verified in repo). Create one and copy it once.
  *Expected values* (the committed defaults): `SMTP_HOST=smtp.gmail.com`, `SMTP_PORT=587`, `SMTP_USERNAME=info@opsgrids.com`, `SMTP_USE_TLS=true`, `SMTP_USE_SSL=false`, `EMAIL_FROM=info@opsgrids.com`, and `SMTP_PASSWORD` = the app password (quoted per the rule above if it has spaces).
  Six of these seven keys are in the production ref's example and that ref's preflight required each line, so they are **expected to exist on the box with values this file cannot see** (not determinable from the repo): compare them with step 2.5 (names only) and change only what is wrong. Only `SMTP_USE_SSL` is new (step 3.2 appended it).
  One `SMTP_USE_TLS` serves both notification and ops-monitor (matrix section 6.4).
  `[STRUCTURAL]` preflight accepts the password's presence, not its correctness (a fake value passes: run R1).
  **`[LIVE: owner]` verify it, sending no mail** (`[box: deploy]`, in `infrastructure/edge`). This mirrors what ops-monitor does (`starttls` with a verified context, then login) and prints only the exception class:

  ```bash
  ( set -a; . ./.env; set +a
    python3 - <<'PY'
  import os, smtplib, ssl
  host, port = os.environ["SMTP_HOST"], int(os.environ["SMTP_PORT"])
  user, pw = os.environ.get("SMTP_USERNAME", ""), os.environ.get("SMTP_PASSWORD", "")
  ctx = ssl.create_default_context()
  truthy = ("1", "true", "yes", "on", "t", "y")   # the spellings pydantic (the services) accepts as true
  ssl_mode = os.environ.get("SMTP_USE_SSL", "false").lower() in truthy
  tls_mode = os.environ.get("SMTP_USE_TLS", "true").lower() in truthy
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
  (ops-monitor `/status` then shows a non-null `last_send_at`; `make edge-status` prints it). The probe verifies the server certificate; **notification's own STARTTLS does not** (matrix section 6.4), so an OK here does not prove that path is protected.
  **Only after `OK`** set `EMAIL_TRANSPORT=smtp` **and** `OPS_ALERT_EMAIL` (step 5.1). `EMAIL_TRANSPORT=log` silences notification only: ops-monitor ignores it and sends whenever `SMTP_HOST`, `EMAIL_FROM` and `OPS_ALERT_EMAIL` are all non-empty,
  so until the probe prints OK leave `OPS_ALERT_EMAIL` empty (the release's example carries it empty, so step 3.2 appended it empty).
  Result: `_____`
- [ ] **4.2 Off-box backups** (`BACKUP_S3_BUCKET`, `BACKUP_S3_ENDPOINT`, `BACKUP_S3_REGION`, `BACKUP_S3_ACCESS_KEY_ID`, `BACKUP_S3_SECRET_ACCESS_KEY`; `BACKUP_S3_PREFIX` keeps its default). **[OWNER]**
  *Where:* the backup provider's console (the example names Backblaze B2 and Cloudflare R2 as options: INT:ex:228; **which provider is in use is not determinable from the repo**). Create the bucket, a lifecycle rule for
  retention (the box only prunes its local copies), and an application key scoped to that one bucket. Copy the endpoint URL, region, key id and secret (all console steps are provider-side, not verified in repo).
  *Rule:* set both credentials or neither: exactly one is a preflight FAIL, both empty is only a warning and `backup.sh` then refuses to upload and exits non-zero every night
  (INT:bk:91-94).
  *Scope caution:* the example asks for a write-only key, but the RUNBOOK's restore drill downloads with the same pair, so a write-only key cannot run the drill. Decide which you want.
  `[STRUCTURAL]` preflight checks the pair is consistent. **`[LIVE: owner]` verify it after the deploy** with `make edge-backup HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com`: it really uploads (creates objects in the bucket) and prints
  `uploaded database + certificates to s3://...`; only then does it write the marker ops-monitor's `backup` check reads. Then do the RUNBOOK's restore drill once (INT:rb:1557). Result: `_____`
- [ ] **4.3 Razorpay** (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`; `RAZORPAY_WEBHOOK_SECRET` was generated in 3.3). **[OWNER]**
  **Important (ADR-0032):** the key pair is used when `PAYMENT_WIRE=live`, but it **does nothing by itself**. The platform's Razorpay wire is the in-process stub unless `PAYMENT_WIRE=live`; keys sitting in `.env` change nothing,
  and preflight says so (`payment runs on its in-process stub`). Leave both **empty** until you are ready to take real money (a test-mode checkout cannot be run on this box; rehearse off it), then follow RUNBOOK.md, 'Payments: turning a provider on' (the order, the owner steps, the smoke tool and the rollback).
  Razorpay Dashboard -> Account & Settings -> API Keys (provider-side, not verified in repo); paste into `.env` (mode 600), never into chat. **`[LIVE: owner]`** the platform's own proof is the owner-run `tools/payments_smoke_razorpay.py`
  (test keys, a laptop, never the box); a real invoice paid in test mode locally is the end-to-end proof. `rzp_test_` keys are **refused on this production box** on a live wire unless `PAYMENT_ALLOW_TEST_KEYS=true` (a staging box with no real paying customers only; the owner chose this for the edge box on 2026-10-06: RUNBOOK 'Payments: test mode on a staging box' lists the exact lines); `rzp_live_` keys need `PAYMENT_ALLOW_LIVE_KEYS=true`, and the two flags are never on together.
  **Webhook secret:** do **not** paste `RAZORPAY_WEBHOOK_SECRET` into Razorpay's dashboard (Webhooks) until step 9.7 shows the route is reachable; by static reading it is not (matrix section 9.1). Rotating it later means changing it in `.env` and in Razorpay's Webhooks page together (provider-side, not verified in repo).
  When 9.7 shows the route is reachable and you paste the secret into Razorpay, move it from the box to your clipboard **without printing it** (`[laptop]`; the one sanctioned exception to "never print a `.env` value": nothing reaches a terminal), then clear the clipboard:

  ```bash
  ssh deploy@20.219.18.84 "sed -n 's/^RAZORPAY_WEBHOOK_SECRET=//p' /srv/opsgrids/repo/infrastructure/edge/.env | tail -n 1 | tr -d '\r\n'" | pbcopy
  # paste it into the Razorpay form, then empty the clipboard:
  pbcopy < /dev/null
  ```
- [ ] **4.4 Meta / WhatsApp** (`WHATSAPP_TRANSPORT`, `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`). **[OWNER]**
  **Recommended for this deploy: change nothing.** `WHATSAPP_TRANSPORT=log` sends nothing, the other four stay empty (allowed).
  If and when you enable it: *where:* Meta for Developers -> your app -> Settings -> Basic (**App secret**); WhatsApp -> API Setup (**Phone number ID**); Business settings -> System users -> generate a token (**access token**)
  (all provider-side, not verified in repo). `WHATSAPP_VERIFY_TOKEN` you generate (`fill_if_unset WHATSAPP_VERIFY_TOKEN 32`) and paste into the app's webhook configuration (move it to your clipboard exactly as in step 4.3, with `WHATSAPP_VERIFY_TOKEN` in place of the Razorpay key). Set `WHATSAPP_TRANSPORT=whatsapp_cloud` **last**
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
- [ ] **4.6 Reserved IP (`EDGE_RESERVED_IP`).** **[OWNER]** This key is in the production ref's example and that ref's preflight refused it empty or as the documentation address, so the box very probably already holds a real address (inferred; not determinable from the repo):
  **keep it, do not blank it** (the release's preflight allows empty, but a merchant may already have put the address in DNS). Confirm in the Azure portal that the VM's public IP address resource is **Static** (Standard SKU, static allocation):
  the repo records this as an open owner action (INT:docs/adr/0012-custom-domains.md:1793-1797; INT:docs/adr/open-questions-register.md:72, item 36); the portal check itself is provider-side, not verified in repo. Whether `20.219.18.84` is a static reservation is not determinable from the repo.
  If the key is empty on your box and you have a real reserved address, `set_kv EDGE_RESERVED_IP IP_ADDRESS` after step 5's helper. Static? Yes / No: `_____`
- [ ] **4.7 The two alert mailboxes exist.** **[OWNER]** `info@opsgrids.com` and `zionna@opsgrids.com` (the owner's stated recipients: from the owner's instruction, not in the repo; ask your mail admin). Preflight cannot check that they exist.

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

- [ ] **5.1 The values that change from what the box holds:**

  ```bash
  # ACME_EMAIL is in the production ref's example and that ref's preflight refused the placeholder, so a real inbox is probably on the box already: replace it ONLY if it is still the placeholder or empty.
  if grep -Eq '^ACME_EMAIL=(.*example\.com.*)?$' .env; then set_kv ACME_EMAIL info@opsgrids.com; else echo "kept: ACME_EMAIL"; fi   # any inbox a human reads
  # only after step 4.1's probe printed OK (ops-monitor sends whenever SMTP_HOST, EMAIL_FROM and OPS_ALERT_EMAIL are set, whatever EMAIL_TRANSPORT says):
  set_kv OPS_ALERT_EMAIL info@opsgrids.com,zionna@opsgrids.com         # comma, NO spaces (an unquoted space is a named preflight FAIL; exit 127 on 193b529 and earlier)
  set_kv EMAIL_TRANSPORT smtp
  ```

  `OPS_ALERT_EMAIL` accepts several recipients as a comma-separated list; that is standard-library behaviour verified offline on Python 3.12.6 and 3.13.2, **not a pinned repo contract: no repo test sends to more than one address**
  (matrix section 6.4). The two addresses are the owner's stated recipients (from the owner's instruction; not in the repo).
- [ ] **5.2 Everything else keeps its value.** For the **22 keys step 3.2 appended** that is the committed default; for the **44 keys the box already had** it is whatever the box holds (not determinable from the repo: compare by name with step 2.5, change only what is wrong). The committed defaults are:
  `EDGE_HOST` (exists), `EDGE_WEB_ROOT=/srv/opsgrids/web`, `TLS_PLATFORM_HOSTS` (the 14 names), `JWT_KID=prod-1`, `JWT_ISSUER=https://auth.opsgrids.com`,
  `COMPOSE_PROFILES` (the box's own; step 6.2), `EDGE_PROFILES=` (empty), `SMTP_PORT=587`, `SMTP_USE_TLS=true`, `SMTP_USE_SSL=false`, `WHATSAPP_TRANSPORT=log`, `ENGAGE_PUBLIC_API_BASE=https://app.opsgrids.com/api/v1` (**the path must end in `/api/v1`**),
  `DB_POOL_SIZE=5`, `DB_MAX_OVERFLOW=2`, `APP_BASE_URL=https://app.opsgrids.com`, `CORS_ORIGINS=https://app.opsgrids.com`, `LOG_LEVEL=INFO` (**exact upper case**), `STORE_SLUG_CACHE_SECONDS=300`, `BACKUP_S3_PREFIX=edge/postgres`,
  `BACKUP_LOCAL_KEEP=7` (a positive integer: `0` deletes every local dump), `ASSET_PURGE_RETENTION_DAYS=30`, `DOCUMENT_ORPHAN_SWEEP_ENABLED=false`, `DOCUMENT_ORPHAN_SWEEP_DRY_RUN=true`, `S3_PUBLIC_BUCKET=opsgrids-public`,
  `PUBLIC_ASSET_BASE_URL=https://edge.opsgrids.com/opsgrids-public` (must equal `https://<EDGE_HOST>/<S3_PUBLIC_BUCKET>`; write it, and `EDGE_WEB_ROOT`, bare: no quotes, no inline comment), `OPS_MONITOR_API_CONTAINERS=` and `OPS_MONITOR_DLQ_QUEUES=` (empty: derived),
  `BILLING_TAX_CODE=` and `BILLING_TAX_JURISDICTION=` (empty). Do **not** add `BILLING_RUN_ENABLED`, `BILLING_RUN_NOT_BEFORE`, the MFA flag, or `RUN_SWEEPS_IN_API`.
- [ ] **5.3 Duplicate and stray-line check** (preflight, compose and remote.sh take the LAST assignment of a key, but remote.sh still takes the FIRST for `OPSGRIDS_DB_PASSWORD` and `OPS_MONITOR_PORT` and publish-web.sh takes the first raw line for `EDGE_WEB_ROOT` and `PUBLIC_ASSET_BASE_URL`; keep every key exactly once; matrix section 11, item 5):

  ```bash
  sed -n 's/^\([A-Z][A-Z0-9_]*\)=.*/\1/p' .env | sort | uniq -d      # must print nothing
  stat -c '%U %a' .env                                                 # deploy 600
  ```

## Part 6. The owner decisions (category E)

- [ ] **6.1 Memory: `EDGE_BUDGET_ACCEPTED_MB`.** **[OWNER]** On the merged tree the ten profiles price at **4581 MB against a 3862 MB box (118.6%; 719 MB over)**; the box is already over the hard line by the RUNBOOK's own read (104.7%).
  **First apply the RUNBOOK's rule to what step 2.7 measured** (matrix section 6.3): scale the release, `4581 x measured / 4044`:

  ```bash
  MEASURED_MIB=REPLACE_WITH_STEP_2_7      # the "measured sum" from step 2.7
  awk -v m="$MEASURED_MIB" 'BEGIN { if (m + 0 <= 0) { print "set MEASURED_MIB first"; exit 1 } printf "%.0f\n", 4581 * m / 4044 }'
  ```

  Scaled figure: `______`. **If it is above 4827 (the 125% cap), or if `vmstat` showed `si`/`so` above zero at a quiet hour, do not write a ceiling: choose (a) or (b).** Otherwise
  write the number preflight prints (not the scaled one: the scaled figure only decides whether to run at all). Choose one, in writing:
  (a) resize the VM (the only option that adds headroom; the earlier "no resize" predates this release's +537 MB); (b) de-list products (drop their profiles from `COMPOSE_PROFILES` after checking they have no live activation);
  (c) accept the overcommit. **Do not guess the number: run step 7.1 first.** Before a ceiling exists it FAILs on the memory row only, and that row prints the total for the commit you will deploy (step 7.2). Read it, come back here, and write exactly that:

  ```bash
  TOTAL=REPLACE_WITH_THE_PRINTED_TOTAL     # digits only: the number preflight printed (4581 on a fresh scratch run); never above 4827 (125%)
  case "$TOTAL" in
  '' | *[!0-9]* | 0?*) echo "TOTAL must be digits only, no leading zero: nothing written" ;;
  *) set_kv EDGE_BUDGET_ACCEPTED_MB "$TOTAL" ;;
  esac
  ```

  Choice: `a / b / c`   Number written: `______`   Date: `______`   By: `______`
  **Also, for this one deploy:** `set_kv EDGE_UP_PARALLEL_LIMIT 2` (starts two containers at a time; remove it in part 11).
- [ ] **6.2 Which products run: `COMPOSE_PROFILES`.** **[OWNER]** Keep the box's existing value (all ten per ADR-0013: `inventory,storefront,hrms,crm,pulseboard,projects,support,marketing,finance,itsm`); leave `EDGE_PROFILES=` empty.
  Names are lower case, comma separated, no spaces, on a line that starts at column 0 as `COMPOSE_PROFILES=...` (remote.sh reads only that form: an `export COMPOSE_PROFILES=...` or indented line is accepted by preflight but is empty to remote.sh, which would then run core only). Confirm it prints exactly that (profile names are not secret): `grep '^COMPOSE_PROFILES=' .env`.
  `EDGE_PROFILES` is honoured only by preflight and remote.sh: a `docker compose ... --env-file .env` you run by hand ignores it, so keep it empty. Removing a profile is coupled to 6.1 and to live activations.
- [ ] **6.3 Ceiling vs cap sanity.** If you chose (c), confirm `EDGE_BUDGET_ACCEPTED_MB` <= 4827 and equals the printed total. A larger number is "slack nobody decided on"; preflight says so.
- [ ] **6.4 The billing run stays OFF for this deploy.** **[OWNER]** Do not add `BILLING_RUN_ENABLED` / `BILLING_RUN_NOT_BEFORE`. Turning it on converts every expired trial and bills every overdue period from the cutover and is not reversible for rows already billed.
  When you are ready, follow the RUNBOOK's staging (step 9.10) with a **dry run first**. Cutover you will use later: `______________` (format `YYYY-MM-DDTHH:MM:SS+05:30` or `Z`; set once, never move it).
- [ ] **6.5 The MFA reseal.** **[OWNER]** From step 2.6 the count is `_____`. The deploy reseals automatically between `migrate` and `up` (and once more after `health`); you accept: (i) for those seconds the old auth container returns a **500 to every MFA sign-in, recovery codes included**, for the sealed members;
  (ii) a count that cannot be read aborts the deploy; (iii) **rolling back to the production ref `47143a0` afterwards is a one-way door for MFA** (every MFA sign-in is a 500 until you roll forward; there is no unseal tool). Accept / not yet: `_____`
  Staff enrolment codes (`bin/issue-enrollment-codes`) apply only if staff sign in on this box (admin-gateway is not deployed here): needed? `_____`
- [ ] **6.6 Provider webhooks.** **[OWNER]** Do not configure any webhook URL in Razorpay or Meta until step 9.7 shows the routes are reachable. If they are not, fixing that is a code/route decision, not an environment value (matrix section 9.1). Decision after 9.7: `______`
- [ ] **6.7 Tax.** **[OWNER]** Leave `BILLING_TAX_CODE` and `BILLING_TAX_JURISDICTION` empty until the deployment is tax-registered **and** a `billing.tax_rates` row exists (no migration seeds one). Registered? `_____`

## Part 7. Preflight until it says `preflight: ok` (`[STRUCTURAL]`)

- [ ] **7.1 Run it against the release you are about to deploy** (`[laptop]`). `make edge-preflight` runs whatever ref is *currently checked out on the box* (it takes no `REF`); to preflight the target ref use `ssh-deploy.sh`, which **first checks that ref out (detached) on the box**, exactly as `make deploy` would:

  ```bash
  REF=origin/release/backlog-complete       # the same REF as step 0.1 (5320848 or later; this is your laptop shell, not the box's)
  infrastructure/edge/ssh-deploy.sh deploy@20.219.18.84 edge.opsgrids.com "$REF" preflight
  ```

  **This step moves the box's working tree** to the new ref while the containers still run the old one (INT:sd:56-66): files bind-mounted into running containers (the `caddy/` directory, `rabbitmq/rabbitmq.conf`, `routes.prod.json`, the permission contracts: INT:cp:190,298,607,684) change on disk,
  `make edge-status` prints `checked out: <new ref>` beside the old `last good ref`, and a container that restarts before step 8.1 reads the new files. So run 7.1 close to 8.1, not days before. (`make deploy` runs the same preflight first, so a FAIL changes nothing else.)
  Preflight itself changes no configuration; the only thing it starts is one throwaway container (`id -u`) to check the ops-monitor state directory's owner (INT:pf:586).
  It contacts **no** provider and verifies **no** credential (matrix section 13). Expect the last line `preflight: ok`; on the first pass, before a ceiling exists, expect it to FAIL on the memory row only.
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
  | `KEY in .env has an unquoted value containing whitespace` (or `... a shell metacharacter`) | double-quote the whole value (single quotes if it contains `$`, a backtick or a backslash); nothing was sourced or run |
  | `./.env: line N: ... unbound variable`, `unexpected EOF while looking for matching`, or `command not found`, with **no** `preflight: FAILED` line | a `$` in an unquoted or double-quoted value, or an unbalanced quote: the pre-check does not catch these (Appendix A item 2). If a `$(...)` sat inside double quotes it has already **run**: treat that value as suspect. Fix the line with the quoting rules, then re-run |
  | `KEY in .env has text after its closing quote` | put the whole value inside the quotes |
  | `.env has Windows (CRLF) line endings` | `sed -i 's/\r$//' .env` |
  | `profile 'x' (from EDGE_PROFILES/COMPOSE_PROFILES) is not defined in compose.prod.yml` | a typo: use only `inventory storefront hrms crm pulseboard projects support marketing finance itsm`, comma separated, no spaces |
  | `no memory-budget.json`, `no python3`, `could not read MemTotal` | the box's checkout or tooling is not what the repo describes: stop and look (the gate now fails closed) |
  | `no signing key at ...` | category D: the file is expected to exist already (mode 400/440/600/640); if missing, stop |
  | `the Caddyfile serves  but TLS_PLATFORM_HOSTS does not list it` (two spaces: an **empty** name) | **the known defect**: your `REF` predates `ce35c77` (Appendix A). Choose a ref that contains it |
  | `the Caddyfile serves NAME but TLS_PLATFORM_HOSTS does not list it` (a real name) | the box's own `TLS_PLATFORM_HOSTS` is shorter than the committed one (the key already existed, so step 3.2 did not append it): with an editor append `,NAME` (lower case, no spaces) to that one line. Preflight checks only the twelve literal Caddy sites: also keep `edge.opsgrids.com` and `api.opsgrids.com` in the list by hand |
  | `only NG free`, `inodes NN% used` | real box problems: free disk (`docker image prune -af && docker builder prune -af`), see the RUNBOOK. (Run from a laptop these two rows always FAIL; only the box's own reading counts) |

- [ ] **7.4 Repeat until `preflight: ok`.** **Proves:** every required key is present; no placeholder remains; `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY` and `ADMIN_GATEWAY_HOP_KEY` are at least 32 bytes and the hop key differs from the token; the two MinIO secrets are at least 8 characters;
  the memory decision is recorded. Every other secret got a presence and placeholder check only (step 2.5b prints their lengths). `[STRUCTURAL]`
  **It does not prove** that any credential works.

## Part 8. Deploy

- [ ] **8.1 Deploy** (`[laptop]`, in your quiet window; `[box: deploy]` second terminal running `vmstat 5`):

  ```bash
  make deploy HOST=deploy@20.219.18.84 CONFIRM=edge.opsgrids.com REF=origin/release/backlog-complete    # the same REF as steps 0.1 and 7.1
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

  **Razorpay:** `401` = stopped at the gateway (not reachable); `400` = reached payment (a bad signature is a 400 and nothing is recorded); `404` = the route is missing.
  **WhatsApp:** with the webhook fix deployed (app-gateway `WEBHOOK_PUBLIC_PATHS`, notification mounted under `/api/v1`; see `docs/whatsapp/webhooks.md`), **`403`** is the expected answer: the request reached notification and `hub.verify_token=x` did not match. `401` = stopped at the gateway and `404` = no such path on notification, both meaning the fix is not what is running. Meta's callback URL is `https://<host>/api/v1/webhooks/whatsapp`. Before that fix this URL could only answer `401` or `404`.
  Results: `_____ / _____`.
  Only if Razorpay answers `400` is it sensible to configure that callback URL at Razorpay and then **`[LIVE: owner]`** confirm with a real provider delivery. If `401`, leave webhooks unconfigured and raise it as a code decision.
- [ ] **9.8 `[LIVE: owner]` SMTP end to end** (if not done in 4.1): run the probe; then wait for the first alert or the 08:00 UTC digest and confirm both recipients received it and `/status` shows `last_send_at`.
- [ ] **9.9 Image uploads only (MinIO + document).** If this box sells Storefront images, the RUNBOOK's steps apply (INT:rb:277): reconcile the `core` permissions **dry run first**
  (`make edge-reconcile HOST=... CONFIRM=... PRODUCTS=core`, then `... APPLY=1`; **the APPLY step is a fleet write: every organization's permission version bumps and every member is signed out of their cached token** (INT:rb:335-338), so it is an owner decision: choose the hour), republish the bundles (`make edge-web HOST=... CONFIRM=... APP=buyer` and `... APP=storefront`) so `PUBLIC_ASSET_BASE_URL` is baked in,
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

## Appendix A. The two preflight gotchas (fixed on `ce35c77`, still live on any earlier ref) and the quoting gaps that remain

The owner's two known defects. Both are **fixed on integration since `ce35c77`** (the memory-headroom merge) and **present on `193b529` and earlier**; which ref you deploy decides. They are reproduced in the matrix, section 13 (runs R7a-R7c). Item 2 also lists the quoting mistakes that even the fixed preflight does not catch (matrix section 13.6, runs Q1-Q8).

1. **The Caddyfile catch-all row fails every run.** On `193b529` and earlier the catch-all site line `https:// {` is read by preflight's `sed` as an empty site name, so `FAIL the Caddyfile serves  but TLS_PLATFORM_HOSTS does not list it` appears whatever the values are and `make deploy` refuses at its first step.
   No `.env` value fixes it. Workaround: deploy a ref that contains the fix (`git merge-base --is-ancestor ce35c77 <ref>`); do not edit preflight on the box.
2. **`.env` quoting mistakes (unquoted whitespace exits 127; `$` and unbalanced quotes are worse).** `.env` is `source`d as bash and parsed separately by docker compose, so `OPS_ALERT_EMAIL=info@opsgrids.com and zionna@opsgrids.com` or `EDGE_PROFILES=inventory, storefront`
   runs the words after the first space as a command: on `193b529` `./.env: line N: and: command not found`, exit 127, no `preflight: FAILED` footer. Since `ce35c77` preflight names an unquoted space, `; & | < > ( )`, a backtick or `$(`, text after a closing quote and CRLF as a FAIL before anything is sourced.
   **It still does not catch, and each was reproduced in a scratch tree:** a `$` in a double-quoted or unquoted value (`"ab$cd"` aborts with `cd: unbound variable`; `"ab$(cmd)"` **runs the command**), a backtick inside double quotes (it runs), and an unbalanced quote
   (`unexpected EOF while looking for matching`, exit 2, no footer). **Rules:** spaces or `; & | < > ( )` -> **double** quotes; `$`, a backtick or a backslash -> **single** quotes (`'ab$cd'` is literal in bash and in compose); a value that also holds a single quote -> regenerate it;
   close every quote (step 2.2 has a check); `#` starts a comment only after whitespace; hex secrets need no quotes at all. Use the comma form for `OPS_ALERT_EMAIL`. For the old defect the workaround is to quote, or to deploy a ref that has the pre-check.
3. Also: a `make edge-preflight` runs the *checked-out* ref (use `ssh-deploy.sh ... <ref> preflight`, step 7.1); a laptop cannot run preflight meaningfully (disk, inode and `date` rows are machine-specific: matrix section 13.3).

## Appendix B. How each provider credential is verified, and by whom

Nothing in the "live" column has been run. **A green preflight never appears in the "verified in production" column.**

| Credential | Structural check (preflight / shell) | Live call that would verify it (safe) | Who | Sends or changes anything? |
|---|---|---|---|---|
| `SMTP_PASSWORD` (+ username, host, port) | line present; empty allowed; not a placeholder | the SMTP login probe (4.1); then ops-monitor `/status` `last_send_at` after a real alert or the digest | owner | the probe sends no mail; a real alert sends mail to `OPS_ALERT_EMAIL` |
| `BACKUP_S3_*` | pair both-or-neither | `make edge-backup` (9.6) and the RUNBOOK restore drill | owner | uploads objects to the bucket (creates data) |
| `RAZORPAY_KEY_ID` / `RAZORPAY_KEY_SECRET` | empty allowed; both-or-neither; shape checked when `PAYMENT_WIRE=live` (`lib/payment-switches.sh`) | the owner-run `tools/payments_smoke_razorpay.py` with TEST keys on a laptop (never on the box, never in CI) | owner (provider-side) | creates one test-mode Order with a unique receipt; no money moves |
| `RAZORPAY_WEBHOOK_SECRET` | present, not `dev-only-*` | a Razorpay-signed delivery accepted by the service, after step 9.7 shows the route is reachable | owner | Razorpay would deliver a test event |
| `WHATSAPP_ACCESS_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` | empty allowed | the read-only Graph `GET /{id}?fields=id` (4.4) | owner | read-only call to Meta |
| `WHATSAPP_APP_SECRET`, `WHATSAPP_VERIFY_TOKEN` | empty allowed | Meta's subscribe handshake and a genuine signed delivery, after 9.7 | owner | Meta calls our endpoint |
| `MINIO_*`, `DOCUMENT_S3_*` | present and not a placeholder; >= 8 characters for `MINIO_ROOT_PASSWORD` and `DOCUMENT_S3_SECRET_KEY` only | `minio-init` exits 0 and an upload round trip (RUNBOOK 'Verify an upload end to end') | operator | creates a test asset |
| `INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY`, `ADMIN_GATEWAY_HOP_KEY` | present, not a placeholder, >= 32 bytes; the hop key differs from the token | none: locally generated secrets have no external verifier; every service starting in production is the signal (each refuses a short or `dev-only-` value) | n/a | n/a |
| `ENGAGE_UNSUBSCRIBE_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD`, `RABBITMQ_PASSWORD` | present and not a placeholder **only: no length or strength check** (step 2.5b prints lengths) | none for the first two: a `dev-only-` or weak `ENGAGE_UNSUBSCRIBE_SECRET` stops no service (campaign signing is switched off at the feature level or runs on a weak key), so "the services started" is **not** a signal for it; the Postgres and RabbitMQ passwords are proved only by the services logging in | n/a | n/a |
| the JWT signing key file (`JWT_PRIVATE_KEY_HOST_PATH`) | the file exists with mode 400/440/600/640; the PEM is not parsed | auth starting (the only downstream signal) | operator | none |
| `EDGE_RESERVED_IP`, `SENTRY_DSN` | the line is present (empty allowed); an RFC 5737 documentation address only warns | `EDGE_RESERVED_IP`: compare `dig +short edge.opsgrids.com` with the Azure portal (Static?). `SENTRY_DSN`: none, it is inert in this release | owner | none |
