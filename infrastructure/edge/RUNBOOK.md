# The edge VM — runbook

One Linux VM serves every merchant's storefront and every other product this platform sells:
Caddy, Postgres+PostGIS, Redis, RabbitMQ, and one compose profile per product (45 containers
with every profile on — see [the memory budget](#the-memory-budget) and ADR-0013's memory-budget
erratum, not "five service containers plus one sweep", which was true only of slice 1). ADR-0012
§1 is the decision and its reasoning; this file is what you do about it at 3am.

**Read this first, once, before the box exists.** The parts you will actually need in an
incident are [alerts and what each means](#alerts-and-what-each-means), [roll back a bad
deploy](#roll-back-a-bad-deploy), [the box is out of disk](#the-box-is-out-of-disk), [the memory
budget](#the-memory-budget), [restore from backup](#restore-from-backup) and [is it the app, the
database, Caddy or DNS](#is-it-the-app-the-database-caddy-or-dns).

---

## What is true about this box

- **It is production and there is no second one.** Once one real merchant's domain resolves
  here, this VM cannot be rebooted for convenience. Every restart is a full outage of every
  merchant's shop.
- **A failed build does not take the site down; a deploy still does, briefly.** `make deploy`
  builds every image *before* it recreates any container, so a build error leaves the running
  site exactly as it was. The recreate that follows is a few seconds of downtime per changed
  container, and longer if Caddy itself changed. **This is not zero downtime and slice 1 does
  not pretend to be.** Blue/green is slice 2's problem and needs a second box.
- **A human is paged, by email, within a few minutes of most things going wrong.**
  `ops-monitor` (C10) polls outbox lag, DLQ depth, API readiness, disk, backup freshness and
  certificate expiry every 60s and emails `OPS_ALERT_EMAIL` when one fires — see
  [alerts and what each means](#alerts-and-what-each-means). What it does **not** cover:
  Caddy's own TLS handshake failing on a name nothing else is watching (still Let's Encrypt's
  expiry email, the same as before) and anything that needs a human's judgment rather than a
  threshold.
- **No secret is in the repository.** Everything secret lives in `infrastructure/edge/.env` on
  this box and in the key file it points at, both mode 600, neither ever committed, printed in
  CI, or pasted into a ticket.

## Everything you can type

All of these run from a clone on your laptop, and **all of them require you to name the box**.
`HOST` is an ssh target; `CONFIRM` must equal the `EDGE_HOST` the box records in its own
`.env`, so a command aimed at the wrong machine fails on the first ssh and changes nothing.

| Command | What it does |
|---|---|
| `make deploy HOST=… CONFIRM=… [REF=origin/main]` | preflight → build → `caddy validate` → migrate → recreate → health |
| `make edge-preflight HOST=… CONFIRM=…` | Every check the deploy would run, changing nothing |
| `make edge-validate HOST=… CONFIRM=… [REF=…]` | Puts a ref on the box and loads the Caddyfile with the pinned Caddy. Restarts nothing |
| `make edge-status HOST=… CONFIRM=…` | What is running, which ref, how much disk is left, and `ops-monitor`'s own `/status` (every check's current state) |
| `make edge-logs HOST=… CONFIRM=… [SERVICE=caddy] [TAIL=500]` | Tail logs |
| `make edge-rollback HOST=… CONFIRM=… [TO=<sha>]` | Redeploy the previous ref that actually served |
| `make edge-web HOST=… CONFIRM=… [PLACEHOLDER=1]` | Build the buyer bundle here, ship it, swap the symlink |
| `make edge-web-rollback HOST=… CONFIRM=…` | Point the edge back at the previous bundle |
| `make edge-releases HOST=… CONFIRM=…` | Which bundles are on the box, which one is live |
| `make edge-backup HOST=… CONFIRM=…` | Run the nightly backup now |
| `make edge-reconcile HOST=… CONFIRM=… PRODUCTS=core [APPLY=1]` | Widen existing orgs' roles with new permission codes from a product's manifest — dry run without `APPLY=1` |

On the box itself everything is `infrastructure/edge/remote.sh <command>` in
`/srv/opsgrids/repo`, and raw compose is
`docker compose -f compose.prod.yml --env-file .env …` from `infrastructure/edge/`.

---

## Standing up a new box

Roughly 45 minutes, most of it waiting for a build. Steps 1–6 are one-time.

### 1. The VM and its address

**Size against `infrastructure/edge/memory-budget.json` for the profiles you actually intend to
run, not by habit.** This platform's own production box is 2 vCPU / 3.8 GB RAM (+4 GB swapfile)
and runs every profile it sells on that — tight (`preflight.sh`'s memory-budget check warns at
this size; see [the memory budget](#the-memory-budget)), but running, and the owner has declined
to resize it. A CX32-class box (4 vCPU / 8 GB / 80 GB) or a DigitalOcean 4 GB droplet,
**Ubuntu 24.04 LTS**, gives comfortable headroom for the same profile list if you are standing up
a fresh box and can choose its size; a smaller one is workable if you also trim
`COMPOSE_PROFILES` to match — `preflight.sh` will tell you which, and refuse rather than guess if
you get it wrong.

**Attach a reserved (floating) IPv4 before you do anything else.** Apex merchants put this
address in their own zone file, so it becomes part of the platform's public contract. A
reserved IP costs a few dollars a month and means rebuilding this VM never requires fifty
merchants to edit DNS. Skipping it is the cheapest mistake in this design to make and the most
expensive one to unmake.

Provider firewall: allow inbound **22, 80, 443** (TCP) and **443/UDP** if you want HTTP/3.
Nothing else. Port 80 is not optional — ACME HTTP-01 challenges arrive there, and blocking it
means no certificate is ever issued.

### 2. DNS for the platform zone

Three records, by hand, once. **DNS only — do not proxy them through Cloudflare.** A proxied
record terminates TLS at Cloudflare and our ACME challenge never arrives.

| Name | Type | Value |
|---|---|---|
| `edge.opsgrids.com` | A | the reserved IP |
| `api.opsgrids.com` | A | the reserved IP — the gateway answers `/api/*` on every host this box serves |
| `app.opsgrids.com` | A | the reserved IP — the dashboard (ADR-0013 §4): sign up, sign in, the marketplace |
| `ops.opsgrids.com` | A | the reserved IP — the Inventory product's app (one hostname per product, ADR-0001) |
| `*.stores.opsgrids.com` | A | the reserved IP |
| `people.opsgrids.com` | A | the reserved IP — OpsGrids People's app |
| `revenue.opsgrids.com` | A | the reserved IP — OpsGrids Revenue's app |
| `pulse.opsgrids.com` | A | the reserved IP — PulseBoard's app |
| `projects.opsgrids.com` | A | the reserved IP — OpsGrids Projects's app |
| `support.opsgrids.com` | A | the reserved IP — OpsGrids Support's app |
| `marketing.opsgrids.com` | A | the reserved IP — OpsGrids Marketing's app |
| `finance.opsgrids.com` | A | the reserved IP — OpsGrids Finance's app |
| `it.opsgrids.com` | A | the reserved IP — OpsGrids IT's app |
| `store.opsgrids.com` | A | the reserved IP — OpsGrids Storefront's app |
| every name above | AAAA | the VM's IPv6, if it has one |

`opsgrids.com` (the marketing site, on Vercel) and `auth.` are ADR-0001's and are not touched
here. **Every platform name in this table must also be in `TLS_PLATFORM_HOSTS`** in the
box's `.env`, except the wildcard: a store's own subdomain is authorised by the store lookup,
but `edge.` and `api.` are nobody's store, and the issuance gate refuses any name it cannot
account for.

### 3. Docker, and a user to deploy as

```bash
ssh root@<reserved-ip>
apt-get update && apt-get install -y ca-certificates curl git rsync
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update && apt-get install -y docker-ce docker-ce-cli containerd.io \
  docker-buildx-plugin docker-compose-plugin

adduser --disabled-password --gecos "" deploy
usermod -aG docker deploy
mkdir -p /home/deploy/.ssh && cp ~/.ssh/authorized_keys /home/deploy/.ssh/
chown -R deploy:deploy /home/deploy/.ssh && chmod 700 /home/deploy/.ssh

mkdir -p /srv/opsgrids/{repo,web/releases,secrets,backups,state}
chown -R deploy:deploy /srv/opsgrids
```

Put **your own** ssh public key in `/home/deploy/.ssh/authorized_keys`. Deploys are made with
an engineer's own key from their own agent; there is no shared deploy key and no CI secret.

### 4. The repository

```bash
ssh deploy@<reserved-ip>
git clone <repo-url> /srv/opsgrids/repo
```

A private repository needs a **read-only** deploy key on the box:
`ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -N ""`, add the public half to the repository's
deploy keys with write access **off**, then clone over ssh.

### 5. Secrets — generate them here, now, and never anywhere else

```bash
cd /srv/opsgrids/repo/infrastructure/edge
cp .env.example .env
chmod 600 .env

# Generate one value per secret. Never reuse a development value; never paste one from chat.
openssl rand -hex 32     # POSTGRES_PASSWORD
openssl rand -hex 32     # OPSGRIDS_DB_PASSWORD   (alphanumeric only — it goes into a URL)
openssl rand -hex 32     # INTERNAL_SERVICE_TOKEN
openssl rand -hex 32     # PII_ENCRYPTION_KEY
openssl rand -hex 32     # BACKUP_S3_SECRET_ACCESS_KEY, if your provider does not issue one
openssl rand -hex 16     # MINIO_ROOT_USER
openssl rand -hex 32     # MINIO_ROOT_PASSWORD

# The only private signing key in the platform.
openssl genpkey -algorithm ed25519 -out /srv/opsgrids/secrets/jwt-ed25519.pem
chmod 600 /srv/opsgrids/secrets/jwt-ed25519.pem
```

Then edit `.env`: fill every empty value, set `EDGE_HOST` to this box's name, `EDGE_RESERVED_IP`
to the real address, `TLS_PLATFORM_HOSTS` to every platform name from the DNS table above
(comma-separated, no wildcard), `ACME_EMAIL` to **an inbox a human reads** (Let's Encrypt's
expiry warning is still the only channel for a *merchant* domain's renewal trouble — see
[alerts and what each means](#alerts-and-what-each-means)), and `OPS_ALERT_EMAIL` to the same
kind of inbox — that is where `ops-monitor` sends everything else. Neither is a secret; both go
unread at the platform's expense.

`BACKUP_S3_ACCESS_KEY_ID` and `BACKUP_S3_SECRET_ACCESS_KEY` may both be left empty to stand the
box up before a bucket exists. Preflight warns in capitals and `backup.sh` exits non-zero every
night until they are set; that is the reminder, and it stops being acceptable the day the first
real store is published.

**Which values must be fresh, and which must never be reused:**

| Value | Rule |
|---|---|
| `POSTGRES_PASSWORD`, `OPSGRIDS_DB_PASSWORD` | Generated on this box. Never the development `opsgrids`, which is what `tools/dev-bootstrap/init-db.sql` creates every role with before `postgres-init/30-role-passwords.sh` replaces it |
| `INTERNAL_SERVICE_TOKEN` | Generated fresh, ≥32 bytes. It signs the tokens one service presents to another; the development value is public in `.env.example` and `platform_core` refuses to start production with any `dev-only-` value |
| `PII_ENCRYPTION_KEY` | Generated fresh, ≥32 bytes, **and backed up off this box before any data exists**. Lose it and encrypted PII is unreadable; there is no recovery |
| `jwt-ed25519.pem` | Generated on this box. Never `make devkeys`' key — that one is on every developer's laptop, and anyone holding it can mint a token for any tenant |
| `BACKUP_S3_*` | Scoped to write into the backup bucket and nothing else |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` | Generated on this box, only exercised when `COMPOSE_PROFILES` includes `storefront`. `document` reuses the same credential — see "Document and MinIO" below |
| `ACME_EMAIL` | Not a secret |

Nothing above ever goes into git, a CI log, a screenshot, or this file.

The container runs as a non-root user, so the key file must be readable by it:

```bash
uid=$(docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T \
        --entrypoint id auth -u)
sudo chown "$uid" /srv/opsgrids/secrets/jwt-ed25519.pem
```

### 6. First deploy

**Two things nobody has been able to verify from a laptop, and this is where they get
verified.** Neither can be checked without the network and the box:

- **The Caddyfile has never been through `caddy validate`.** No Caddy binary and no registry
  access exists in the development environment it was written in. The deploy runs
  `caddy validate` against the pinned image before it restarts anything, so an invalid config
  aborts the deploy — on a first deploy that means nothing comes up, and the fix is to correct
  the file and deploy again. Run `make edge-validate HOST=… CONFIRM=…` on its own first.
- **The pinned Caddy version is `caddy:2.10.0-alpine`** and the exact `on_demand_tls` options
  it accepts have not been checked against it (ADR-0012 §17 says to do this rather than write
  them from memory). If the tag does not exist the build fails with a manifest error; if the
  options have moved, `caddy validate` says which. After the first successful deploy, record
  `docker compose exec caddy caddy version` in this file.

From your laptop:

```bash
make edge-preflight HOST=deploy@<ip> CONFIRM=edge.opsgrids.com   # expect: preflight: ok
make deploy         HOST=deploy@<ip> CONFIRM=edge.opsgrids.com
```

The first build takes five to ten minutes. When it finishes, publish a bundle:

```bash
# If frontend/product-storefront/buyer.html exists:
make edge-web HOST=deploy@<ip> CONFIRM=edge.opsgrids.com
# If it does not yet — serve the explicit placeholder rather than a merchant's admin build:
make edge-web HOST=deploy@<ip> CONFIRM=edge.opsgrids.com PLACEHOLDER=1
```

### 7. Prove it works, in this order

```bash
curl -si http://<reserved-ip>/_edge/health            # plain HTTP, no TLS: "ok"
curl -si https://edge.opsgrids.com/_edge/health       # TLS on a platform name
curl -si https://edge.opsgrids.com/api/v1/auth/health # through Caddy to the gateway to a service
```

Then a real store: create one, publish it, and open
`https://<slug>.stores.opsgrids.com`. The first request for a new name waits two to five
seconds while Caddy obtains a certificate; that is on-demand TLS working, not a fault.

Then `make edge-status HOST=… CONFIRM=…` and read the `ops-monitor` block at the bottom. On a
box this fresh, expect `backup: "no marker yet"` (step 8 has not run yet) and everything else
either `ok` or still `pending` (fewer than 3 polls old) — `firing` this early usually means a
profile is enabled in `COMPOSE_PROFILES` whose API isn't actually up yet.

### 8. The nightly backup

```bash
ssh deploy@<ip>
( crontab -l 2>/dev/null
  echo '17 3 * * * /srv/opsgrids/repo/infrastructure/edge/backup.sh >> /srv/opsgrids/state/backup.log 2>&1'
) | crontab -
crontab -l          # read it back
```

Then run one by hand and read the output: `make edge-backup HOST=… CONFIRM=…`. **A backup you
have not restored is not a backup** — do the [restore drill](#restore-from-backup) once now,
while nothing is at stake.

### 9. Document and MinIO (uploaded images, ADR-0009)

The storefront console's image upload (`POST /api/v1/documents/assets`) needs two more
containers, `document` and `minio`, both behind the `storefront` compose profile — see the
comments on `minio` and `document` in `compose.prod.yml` for why they are profile-gated rather
than always on. **Skip this section if this box does not sell Storefront.**

**Add to the box `.env` (names only — see `.env.example` for what each one is and why):**

- `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD` — generate fresh, same rule as every other secret
  in this file. `MINIO_ROOT_PASSWORD` must be at least 8 characters — MinIO's own minimum —
  and preflight now enforces it.
- `DOCUMENT_S3_ACCESS_KEY`, `DOCUMENT_S3_SECRET_KEY` — generate fresh. The scoped credential
  `document` actually connects with; `minio-init` creates this user and a policy granting it
  only `s3:PutObject`/`s3:DeleteObject` on this bucket, never the root account. Same 8-character
  minimum, same preflight check.
- `S3_PUBLIC_BUCKET` — the committed default (`opsgrids-public`) is fine unless you have a
  reason to rename it.
- `PUBLIC_ASSET_BASE_URL` — must be **exactly** `https://<this box's own EDGE_HOST>/<S3_PUBLIC_BUCKET>`
  — not merely a name in `TLS_PLATFORM_HOSTS`. Caddy proxies `/opsgrids-public/*` to MinIO only on
  EDGE_HOST's own site block, deliberately, so that a stray `response-content-type` on an
  uploaded object can never be served back as HTML on a platform hostname carrying the
  `.opsgrids.com`-scoped refresh cookie (ADR-0009 addendum, 2026-09-23). The committed default
  already matches `EDGE_HOST`; change both together if you rename the box. Preflight refuses to
  deploy if these two values disagree.
- `COMPOSE_PROFILES` must list `storefront` (it already does if this box sells the product).

**Merging this without the values above set on the box is safe and boring, not a surprise
outage**: preflight refuses to deploy the moment any of them is missing, empty or still a
placeholder, and a preflight failure changes nothing — the box keeps serving whatever ref it was
already on. Nobody's shop goes down; the *next* deploy simply does not proceed until these are
set. `make edge-preflight HOST=… CONFIRM=…` shows exactly which key.

**Order.** No separate step for these two containers: the Deploy workflow (green `main`, or
*Run workflow*) already does the right thing once the values above are in `.env` — it runs the
same `remote.sh` steps `make deploy` does. The permission reconcile (below) is a genuinely
separate, deliberate step and is never run automatically.

1. `preflight` fails loudly if any of the required keys are missing, empty or still a
   placeholder — the same blanket rule as every other key in `.env.example`.
2. `build` builds the `document` image alongside everything else.
3. `migrate` runs `document`'s Alembic migrations — it is in `remote.sh`'s
   `SERVICES_WITH_SCHEMA` list, so this needs no manual step.
4. `up` starts `minio`, then `minio-init` (a one-shot: creates the `opsgrids-public` bucket and
   its anonymous-GetObject policy, then the scoped `document` user and its
   put/delete-only policy — all idempotent on every later deploy), then `document`.
5. **Reconcile the `core` permissions before anyone can upload in an organization that already
   existed before this deploy** (ADR-0009 §4, ADR-0007). `core.content.asset.read` and
   `core.content.asset.write` are two permissions added to the `core` manifest;
   `install_registration` skips role codes that already exist, so `ORG_OWNER`/`ORG_ADMIN` in
   every organization created *before* this deploy keep exactly the grants they had — every
   upload 403s for them — while a brand-new organization works perfectly. **Dry run first,
   always**:

   ```bash
   make edge-reconcile HOST=deploy@<ip> CONFIRM=edge.opsgrids.com PRODUCTS=core
   # read the diff, then:
   make edge-reconcile HOST=deploy@<ip> CONFIRM=edge.opsgrids.com PRODUCTS=core APPLY=1
   ```

   or, from the Deploy workflow's *Run workflow* form, set `reconcile: core` — that path applies
   directly (no separate dry run in the pipeline), so run the dry run above by hand first if you
   want to see the diff before it is written. This is a fleet write (every organization's
   permission version bumps, which signs every member out of their cached token) and is
   deliberately never part of an ordinary `make deploy`.
6. **Publish (or republish) the `buyer` and `storefront` bundles after this deploy, not
   before**: run the Deploy workflow with `apps: buyer,storefront` (it calls the same
   `publish-web.sh publish` that `make edge-web APP=…` does). `publish-web.sh` reads `PUBLIC_ASSET_BASE_URL` off the box's `.env` at
   build time and bakes it in as `VITE_ASSET_BASE_URL` — Vite bakes `import.meta.env.VITE_*`
   in at build time, so this cannot be fixed by redeploying the box alone. If either bundle was
   published **before** this box had `PUBLIC_ASSET_BASE_URL` set, every uploaded image 404s on
   the live store until it is republished, even though the upload itself succeeds — republish
   now if in doubt.
7. **Verify the backup, not just the deploy.** The next nightly `backup.sh` run now also tars
   MinIO's data volume and uploads it alongside the Postgres dump and Caddy's certificates
   (skipped automatically on a box that does not run `minio`). Don't wait for cron: run
   `make edge-backup HOST=… CONFIRM=…` once by hand right after this deploy and read its output
   for `object storage: …, uploaded` — and do the [restore drill](#restore-from-backup) at least
   once so "we have a backup" is a tested fact about this box, not an assumption about the
   script.

**Verify an upload end to end**, from a merchant's own signed-in session or with a token by hand:

```bash
# 1. Upload. Any small JPEG/PNG/WebP under 5 MiB.
curl -sS -X POST https://edge.opsgrids.com/api/v1/documents/assets \
  -H "Authorization: Bearer $TOKEN" -F file=@sample.jpg | tee /tmp/asset.json

# 2. The response's "public_path" looks like /a/<org-uuid>/<asset-uuid>. Fetch it back through
#    the same path Caddy proxies to MinIO — this is what a buyer's <img> actually does, not
#    document's own preview "public_url" field, which points at the same address anyway.
org_and_asset=$(python3 -c "import json,sys; print(json.load(open('/tmp/asset.json'))['public_path'][3:])")
curl -sSI "https://edge.opsgrids.com/opsgrids-public/$org_and_asset"   # expect: 200, Content-Type: image/...

# 3. Confirm the bucket really cannot be listed (the isolation argument ADR-0009 §3 depends on):
curl -sSI "https://edge.opsgrids.com/opsgrids-public/"                # expect: 403, never a listing

# 4. Confirm a response-content-type override cannot turn an object into HTML on this host
#    (ADR-0009 addendum, 2026-09-23) — expect the object's own true content type, never text/html:
curl -sSI "https://edge.opsgrids.com/opsgrids-public/$org_and_asset?response-content-type=text/html"

# 5. Confirm the SAME path 404s on a platform hostname other than EDGE_HOST — it must NOT proxy
#    there at all:
curl -sSI "https://app.opsgrids.com/opsgrids-public/$org_and_asset"   # expect: 404
```

If step 2 returns 502, `minio` did not start — check `COMPOSE_PROFILES` and
`make edge-logs SERVICE=minio`. If step 1 returns 404, the gateway has no route for
`/api/v1/documents` — check `routes.prod.json` was actually deployed (`ROUTES_SHA` in
`make edge-status`). If step 1 returns 403 for an organization that existed before this deploy,
the permission reconcile (step 5 above) has not run yet.

#### The purge sweep (ADR-0009 §8, and its 2026-09-23 addendum's §9)

`document-cron` is a second container behind the same `storefront` profile as `document` and
`minio` — see the comment on it in `compose.prod.yml`. It is the only place ADR-0009 §8's
30-day retention window and §9's orphan-object pass actually run; nothing about the API
container does either.

**What it does, one tick at a time (default every five minutes, idle-backing-off to thirty):**

1. **Row purge.** Finds every asset, across every organization, whose `deleted_at` is more
   than `ASSET_PURGE_RETENTION_DAYS` (default 30) in the past and whose `purged_at` is still
   null; for each, deletes the object from the bucket and then sets `purged_at`. A storage
   failure leaves the row untouched for the next tick — it never marks a row purged while the
   object might still be there.
2. **Orphan sweep**, only when `DOCUMENT_ORPHAN_SWEEP_ENABLED=true`: lists the whole bucket
   with the second, list-capable credential (`DOCUMENT_S3_ORPHAN_ACCESS_KEY`/`_SECRET_KEY`,
   held only by this container), and for every object at least 24 hours old with **no**
   `assets` row at all — live, soft-deleted or purged — either logs what it would delete
   (`DOCUMENT_ORPHAN_SWEEP_DRY_RUN=true`, the default even once enabled) or actually removes
   it. A referenced object, whatever state its row is in, is never a candidate.

**How to check it — logs, not a dashboard:**

```bash
make edge-logs SERVICE=document-cron
```

- `document.sweep.started` once, at boot, naming the retention window and whether the orphan
  pass is on.
- `document.sweep.pass` only when a tick actually did something — `purged`, `skipped`,
  `failed`, and the four `orphan_*` counters. A quiet box between deploys is a box with no
  soft-deleted assets past their window, not a stopped sweep; `docker compose ps` showing
  `document-cron` running is the "is it alive at all" signal, the same as every other
  background container here.
- `document.sweep.purge_failed` names the organization and asset id a single purge raised on
  — the rest of that tick, and every later tick, still runs; it is not a stuck sweep.
- `document.sweep.tick_failed` means discovery itself failed (a DB restart, a deadlock) —
  logged with a full traceback and backed off; it recovers on its own once the fault clears.
- `document.sweep.orphan_would_delete` / `orphan_deleted` name the organization id, asset id
  and key for every candidate — read a few passes of the dry-run ones before ever setting
  `DOCUMENT_ORPHAN_SWEEP_DRY_RUN=false`.

**Run it once by hand** (useful right after a deploy, or to confirm the retention window
actually cleared a backlog, without waiting for the next poll):

```bash
ssh deploy@<ip>
cd /srv/opsgrids/repo/infrastructure/edge
docker compose -f compose.prod.yml --env-file .env run --rm document-cron python -c "
import asyncio
from app.cron import main
asyncio.run(main())
"
```

That runs `main()` — the same one-tick-then-loop entrypoint `bin/cron` execs — inside a
throwaway container sharing the deployed image and `.env`; press Ctrl-C after the first
`document.sweep.pass` (or `document.sweep.started` with nothing to do) rather than leaving it
running as a second, redundant sweeper. `--rm` means it leaves nothing behind either way.

**Turning the orphan pass on for the first time:**

1. Generate `DOCUMENT_S3_ORPHAN_ACCESS_KEY`/`_SECRET_KEY` into `.env` (see `.env.example`) —
   the next deploy's `minio-init` creates the scoped user and its `ListBucket`+`DeleteObject`
   policy idempotently, same as the write-only pair already there.
2. Set `DOCUMENT_ORPHAN_SWEEP_ENABLED=true`, leave `DOCUMENT_ORPHAN_SWEEP_DRY_RUN=true`, and
   redeploy. Watch `document.sweep.orphan_would_delete` for a day or two.
3. Only once those candidates look right — every one of them genuinely has no row — set
   `DOCUMENT_ORPHAN_SWEEP_DRY_RUN=false` and redeploy. From here it actually calls
   `DeleteObject` on what it lists.

**Known gap this section does not close**: `infrastructure/edge/memory-budget.json` does not
exist on this base — a sibling slice (`slice/memory-budget`) adds it. When that slice merges,
`document-cron` needs a `"document-cron": { "kind": "background", "profile": "storefront" }`
entry alongside `document`'s own, or `tests/infra/test_compose_memory_budget.py` fails by
name for a container this compose file defines with no budget entry.

---

## Deploying

```bash
make deploy HOST=deploy@<ip> CONFIRM=edge.opsgrids.com REF=origin/main
```

**Before you deploy a release that adds `.env` keys, add them to the box's `.env` first.**
Preflight (step 3) treats every key in `.env.example` as required *to be present* — an empty
value is fine for the ones marked optional, a missing line is not — and it refuses the deploy,
before anything changes, when one is absent. The release that introduced `ops-monitor` (C10)
adds four; see [alerts and what each means](#alerts-and-what-each-means) for the exact lines to
paste. Any future release that adds a key to `.env.example` is a deploy prerequisite in exactly
the same way, and its release notes must list the keys.

What happens, in order, and what each step protects:

1. **assert-host** — the box states its own `EDGE_HOST`. A mismatch aborts before anything is
   fetched. This is what makes deploying to the wrong machine loud instead of quiet.
2. **checkout** — the box fetches and checks out `REF`. **Your working tree is never
   deployed.** A dirty checkout on the box aborts the deploy; somebody edited files on the
   server and that needs reconciling before anything else.
3. **preflight** — `.env` complete, no placeholders, the signing key present and readable, disk
   and inodes, docker responding.
4. **build** — images build with the running containers untouched. **A failed build stops here
   and the site keeps serving.**
5. **caddy validate** — the Caddyfile is loaded by the pinned Caddy binary. A Caddyfile that
   fails to load is a total outage and there is no staging environment to catch it.
6. **migrate** — first **reconcile-roles**: create any `opsgrids_<service>` role this box does
   not have yet (idempotent — see "New service on an existing box" below), then
   `alembic upgrade head` for every service this box's profiles run, **before** the new
   containers serve. For the length of the deploy the old code runs against the new schema, so
   **every migration must be backward compatible with the version currently running**: add a
   nullable column, backfill, and only drop the old one in a later deploy. `make
   migration-roundtrip` locally is what proves the `downgrade()` actually reverts.
7. **up** — containers are recreated. Brief downtime. Not zero.
8. **health** — every container running and healthy within 120 s, or the deploy reports a
   failure and leaves the decision to you.

### Bundle entitlement: order, and the one-off backfill

The change that made a `suite` subscription entitle its members (ADR-0004 §5 addendum,
docs/commerce-packaging.md) touches four services, and two of them migrate. Everything is
additive — a nullable column and a new table — so the old code runs safely against the new
schema, but the **order the services come up in** decides whether a bundle sold during the
rollout gets its members or silently does not:

1. **catalog** — validates a bundle's members against product-service when one is authored.
   Migration `c5f1e8a2b7d4` retires the seeded `suite/full` plan, whose `projects` member is
   still `soon`. Until product is up, authoring a *bundle* answers 503; every other plan authors
   as before.
2. **product** — migration `b3d5f7a9c1e2`: `products.is_bundle`, the `suite` registry row
   (`status = soon`), `activation_sources` under a forced tenant policy, and one source row per
   existing activation (so nothing changes state on deploy). Must be migrated and its consumer
   running **before** subscription emits `entitled_products`.
3. **subscription** — migration `a9c4e2b7d1f0`: the nullable `entitled_products` snapshot. Every
   `commerce.subscription.*` event from here carries the member list.
4. **account** — no migration; its consumer installs each member's manifest. An event the *old*
   account consumer handles installs nothing for a bundle (there is no `suite` manifest) and is
   marked processed — so on a split rollout, bring account's consumer up before the first bundle
   is sold. A single `make deploy` recreates all four together, which is fine.

Then, once all four are up, snapshot the rows that predate the column — once, reviewed, like any
other backfill:

```bash
docker compose -f compose.prod.yml --env-file .env run --rm -T subscription \
  python -m app.backfill --dry-run     # prints {"considered": …, "bundles": …}; writes nothing
docker compose -f compose.prod.yml --env-file .env run --rm -T subscription \
  python -m app.backfill
```

For a non-bundle it only writes `[product_key]`. For each bundle it emits
`commerce.subscription.entitlements_resynced`, which product projects (and retires the dead
`suite` activation the old fold left behind) and account installs from. Re-running is a no-op.
`unresolved > 0` means a pinned version no longer resolves in catalog — that row stays NULL and
reads as its own product; look at it by hand. If an account consumer on old code ate a bundle
event, `tools/wire_product.py <org> <member>` installs the missing member's roles.

**Deploy check — every deploy, not just this one.** Re-judge every sellable bundle against the
registry as it now is (a member rolled back to `soon`, a product folded into the platform):

```bash
docker compose -f compose.prod.yml --env-file .env run --rm -T catalog python -m app.bundle_audit
```

Exit 1 means an **offered** bundle names a member the registry cannot vouch for: retire that plan
or launch the member before selling it. A retired plan's problems are printed as warnings and do
not fail the check. Changing a product's `status` on the box is exactly the edit that can make
this fail, so run it after that too.

Bundles are sold only through subscription's operator routes (`POST /internal/v1/subscriptions`,
`…/{id}/change-plan`, service token); the customer route answers 403. A trial's length is the
plan version's `trial_days` feature (14 when absent), never the customer's.

### Deploying from the pipeline

`.github/workflows/deploy.yml` runs the same two scripts a laptop does — `ssh-deploy.sh` for the
services and `publish-web.sh` for the app bundles — so everything above still holds. It has three
ways in:

| Trigger | Deploys |
|---|---|
| CI finishes green on `main` | that commit, and only what it changed (`ci-plan.sh`): the services if anything outside `frontend/`, `docs/` and `e2e/` changed; each app whose package changed; every app if a shared front-end package or the lockfile changed |
| a push to a `deploy/**` branch | that branch head, everything — what `REF=origin/<branch>` was for. CI has **not** vouched for it |
| **Run workflow** in the Actions tab | any ref, with `services` and `apps` to narrow it. **A rollback is a run whose `ref` is an earlier sha** |

One deploy runs at a time and a running one is never cancelled. The app bundles publish after
the services, three at a time, built on the runner — the box only receives files.

It needs two secrets and two variables in the repository settings: `EDGE_SSH_KEY` (the private
half of a key pair in the `deploy` user's `authorized_keys`; give the pipeline its own pair so it
can be revoked without locking a person out), `EDGE_KNOWN_HOSTS` (the box's host key line, so the
runner refuses an impostor), `EDGE_SSH_TARGET` (`deploy@<ip>`) and `EDGE_CONFIRM_HOST` (the box's
`EDGE_HOST` — the same interlock `CONFIRM=` is). The jobs run in the `production` environment;
add a required reviewer there if a human should approve each deploy.

What the pipeline does **not** do: edit the box's `.env`, change a product's catalogue `status`,
or touch DNS. Those are the deployment's own data and stay a person's decision.

### New service on an existing box

`tools/dev-bootstrap/init-db.sql` (mounted as `postgres-init/20-init.sql`) creates every
`opsgrids_<service>` role — but only on a **fresh** Postgres data volume, because the docker
entrypoint runs `/docker-entrypoint-initdb.d/*` exactly once, the first time a volume
initialises. A service added after that first boot has no role at all: its migration's own
`CREATE POLICY ... TO opsgrids_<service>` or `REVOKE ... FROM opsgrids_<service>` fails outright,
and `set -euo pipefail` aborts the whole deploy on it — this is exactly how the `leave` service's
first deploy broke, on a box that had been running since before `leave` existed.

`remote.sh migrate` now runs **reconcile-roles** first, every deploy: for every service in
`SERVICES_WITH_SCHEMA`, idempotently create the role if it is missing, set its password to this
box's `OPSGRIDS_DB_PASSWORD` (never the bootstrap's dev placeholder), and grant it `CONNECT` and
the schema `USAGE` the bootstrap grants at first boot — the same three statements
`tools/dev-bootstrap/init-db.sql` runs for that role, built once in
`infrastructure/edge/lib/role-reconcile.sh` so there is one definition of what a per-service role
needs, not a copy that can drift from the one a fresh volume gets. Safe to run on every deploy,
not only the one that adds a service — once every role exists it is a handful of `SELECT`s and
no-op `GRANT`s.

Run it by hand if you ever need to (a role deleted by mistake, standing up a service against an
existing box out of band):

```bash
./remote.sh reconcile-roles
```

The dev counterpart is `make reconcile-roles`, for the same problem on a local `make infra`
volume that predates a service — it reads the same `SERVICES_WITH_SCHEMA` list out of
`remote.sh` and reuses the identical SQL, against the dev password.

### Roll back a bad deploy

**The application:**

```bash
make edge-rollback HOST=deploy@<ip> CONFIRM=edge.opsgrids.com
# or to a specific commit:
make edge-rollback HOST=deploy@<ip> CONFIRM=edge.opsgrids.com TO=<sha>
```

This is a full deploy of the previous ref that actually served — same preflight, same build,
same validate, same recreate. It takes as long as a deploy did, because it is one. There is no
faster path in slice 1: images are built on the box, so there is no previous image tag to point
at. If that is too slow to live with, that is slice 2's registry, and this is its trigger.

**The bundle** (a broken storefront page, but the API is fine) is much faster — one symlink:

```bash
make edge-web-rollback HOST=deploy@<ip> CONFIRM=edge.opsgrids.com
```

**A migration** is the one thing rollback does not undo for you. `make edge-rollback` runs
`alembic upgrade head` at the *older* ref, which is a no-op — it does not downgrade. If the
schema change is what broke things:

```bash
ssh deploy@<ip>
cd /srv/opsgrids/repo/infrastructure/edge
docker compose -f compose.prod.yml --env-file .env run --rm -T storefront alembic downgrade -1
```

Then roll the application back. **Think before typing that**: a downgrade that drops a column
destroys the data in it, and the last backup is up to 24 hours old. If the bad migration only
*added* something, leaving it in place and rolling back the code is nearly always the safer
call.

---

## Alerts and what each means

`ops-monitor` (C10) is one always-on container, `python -m platform_core.ops_monitor` in the
same `product` image `edge-relay` reuses. Every 60s it runs six checks, feeds the results
through a small state machine (`libs/platform-core/src/platform_core/ops_monitor/state.py`) and
emails `OPS_ALERT_EMAIL` — direct SMTP, reusing the box's own `EMAIL_FROM`/`SMTP_*`,
**deliberately not through notification-service**, which is one of the things being watched.

**Deploy prerequisite — four new `.env` keys (release notes).** `preflight.sh` requires every
key in `.env.example` to be *present* in the box's `.env` (it reads the key names from the
example, so adding one there is what makes it required), so **a box that does not have these
four lines will have its deploy refused at preflight, before anything changes.** Add them to
`infrastructure/edge/.env` **before** deploying this release — empty values are accepted for
all four:

```
OPS_ALERT_EMAIL=
OPS_MONITOR_API_CONTAINERS=
OPS_MONITOR_DLQ_QUEUES=
SMTP_USE_SSL=false
```

What each means (`.env.example` has the full comment for each): `OPS_ALERT_EMAIL` (where alerts
go — empty means alerts are logged only), `OPS_MONITOR_API_CONTAINERS` and
`OPS_MONITOR_DLQ_QUEUES` (empty means derive from `COMPOSE_PROFILES`, which already exists;
`COMPOSE_PROFILES=*` is understood too), and `SMTP_USE_SSL` (defaults `false` — implicit TLS on
465 instead of STARTTLS on 587, for a provider that only offers SMTPS). Their *values* are
optional, their *presence* is not: a box with all four empty still runs every check and still
logs `ops.alert.firing`, it just pages nobody until `OPS_ALERT_EMAIL`/`SMTP_HOST` are filled in.

**Hysteresis, so a single blip does not page anyone:** a check needs 3 consecutive failing
polls (about 3 minutes) before it *opens* and sends one email; once open it *reminds* every 6
hours for as long as it keeps failing; 3 consecutive good polls *resolves* it and sends one more
email. Every check that changes state in the same poll goes out in **one** email, not one each.
A check that never fails 3 *in a row* but fails often enough — 3 of its last 5 polls, by
default — still opens: pure consecutive counting would never catch a flapper that fails,
passes, fails, passes, fails.

**Delivery is retried, capped, and never claimed before it happens.** A failed send (SMTP
down) does not consume the state machine's "notified" bookkeeping — the next poll retries
immediately, for up to 5 consecutive cycles, before falling back to the normal cadence so a
long SMTP outage is not hammered forever. A rolling cap (12 emails/hour by default) throttles
the other direction: a box genuinely falling apart pages once per email, not once per check per
minute. What the cap holds back is queued and goes out, named check by check, in the next email
that fits under it.

The rules live in one pure function (`ops_monitor/delivery.py`, `plan_delivery`), and are worth
knowing at 3am: (1) a check not reported firing in the last hour, and the daily digest, are
**exempt from the cap** — a *new* problem is never held back because reminders used the budget;
a check that re-fires within the hour of its last report is throttled like any reminder — and so
is its "resolved" — so one flapping check cannot page you every few minutes, nor starve a
brand-new check of its first alert. (2) Exempt notices have their own ceiling of twice the cap
per hour, so they are generous, not unlimited. (3) A newer notice for a check replaces an older
queued one — a queued "resolved" is dropped the moment its check fires again, so you are never
told "all clear" about something that is failing right now. A "resolved" that replaces an
opening which was **never emailed** (held back by the cap or a failed send) says so, in so many
words, and carries the opening's own detail; a first alert whose send failed still reads OPENED,
not "still firing", when it finally goes out. (4) A reminder for
a check nobody has ever successfully been told about is treated as a first alert. (5) The queue
is **in memory only** (200 entries, oldest dropped): restarting `ops-monitor` empties it. A
queued firing/reminder comes back by itself — its "notified" bookkeeping was never committed, so
the next cycle sees it as due again — but a queued *resolved* is simply lost (the check is OK
again and `/status` says so; nothing regenerates the email). That is a deliberate trade against
persisting a second file that could itself go stale.

| Check | Fires when | Where to look |
|---|---|---|
| `outbox.<schema>` | oldest unpublished row ≥ 900s (warns at 300s, in `/status` but not emailed), or any row has exhausted its attempts | `make edge-logs SERVICE=edge-relay`; `tools/outbox.py --service <schema> --reset` **after** fixing the cause, not instead of it |
| `dlq.<queue>` | one or more messages parked on `{queue}.dlq` | `tools/dlq.py peek --queue <queue>`; `tools/dlq.py replay ...` after fixing the cause |
| `api.<name>` | `GET /health/ready` failed on 3 consecutive polls | `make edge-logs SERVICE=<name>` — usually its database or Redis is unreachable, which `/health/ready`'s own body already names |
| `disk` | less than 15% free on `/` | [the box is out of disk](#the-box-is-out-of-disk) |
| `backup` | no successful `backup.sh` run in the last 26h. **"no marker yet" is its own state, not this alarm** — a box that has never backed up successfully (or predates this check) says so plainly in `/status` without emailing anyone | run `make edge-backup` by hand and read its output; RUNBOOK's [restore from backup](#restore-from-backup) |
| `cert` | the TLS handshake to `EDGE_HOST` itself (not a merchant domain — see below) expires in under 14 days | see "Certificates" below |
| a daily digest, `check: "*"` | once a day at a fixed hour, **only when nothing is firing** | not a problem — it is the dead-man's switch: a day with no digest and no other mail is a stopped monitor, not a quiet one |

**How to check it yourself, any time:** `make edge-status HOST=… CONFIRM=…` prints `ops-monitor`'s
own `/status` — every check's current state, detail and how long it has been that way — alongside
everything else that command already showed. `make edge-logs HOST=… CONFIRM=… SERVICE=ops-monitor`
is its own log; `ops_monitor.notification` is the line for every email actually sent.

**If nothing is delivered at all:** `/status`'s `email` field says `"disabled — SMTP_HOST/
OPS_ALERT_EMAIL/EMAIL_FROM not set"` in so many words, and `preflight.sh` warns about the same
gap in capitals before every deploy. Every firing check is still logged at error
(`ops.alert.firing`) even with email off — `make edge-logs SERVICE=ops-monitor | grep alert.firing`
finds them.

**What this does not cover**, named so nobody assumes otherwise: a merchant's *own* custom
domain's certificate (that is the claim row and the sweep, next section), and anything that
needs a human's judgment rather than a threshold — this pages on symptoms, not root causes.

**If `ops-monitor` restart-loops instead of running:** it runs as the `product` image's own
non-root user (never the `deploy` user's uid — those are not guaranteed to match on this box;
Azure's own provisioning already takes uid 1000 for `azureuser`), and needs
`/srv/opsgrids/state/ops-monitor` to exist and be owned by that same uid before it can write its
state file and heartbeat. `remote.sh up`'s `ensure_ops_monitor_state_dir` creates and chowns it
(recursively, to the image's real `uid:gid` — both asked of the image, both checked to be plain
digits by `lib/numeric-id.sh` before anything runs as root, and the deploy aborts if either is
not) automatically on every deploy — `make edge-preflight` also checks and warns if it is out of
sync. If you ever need to fix it by hand outside a deploy: `ssh` to the box and run
`infrastructure/edge/remote.sh up` (safe to run alone; it recreates only what changed).

## Certificates, and how a human ever finds out

`ops-monitor`'s `cert` check (above) covers `EDGE_HOST`'s own certificate. A **merchant's**
custom domain is different — nobody configured its expiry into a check, because on-demand TLS
means each one is issued and renewed independently, on the first handshake, by Caddy itself.
These are the instruments for *that*:

1. **The merchant's own domain screen.** Every failed probe writes a reason and a
   `next_check_at` onto the claim row, and the merchant sees exactly what we looked for and
   what we found. This is the fastest path to discovery and it is a customer doing the
   noticing, which is not as good as a check that pages someone here directly.
2. **Let's Encrypt's expiry email** to `ACME_EMAIL`, roughly 20 and 7 days before a
   certificate expires, but only when renewal has already been failing. **If that address is
   not read by a human, a merchant domain's renewal failure has no other alert.**
3. **Caddy's log**, which is the only place a refusal is written in full:

```bash
make edge-logs HOST=… CONFIRM=… SERVICE=caddy TAIL=1000 | grep -i -e acme -e "on-demand" -e error
```

A weekly five-minute check is the compensating control, and it is a habit, not a system:

```bash
make edge-status HOST=… CONFIRM=…                 # everything up? disk?
make edge-logs   HOST=… CONFIRM=… SERVICE=caddy | grep -ci error
make edge-logs   HOST=… CONFIRM=… SERVICE=storefront-cron | tail -20   # is the sweep running?
```

**What each failure looks like:**

| Symptom | What it means |
|---|---|
| Browser TLS error on a merchant's domain, `ask` returning 403 | The name is not `verified`/`live`/`failing` in the database, or the store is not published. The origin is refusing on purpose |
| Browser TLS error, `ask` returning 200 | Issuance is failing. Usually the merchant's DNS does not point here, or port 80 is blocked, or the record is proxied through Cloudflare |
| The shop loads but every API call 503s | Not TLS. Go to [triage](#is-it-the-app-the-database-caddy-or-dns) |
| A merchant's domain says "pending" forever | The sweep is not running. `docker compose ps` — is `storefront-cron` up, or in a crash loop with `app/cron.py is not in this image`? |

**Losing `caddy_data`** (the volume holding the ACME account key and every certificate) is
survivable and expensive: Caddy re-issues each name on its next handshake, so the whole fleet
re-issues at once and every merchant's first visitor waits. Restore it from the nightly
`caddy-data-*.tar.gz` before letting it re-issue:

```bash
cd /srv/opsgrids/repo/infrastructure/edge
docker compose -f compose.prod.yml --env-file .env stop caddy   # every shop is down from here
docker run --rm -v opsgrids-edge_caddy_data:/data -v /srv/opsgrids/backups:/b:ro \
  alpine sh -c 'cd /data && tar xzf /b/caddy-data-<stamp>.tar.gz'
docker compose -f compose.prod.yml --env-file .env start caddy
```

(`opsgrids-edge_caddy_data` is the volume name: the compose project is `opsgrids-edge`. Check
with `docker volume ls` before typing it.)

### Take one merchant off the air, now

The supported lever is the merchant's own store state — `POST /storefront/store/unpublish`
over the audited API. Both the `ask` endpoint and origin resolution key on
`storefronts.status = 'published'`, so unpublishing stops certificate issuance and makes the
origin refuse the host. Allow up to `STORE_SLUG_CACHE_SECONDS` (300 s by default) for the
Redis host cache to expire, or clear it:

```bash
cd /srv/opsgrids/repo/infrastructure/edge
docker compose -f compose.prod.yml --env-file .env exec -T redis sh -c \
  "redis-cli --scan --pattern 'storefront:host:*' | xargs -r redis-cli DEL"
```

Do **not** edit the Caddyfile to block a host. Per-merchant edge configuration is the coupling
this design exists to avoid, and an edge config edit is a restart of everyone's shop.

---

## Is it the app, the database, Caddy or DNS?

Work down this list. Each step rules out one layer, and the first failure is your answer.

```bash
# 1. DNS — does the name reach us at all?
dig +short merchant.com A          # expect the reserved IP
dig +short _opsgrids-challenge.merchant.com TXT
#    Wrong address → the merchant's DNS. An address owned by Cloudflare → a proxied
#    (orange-cloud) record: TLS terminates there, our ACME challenge never arrives.

# 2. The edge process — no TLS, no DNS, no upstream in the way.
curl -si http://<reserved-ip>/_edge/health          # "ok" → Caddy is alive
#    No answer → Caddy is down or the box is unreachable. Check the provider console first:
#    a box that is out of disk or has been rebooted looks exactly like a network fault.

# 3. TLS for this specific name.
curl -sv https://merchant.com/_edge/health 2>&1 | grep -e "SSL certificate" -e "subject:"
#    Handshake error → issuance. Go to the certificates section above.

# 4. The API path, end to end.
curl -si https://merchant.com/api/v1/auth/health    # Caddy → gateway → auth → Postgres
#    502/504 → the gateway cannot reach the service. 503 → the service cannot reach a dependency.

# 5. Which container is unhappy?
make edge-status HOST=… CONFIRM=…
#    `storefront-cron` restarting is the sweep, not the shop: domains stop being checked,
#    nothing that is already live goes down.

# 6. The database.
docker compose -f compose.prod.yml --env-file .env exec -T postgres \
  psql -U opsgrids -d opsgrids -c 'select 1'
docker compose -f compose.prod.yml --env-file .env exec -T postgres \
  psql -U opsgrids -d opsgrids -c \
  "select state, count(*) from pg_stat_activity group by 1"
```

**Two silent failures that look like nothing at all**, both worth checking by hand when
behaviour is inexplicable and no log says anything:

- **Every buyer is told the store does not exist, with HTTP 200 and no error.** That is the
  storefront's directory role being unset. The service refuses to boot without it, so the
  symptom is a container that will not start with a long message about
  `opsgrids_storefront_directory` — which is the good outcome, and the reason the assertion
  exists.
- **The outbox fills and nothing is delivered.** True by construction in slice 1: no RabbitMQ
  and no relay are deployed. See [known gaps](#known-gaps-in-slice-1).

---

## The box is out of disk

80 GB, and three things consume it: Docker images and build cache, container logs, and
Postgres. Disk exhaustion stops Postgres accepting writes, which looks like a total outage.

```bash
make edge-status HOST=… CONFIRM=…    # prints df
ssh deploy@<ip>
df -h /; du -sh /var/lib/docker/* /srv/opsgrids/backups | sort -h | tail
docker system df
```

Reclaim, safest first:

```bash
docker image prune -af          # untagged and unused images — always safe
docker builder prune -af        # the build cache; the next deploy is slower, nothing else
rm -f /srv/opsgrids/backups/opsgrids-*.sql.gz   # only ones already uploaded off-box
ls -1 /srv/opsgrids/web/releases | head -n -3   # old bundles; keep at least two to roll back
```

**Do not** `docker system prune --volumes`. It deletes `caddy_data` (every certificate) and
`postgres_data` (everything). There is no confirmation you can take back.

Container logs are capped at 10 MB × 5 per container by `compose.prod.yml`. If a log has grown
past that, the cap was added after that container was created — recreate it with
`make deploy`, which is the only thing that applies a new logging option.

Prevention: the nightly backup prunes to `BACKUP_LOCAL_KEEP` dumps, `make edge-web` keeps five
bundles, and `docker image prune -af` after a deploy is a good habit that nothing does for you.

---

## The memory budget

`infrastructure/edge/memory-budget.json` prices every container this platform can deploy at a
measured (or, where noted, conservatively estimated) steady-state RSS. `preflight.sh` sums it
against whichever `COMPOSE_PROFILES` the box's own `.env` enables and the box's own live
`/proc/meminfo`, and prints one of:

- **ok** — comfortably under budget.
- **warn** — over the 80% soft line. The deploy proceeds; this is what the real production box
  reads today with every profile on (ADR-0013's memory-budget erratum, 2026-09-23) — tight, and
  already relying on the swapfile, but not refused.
- **refuse** — over the 100% hard line. The deploy stops. A box this far over budget will not
  come up cleanly under load even if the deploy itself succeeds.

If you see **refuse**: trim `COMPOSE_PROFILES` to the products this box actually sells (drop one
with zero activations first — check the catalogue, not habit), or consolidate a service's
processes the way `subscription` and `approval` already were (ADR-0013's erratum has the list of
what is left to do this way). **Do not** raise `hard_budget_percent` in `memory-budget.json` to
make a refuse go away — that number is the line past which the kernel starts reclaiming from
Postgres, RabbitMQ or Redis instead of an idle API container, and moving it does not move that
fact.

`tests/infra/test_compose_memory_budget.py` (`make test-infra`) is the same check run in CI,
against `.env.example`'s profile list and a fixed reference `MemTotal` — so a service added to
`compose.prod.yml` without a `memory-budget.json` entry fails before it ever reaches a box.

---

## Restore from backup

If `BACKUP_S3_ENDPOINT` is set (anything other than AWS itself — R2, B2, the provider's own
object store), add `--endpoint-url "$BACKUP_S3_ENDPOINT"` to every `aws s3` command below.
`backup.sh` does that for you; a command you type does not.

Nightly `backup.sh` puts two or three files in `s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/`: a
gzipped `pg_dump` of the whole database, a tar of Caddy's data volume, and — only when the
`storefront` profile is on — a tar of MinIO's data volume (every uploaded merchant image,
ADR-0009). The provider's VM snapshot is one more copy and the one to use when the box itself
is gone.

**The drill — do this once before you need it, and once a quarter after.** It restores into a
scratch database on the live box and touches nothing that is serving:

```bash
ssh deploy@<ip>
cd /srv/opsgrids/repo/infrastructure/edge
set -a; . ./.env; set +a          # the bucket name and credentials, from the box's own .env
C="docker compose -f compose.prod.yml --env-file .env"

# 1. Fetch the newest dump back from the bucket — this proves the upload works, which a
#    successful `backup.sh` on its own does not.
docker run --rm \
  -e AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY" \
  -e AWS_DEFAULT_REGION="$BACKUP_S3_REGION" \
  -v /srv/opsgrids/backups:/backup amazon/aws-cli:2.36.50 \
  s3 ls "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/"     # pick the newest name, then:
docker run --rm \
  -e AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY" \
  -e AWS_DEFAULT_REGION="$BACKUP_S3_REGION" \
  -v /srv/opsgrids/backups:/backup amazon/aws-cli:2.36.50 \
  s3 cp "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/<newest>.sql.gz" /backup/

# 2. Restore it into a scratch database.
$C exec -T postgres psql -U opsgrids -d postgres -c 'DROP DATABASE IF EXISTS opsgrids_restore'
$C exec -T postgres psql -U opsgrids -d postgres -c 'CREATE DATABASE opsgrids_restore'
gunzip -c /srv/opsgrids/backups/<newest>.sql.gz | \
  $C exec -T postgres psql -v ON_ERROR_STOP=1 -U opsgrids -d opsgrids_restore

# 3. Prove it holds what it should.
$C exec -T postgres psql -U opsgrids -d opsgrids_restore -c \
  "select count(*) from storefront.storefronts"
$C exec -T postgres psql -U opsgrids -d opsgrids_restore -c \
  "select custom_domain, custom_domain_status from storefront.storefronts \
   where custom_domain is not null"

# 4. Clean up.
$C exec -T postgres psql -U opsgrids -d postgres -c 'DROP DATABASE opsgrids_restore'
```

**The real thing — the database is corrupt or wrong and the box is otherwise fine.** This is
destructive and every merchant is down while it runs. Announce it, then:

```bash
cd /srv/opsgrids/repo/infrastructure/edge
C="docker compose -f compose.prod.yml --env-file .env"

$C stop auth account product storefront storefront-cron app-gateway   # stop writing
# Keep Caddy up: buyers get an error page from the origin, not a TLS failure.
gunzip -c /srv/opsgrids/backups/<chosen>.sql.gz | \
  $C exec -T postgres psql -v ON_ERROR_STOP=1 -U opsgrids -d opsgrids
$C start auth account product storefront storefront-cron app-gateway
./remote.sh migrate      # re-applies grants and brings the schema to head
./remote.sh health
```

The dump carries ownership, so the `opsgrids_*` roles must exist — they do, because
`20-init.sql` creates them on any fresh volume and `30-role-passwords.sh` sets their password.

**The box is gone entirely.** Rebuild from [standing up a new box](#standing-up-a-new-box),
move the reserved IP to the new VM (this is the step that means no merchant edits DNS), restore
the dump as above, restore `caddy_data` from its tar, then `make deploy` and `make edge-web`.
Expect 60–90 minutes, most of it the first build.

---

## Known gaps in slice 1

Named here so nobody discovers them during an incident:

- **Resolved 2026-09-23 (C10): `ops-monitor` alerts by email.** See
  [alerts and what each means](#alerts-and-what-each-means). What is still true: no pager
  rotation, no SMS/Slack/PagerDuty integration — one mailbox, checked by a human. And it covers
  the paths named in that table only; a symptom nowhere on that list still needs someone to
  notice it the old way.
- **No zero-downtime deploy.** Every deploy that changes a container restarts it.
- **The box does not resize to fit the container count (2026-09-23, ADR-0013's memory-budget
  erratum).** §5's own sizing said this box moves to 4 vCPU / 8-16 GB before slice 1 goes live;
  it never did, the owner has since said explicitly it will not, and every slice since has
  shipped on the same 2 vCPU / 3.8 GB VM regardless. [The memory budget](#the-memory-budget) is
  the enforcement that exists because of that: `preflight.sh` now refuses a deploy whose
  `COMPOSE_PROFILES` do not fit, instead of letting the box find out under load. `subscription`
  and `approval` each lost a container to a worker+cron merge as part of the same erratum
  (`app/worker.py` in each now runs the consumer and the sweep together, the same `gather`
  shape `notification/app/worker.py` already used for its two consumers). `notification` is the
  one remaining service that still runs a matching `-worker` and `-cron` as separate containers
  — a candidate for the same treatment, not done here because it was out of this slice's scope.
- **Resolved 2026-09-20 (ADR-0013 slice 1): RabbitMQ, a relay per outbox and the consumers now
  run on this box**, so activation installs a product's roles by event and
  `tools/wire_product.py` is a repair tool again, not a step. The paragraph below is kept as
  the record of why. What is still true: services *not* deployed here (employee, customer,
  approval, …) publish nothing and consume nothing, and a product behind a disabled compose
  profile is exactly as absent as before.
- **One relay, not one per service (2026-09-21).** With every product on, 24 relay processes
  cost ~800 MB of a 3.8 GB box, and the swapping that caused was the box's real CPU problem. The
  `edge-relay` container runs `python -m platform_core.edge_relay`, which drains every schema
  named in `EDGE_RELAY_SCHEMAS` (the compose default lists them all) as `opsgrids_outbox`. When
  events stop flowing — a trial that never activates, roles that never arrive — look here
  first: `make edge-logs SERVICE=edge-relay`. `edge_relay.schema.failed` names a schema whose
  relay died and is being restarted on its own; `edge_relay.schema.missing_table` is a product
  whose profile is off and is harmless. Outbox depth per schema is still the truth:
  `select count(*) from <schema>.outbox where published_at is null`. Every service keeps its
  own `bin/relay`; going back to one container per service is a compose edit, not a rewrite.
- **The scheduler (ADR-0015).** Behind the `finance` profile: finance's e-Invoice retry backoff
  is its only producer today. One container, `scheduler`, runs `./bin/worker` — the event
  consumer and the due-job sweep together in one process, for the same RAM reason as the shared
  relay above. A retry that never seems to happen: check
  `select count(*) from scheduler.scheduled_jobs where status = 'pending' and due_at <= now()` —
  a non-zero count with the `scheduler` container up means the sweep itself is stuck (`make
  edge-logs SERVICE=scheduler`); a non-zero count with it down or restarting is the container.
  `scheduler.outbox` fills exactly like any other service's if `edge-relay` is not draining it.
- **A verified SSO domain can be automatically demoted (2026-09-23, ADR-0018).** `account`'s
  worker re-checks every verified email domain's DNS on a schedule; if a domain has been
  failing for at least 14 days *and* failed at least 3 checks in a row, the sweep clears its
  `verified_at` itself, stamps `demoted_at`, writes an audit row, and publishes
  `core.identity.domain.demoted`. There is no pager for this in slice 1 — the only ways an
  operator finds out are the audit row (`account.audit_log`, action `domain.demoted`), the
  structured log line `account.domain.demoted` (`make edge-logs SERVICE=account-worker | grep
  domain.demoted`), or the organization noticing sign-in for that domain has stopped. Once
  demoted, the domain stops routing SSO immediately — the internal lookup only ever returns a
  domain with `verified_at IS NOT NULL` — and nothing re-verifies it automatically: the
  organization has to re-claim and re-verify it, the same as a brand-new domain.
- *(historical)* **No RabbitMQ, no relay and no consumers — and this breaks product activation.** An earlier
  version of this paragraph said slice 1 publishes no event that anything consumes. That was
  wrong. `product.activate()` publishes `core.product.activated`, and the **only** thing that
  installs a product's roles into an organisation is `account`'s consumer of that event
  (`services/account/app/consumers.py`). With no broker, a merchant can activate Storefront,
  the entitlement check passes, and `STOREFRONT_ADMIN` / `STOREFRONT_STAFF` are never created
  — so nobody can be granted `storefront.store.*` and every merchant route 403s. No log, no
  alarm. The outbox row sits in `product.outbox` forever.

  **Until a broker and `account`'s worker are deployed, activation is a two-step manual
  procedure**: activate the product, then run `tools/wire_product.py <org> <product>` against
  the box (it calls the same installer the consumer would). Do this for every org, every
  product, every time — and treat `product.outbox` depth as the signal that you forgot.

  Before any of that, the product has to be sellable at all: `docker compose exec product
  python -m app.launch <product_key> available` (P6) flips `products.status`, refuses a key the
  catalogue does not know, and writes an audit row. That replaced a hand-run `UPDATE
  product.products SET status = 'available' WHERE key = ...` — the command an operator used to
  be told to type directly against the database, with no refusal and no audit trail.

  Every other outbox also fills silently. `approval`, `subscription` and `storefront` have no
  entrypoint that starts their relay at all (their `bin/cron` correctly runs a sweep, and
  there is no `bin/relay`), so even with a broker those three would not publish.
- **No custom-domain sweep, so no merchant custom domain can go live.** `storefront-cron`
  runs `app/cron.py`, and that module was never written (ADR-0012 §6 was designed, not
  built). The container sits behind the `sweep` compose profile and is not started; a claim a
  merchant makes stays `unverified` forever, and the origin and the issuance gate both refuse
  an unverified name, by design. **`<slug>.stores.opsgrids.com` is unaffected** — it needs no
  sweep. Building the sweep is the next storefront slice; when it lands, start it with
  `docker compose -f compose.prod.yml --env-file .env --profile sweep up -d storefront-cron`
  and drop the profile from the compose file.
- **No merchant admin SPA on this box.** The edge serves the anonymous buyer bundle only. The
  API is reachable at `https://<EDGE_HOST>/api/…`; the merchant console is ADR-0001's and is
  served elsewhere.
- **Resolved: `document` and `minio` are on this box, behind the `storefront` profile** (see
  "Document and MinIO" above), **and resolved: ADR-0009 §8's purge sweep is built and
  deployed** as `document-cron` (see "The purge sweep" above) — a soft-deleted asset's object
  is actually removed from the bucket 30 days after removal, and quota accounting no longer
  drifts from reality forever. What is still off by default: §9's orphan-object pass
  (`DOCUMENT_ORPHAN_SWEEP_ENABLED`) — built, and safe to turn on, but left disabled until an
  operator has watched a run or two of its dry-run report, per the same section.
- **No log shipping.** Logs live in Docker's json-file driver on this disk and are lost when a
  container is pruned.
- **No staging environment.** `caddy validate` and the local `make gate` are the only things
  between an edit and production.
- **Certificates are not deleted when a merchant removes a domain.** Harmless: the origin
  answers 410 for a host with no live store, and the hostname was already public in
  Certificate Transparency.

## Routine

| When | What |
|---|---|
| After every deploy | `make edge-status` — everything up, disk sane, `ops-monitor`'s own `/status` |
| Daily | The `ops-monitor` all-quiet digest arrives (dead-man's switch) — if a day goes by with neither a digest nor an alert, the monitor itself may be stuck |
| Weekly | Caddy log for ACME errors; `storefront-cron` alive; disk |
| Monthly | `apt-get update && apt-get upgrade` and **a planned reboot window** — kernel updates are a full outage, so choose when rather than having it chosen for you |
| Quarterly | The restore drill above. A backup nobody has restored is a hypothesis |
| When a merchant asks about uptime in writing | Stop. That is ADR-0012's stated trigger to move off one box |
