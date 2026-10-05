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
| `admin.opsgrids.com` | A | the reserved IP — the operator/staff console (ADR-0003 §2): only needed once the `admin` compose profile is turned on ("Turning on the admin console", below), but harmless to create now like every other name in this table |
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
openssl rand -hex 32     # ADMIN_GATEWAY_HOP_KEY  (a different value from INTERNAL_SERVICE_TOKEN)
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
| `ADMIN_GATEWAY_HOP_KEY` | Generated fresh, ≥32 bytes, **different from `INTERNAL_SERVICE_TOKEN`**. Signs admin-gateway's operator hop header (BLOCKER 2 of the 2026-09-24 security review); only admin-gateway and auth read it |
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

**Single sign-on (ADR-0003 addendum, 2026-09-24):** `publish-web.sh` reads the box's own
`APP_BASE_URL` and bakes it into every product SPA's build as `VITE_AUTH_ORIGIN` — never `dashboard`
(the cookie already lives on its own origin) or `buyer` (no auth code at all); it also refuses to
publish a product app at all if its own host is not already in `AUTH_REFRESH_CORS_ORIGINS` (below),
rather than shipping a build that would fail its first reload. Baking in the auth origin is what
lets a product app's own `/login` route send the browser to the dashboard and get a session back
rather than rendering a form nobody can complete there.

`AUTH_REFRESH_CORS_ORIGINS` (`.env`, required) is the matching server-side half — the narrow,
credentialed CORS allowance on app-gateway for exactly `/api/v1/auth/refresh` and `/auth/logout`,
one entry per product app host (every platform app host except `app.opsgrids.com` itself), never a
storefront host. `.env.example` lists the current set, pinned in CI against `TLS_PLATFORM_HOSTS`
and the Caddyfile (`tests/infra/test_sso_redirect_allowlist.py`) so the three cannot silently
drift apart — add a host to all three together when a product ships one, or `publish-web.sh`'s
own check above catches the specific case of forgetting this one.

A host missing from this list does **not** surface as a 403, and the symptom is **not** "signed
out every ~15 minutes" the way the original bug was: the browser's own CORS check refuses the
preflight before the request ever reaches app-gateway (a 400 response app-gateway does send, but
the browser never exposes to the page — `fetch` just rejects), `refreshAccessToken()` sees that as
a network failure and reports its generic `"error"` outcome, and the client shows a "Couldn't
reach the sign-in service — Retry" screen rather than bouncing to `/login`. Confirm the cause in
the browser console (a CORS error naming the missing origin) or by checking the entry is actually
present in `AUTH_REFRESH_CORS_ORIGINS` on the box.

**Session length (ADR-0003 addendum, 2026-09-24):** a session lasts 7 days from sign-in, however
often it refreshes in between, in every app including the storefront console — `SESSION_TTL_DAYS`
on `auth`, default `7`, not currently set in `.env.example` or the box `.env` because the default
is the wanted value; set it explicitly only to change the policy. The silent refresh keeps
renewing the access token every ~15 minutes without interruption for the full 7 days and then
fails closed: `/auth/refresh` returns 401, and every product app's own client shows its sign-in
screen rather than looping or hanging — there is no separate expiry warning to configure.

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

**MinIO's memory (2026-09-25).** If `minio` is 502ing and `minio-init` is looping on "connection
refused", check for an OOM crash loop before anything else: `docker inspect
opsgrids-edge-minio-1 --format '{{.State.OOMKilled}} {{.RestartCount}}'` and `docker events
--filter container=opsgrids-edge-minio-1` (look for `oom` → `die 137` → `start` on repeat — this
is what failed `remote.sh health` in production on 2026-09-25, about once a minute, at the old
128 MiB `mem_limit`). The Go runtime does not read the cgroup limit on its own, so an idle bucket
can climb to within a few MiB of `mem_limit` and get OOM-killed before its own GC ever runs. The
fix already shipped: `mem_limit: 256m` plus `GOMEMLIMIT=192MiB` (compose.prod.yml) so the Go GC
targets a ceiling below the cgroup limit instead of the OOM killer picking the victim — see
`memory-budget.json`'s `datastores.minio.source` for the production evidence and the local
repro/proof numbers. If it is still OOM-killing at 256 MiB, the bucket has outgrown this box's
fuse (a lot more objects, or listing/console traffic this profile was never priced for) — raise
`mem_limit` and `GOMEMLIMIT` together (keep GOMEMLIMIT roughly 64 MiB under `mem_limit`), re-price
`datastores.minio` in `memory-budget.json`, and re-run `tests/infra` before deploying the change.

#### The purge sweep (ADR-0009 §8, and its 2026-09-23 addendum's §9)

**`document-cron` no longer exists as a container (the memory-headroom slice's fix round 2,
2026-09-24).** The row purge (§8's 30-day retention window) runs as a supervised background task
**inside the `document` API container** — `RUN_SWEEPS_IN_API` in `compose.prod.yml`,
`services/document/app/main.py`'s own `_start_purge_sweep`. The orphan-object pass (§9) does
**not**: it needs the second, list-capable credential, and that credential must never sit in the
environment of the container that serves requests (ADR-0009 §3 — a compromised or merely buggy
API process still cannot enumerate the bucket *because it does not hold the key that could*; a
runtime flag cannot uphold that, since compose's `environment:` block is static). The orphan pass
is therefore a deliberate, by-hand, one-off run — see below.

**What the in-API sweep does, one tick at a time (default every five minutes, idle-backing-off
to thirty):** finds every asset, across every organization, whose `deleted_at` is more than
`ASSET_PURGE_RETENTION_DAYS` (default 30) in the past and whose `purged_at` is still null; for
each, deletes the object from the bucket and then sets `purged_at`. A storage failure leaves the
row untouched for the next tick — it never marks a row purged while the object might still be
there. A tick whose *discovery* fails is logged and backed off, never propagated: this sweep now
shares a process with request traffic, and a failing sweep must not take the API down.

**How to check it — logs, not a dashboard** (there is no container to `ps` any more):

```bash
make edge-logs SERVICE=document | grep document.sweep
```

- `document.sweep.started` once, at boot, naming the retention window (`orphan_enabled=false`
  in-API, always).
- `document.sweep.pass` only when a tick actually did something — `purged`, `skipped`, `failed`.
  A quiet box between deploys is a box with no soft-deleted assets past their window, not a
  stopped sweep. **Whether the sweep is alive at all** is now "did `document.sweep.started`
  appear after the last `service.starting`" — if not, `RUN_SWEEPS_IN_API` is off for this
  container (`docker compose exec document printenv RUN_SWEEPS_IN_API`), or the sweep's outbox-role
  precondition failed (`document.sweep.not_started` at error — the upload API still started, on
  purpose). A sweep that ran and then died logs `supervise.background_sweep_exited`; the
  `sweep_alive` / `sweep_consecutive_failures` / `sweep_last_success_timestamp_seconds` gauges
  (`sweep="document.sweep"`) on `document`'s `/metrics` say the same without the logs — see 'The
  three sweeps that live inside their API now', under the memory budget.
- The pace: a tick that *purged* something runs again at once (a backlog drains back to back);
  anything short of that — nothing due, or work found that every attempt failed on or that another
  sweeper's lock skipped — backs off, doubling from the poll interval up to thirty minutes. An
  always-failing asset therefore shows as `document.sweep.pass` with `failed` > 0 every half hour
  and a climbing `sweep_consecutive_failures`, not as a busy loop.
- `document.sweep.purge_failed` names the organization and asset id a single purge raised on —
  the rest of that tick, and every later tick, still runs; it is not a stuck sweep.
- `document.sweep.tick_failed` means discovery itself failed (a DB restart, a deadlock) — logged
  with a full traceback and backed off; it recovers on its own once the fault clears.

**Run a purge (and, if wanted, the orphan pass) once by hand** — useful right after a deploy, to
confirm the retention window cleared a backlog without waiting for the next poll, and the *only*
way the orphan pass ever runs:

```bash
ssh deploy@<ip>
cd /srv/opsgrids/repo/infrastructure/edge
set -a; . ./.env; set +a
# Purge only (no extra credential needed):
docker compose -f compose.prod.yml --env-file .env run --rm --no-deps document ./bin/cron
# Purge plus the orphan pass, dry-run — the orphan credential is handed to THIS one container
# for THIS one run, by name, and never appears in a compose file or in `docker inspect`
# of the long-running one:
S3_ORPHAN_ACCESS_KEY="$DOCUMENT_S3_ORPHAN_ACCESS_KEY" S3_ORPHAN_SECRET_KEY="$DOCUMENT_S3_ORPHAN_SECRET_KEY" \
  docker compose -f compose.prod.yml --env-file .env run --rm --no-deps \
  -e S3_ORPHAN_ACCESS_KEY -e S3_ORPHAN_SECRET_KEY -e ORPHAN_SWEEP_ENABLED=true \
  -e ORPHAN_SWEEP_DRY_RUN=true document ./bin/cron
```

Press Ctrl-C after the first `document.sweep.pass` (or `document.sweep.started` with nothing to
do) rather than leaving it running as a second sweeper. It is safe alongside the in-API one — the
purge claims each row with `SELECT ... FOR UPDATE SKIP LOCKED`, so two sweepers racing never
double-purge — but pointless once the first pass has printed. `--rm` leaves nothing behind.

**Turning the orphan pass on for the first time:**

1. Generate `DOCUMENT_S3_ORPHAN_ACCESS_KEY`/`_SECRET_KEY` into `.env` (see `.env.example`) — the
   next deploy's `minio-init` creates the scoped user and its `ListBucket`+`DeleteObject` policy
   idempotently, same as the write-only pair already there.
2. Run it by hand, as above, with `ORPHAN_SWEEP_DRY_RUN=true`. Read the
   `document.sweep.orphan_would_delete` lines (organization id, asset id and key for every
   candidate) — every one of them must genuinely have no row.
3. Only then repeat with `-e ORPHAN_SWEEP_DRY_RUN=false`. From here it actually calls
   `DeleteObject` on what it lists. There is deliberately no always-on setting for this: a
   bucket-wide delete earns a human at the keyboard each time.

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
   migration-roundtrip` locally is what proves the `downgrade()` actually reverts. `migrate`
   ends with the **MFA reseal** — a no-op unless some `auth.mfa_factors` row still holds a
   plaintext secret, in which case it runs once from the new image and aborts the deploy unless
   it leaves none (see "Operator MFA" below).
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
`…/{id}/change-plan`, a service token minted by **console** — a token from any other service is
refused, ADR-0029); the customer route answers 403. A trial's length is the
plan version's `trial_days` feature (14 when absent), never the customer's.

### Inventory Phase 1 (lots, rolls, stock operations): migration and the `operations` permission reconcile

Two migrations (operations), both part of the ordinary `migrate` step; nothing else to run.
`b7d41e92c05a` creates `lots`, `rolls`, `roll_events`, `lot_genealogy`, backfills placeholders, and
adds organization-scoped foreign keys from the ledger, balances and discrepancies to them
**`NOT VALID`**; `c4a8e1f6d273` then validates those keys and builds the eight read-path indexes on
`inventory_ledger`/`inventory_balances`/`inventory_moves` with `CREATE INDEX CONCURRENTLY`.
Before it, take the nightly backup (`make edge-backup`) **and write down the row counts**:

```sql
SELECT (SELECT count(*) FROM operations.inventory_ledger)   AS ledger,
       (SELECT count(*) FROM operations.inventory_balances) AS balances,
       (SELECT count(*) FROM operations.inventory_moves)    AS moves;
```

**What stalls, and for how long.** The first revision sets `lock_timeout = 5s` and takes its
write-blocking lock on the ledger/balances/discrepancies only for the instant the `NOT VALID` keys
are created (last, just before commit) — so an unlucky deploy that cannot get the lock in five
seconds *fails* (re-run it once the long transaction is gone) rather than queueing every stock
post behind it. The backfill reads the ledger once per table (a sequential scan: expect seconds
per million rows, no write blocking). The second revision validates and indexes without blocking
reads or writes, but it scans/sorts the whole ledger: expect minutes on a ledger of tens of
millions of rows, and `migrate` simply takes that long. If it fails part-way (a long transaction
holding off a `CONCURRENTLY` build, say) the first revision is already committed: fix the cause and
re-run `make deploy` / `alembic upgrade head` — it resumes at the second revision, and an
interrupted index build's `INVALID` leftover is dropped and rebuilt automatically.

**Pre-check.** Nothing in the product could set a lot or roll id before this release, so expect
all of these to be zero. If any is not, the migration creates a `LEGACY-…` placeholder lot/roll
per distinct (organization, item, id) instead of refusing, and never changes a quantity:

```sql
SELECT 'ledger' AS t, count(*) FROM operations.inventory_ledger
 WHERE lot_id IS NOT NULL OR roll_id IS NOT NULL
UNION ALL SELECT 'balances', count(*) FROM operations.inventory_balances
 WHERE lot_id IS NOT NULL OR roll_id IS NOT NULL
UNION ALL SELECT 'discrepancies', count(*) FROM operations.inventory_discrepancies
 WHERE lot_id IS NOT NULL OR roll_id IS NOT NULL
UNION ALL SELECT 'count_lines', count(*) FROM operations.count_lines
 WHERE lot_id IS NOT NULL OR roll_id IS NOT NULL;

-- Lot or roll ids used with more than one item (a lot belongs to one item):
SELECT 'lot' AS kind, lot_id AS id, count(DISTINCT inventory_item_id) AS items FROM (
    SELECT lot_id, inventory_item_id FROM operations.inventory_ledger WHERE lot_id IS NOT NULL
    UNION SELECT lot_id, inventory_item_id FROM operations.inventory_balances WHERE lot_id IS NOT NULL
    UNION SELECT lot_id, inventory_item_id FROM operations.inventory_discrepancies WHERE lot_id IS NOT NULL
    UNION SELECT lot_id, inventory_item_id FROM operations.count_lines WHERE lot_id IS NOT NULL
) s GROUP BY lot_id HAVING count(DISTINCT inventory_item_id) > 1
UNION ALL
SELECT 'roll', roll_id, count(DISTINCT inventory_item_id) FROM (
    SELECT roll_id, inventory_item_id FROM operations.inventory_ledger WHERE roll_id IS NOT NULL
    UNION SELECT roll_id, inventory_item_id FROM operations.inventory_balances WHERE roll_id IS NOT NULL
    UNION SELECT roll_id, inventory_item_id FROM operations.inventory_discrepancies WHERE roll_id IS NOT NULL
    UNION SELECT roll_id, inventory_item_id FROM operations.count_lines WHERE roll_id IS NOT NULL
) s GROUP BY roll_id HAVING count(DISTINCT inventory_item_id) > 1;
```

An id shared by several items is handled, not refused: the first claimant keeps the id, every other
item gets its own placeholder with a new deterministic id, and that item's ledger, balance,
discrepancy and count-line rows are re-pointed at it (the one place the migration rewrites a row it
did not create), so no item is left holding stock that cannot be moved.

**Inventory Phase 1 rollback.** A **code-only rollback leaves the schema in place** — the new
tables, keys and indexes stay, and the previous API ignores them (it never writes a lot or roll id;
`remote.sh migrate` skips the `operations` schema with the `SKIPPED` warning above). That is the
supported rollback. **Never `alembic downgrade` past `b7d41e92c05a`** (`downgrade -2`,
`downgrade 665e44e243d7`) once real lots or rolls exist: it drops `lots`, `rolls`, `roll_events` and
`lot_genealogy` — every lot, roll, split, merge and trace. The migration enforces this: its
`downgrade()` refuses, with a message, when any lot or roll other than its own `LEGACY-…`
placeholders exists, unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` is set in the environment of
the `alembic` command — which you do only after a fresh backup and a decision to throw that data
away. (`downgrade -1`, which only drops the second revision's indexes, is harmless.)

**Reconcile the `operations` permissions after the API is deployed** (ADR-0007; same order as
every permission rollout: manifests committed → service that enforces them deployed → reconcile
→ only then the UI that assumes them). Four codes are new: `operations.inventory.lot.write`,
`operations.inventory.roll.write`, `operations.inventory.stock.read`,
`operations.inventory.stock.transfer`. `OPERATIONS_ADMIN` (`all()`) and `OPERATIONS_MANAGER`
get all four; `OPERATIONS_WORKER` gets `stock.read` and `stock.transfer`; `OPERATIONS_USER` gets
none of them. Releasing stock *out of* a hold status (REJECTED, DAMAGED, QUARANTINE,
QUALITY_HOLD, BLOCKED into AVAILABLE or WIP) additionally needs the existing
`operations.inventory.stock.adjust`, which `OPERATIONS_MANAGER` and `OPERATIONS_ADMIN` carry and
`OPERATIONS_WORKER` does not — a worker can put stock on hold but cannot release it. Roles an organization authored itself are never touched (ADR-0007 §4) — the
reconcile prints `memberships_without` so an operator can see who still lacks them.

```bash
make edge-reconcile HOST=deploy@<ip> CONFIRM=edge.opsgrids.com PRODUCTS=operations          # dry run
make edge-reconcile HOST=deploy@<ip> CONFIRM=edge.opsgrids.com PRODUCTS=operations APPLY=1
```

Until it runs, every new route answers 403 for every non-owner in every existing organization
(a brand-new organization is fine); the existing item-level inventory screens are unaffected.
It is a fleet write — every organization's permission version bumps — so, as always, outside
peak hours and never as part of `make deploy`.

### Warehouse map and storage put-away: migration, rollout, the 30-day purge, the Maps key

The warehouse-map release (design: `docs/superpowers/specs/2026-10-05-warehouse-map-putaway-design.md`;
decisions: [ADR-0028](../../docs/adr/0028-putaway-gps-captures.md) and the addendum to ADR-0014 §12) adds
a site map and a 2-D/3-D floor view to Inventory (`ops.opsgrids.com`), a "find stock" search, and a
**Put away** screen to the worker app (`worker.opsgrids.com`): a worker scans the destination bin's
label and the device's GPS fix is recorded as proof. The bin is the location of record; the fix is
audit evidence, kept 30 days. **There is no new `.env` key to add before this deploy** — the Google
Maps key is optional (see "The Google Maps key" below), so nothing here makes preflight refuse a box
that does not have it.

**What migrates.** One revision, `d7a3c9e5b184` (operations; it follows `c4a8e1f6d273`), part of the
ordinary `migrate` step, nothing else to run. It adds two nullable columns and three named CHECKs to
`warehouses` (`lat_micro`, `lng_micro`: a site's position as integer micro-degrees, both set or both
empty) and creates `putaway_captures`: **append-only** (`UPDATE` and `DELETE` are revoked from the app
role `opsgrids_operations`; row-level security on and forced). Everything is additive: `warehouses` is
a handful of rows and the new table is empty, so the `lock_timeout = 5s` lock is instantaneous; there
is no backfill; the ledger is untouched (a put-away is an ordinary `relocation` move whose
`reference_type` is the free-text `putaway`, so no DDL was needed). The previous API ignores the new
columns and table. Another inventory stack shares the previous head; whichever merges second re-points
this revision's `down_revision`, so confirm `alembic heads` shows **one** head at release time and use
the revision id `alembic current` prints on the box rather than this page's.

**Rollout order** (staged by the owner; merging deploys nothing):

1. Backend PR reviewed and squash-merged.
2. Owner-approved `make deploy` (`migrate` is additive: old and new code both run against the new schema).
3. **Verify the migration, changing nothing.** On the box, from `infrastructure/edge/`:

   ```bash
   docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T operations alembic current
   docker compose -f compose.prod.yml --env-file .env exec -T postgres psql -U opsgrids -d opsgrids -Atc "
   SELECT to_regclass('operations.putaway_captures') IS NOT NULL                          AS table_present,
          has_table_privilege('opsgrids_operations', 'operations.putaway_captures', 'INSERT') AS app_can_insert,
          has_table_privilege('opsgrids_operations', 'operations.putaway_captures', 'UPDATE') AS app_can_update,
          has_table_privilege('opsgrids_operations', 'operations.putaway_captures', 'DELETE') AS app_can_delete,
          (SELECT count(*) FROM operations.putaway_captures)                              AS captures,
          (SELECT count(*) FROM operations.warehouses WHERE lat_micro IS NOT NULL)        AS sites_with_coordinates;"
   ```

   `alembic current` must print the revision with `(head)`; the query must print `t|t|f|f|0|0` on a
   first deploy. **`app_can_update` or `app_can_delete` true is a stop**: the table is no longer
   append-only. Then run one synthetic put-away in a test organization (the new bundles are not
   published until step 5, so through the API with an ADMIN's token: from a receiving or staging bin
   to a rack bin, by the bin's barcode) and read it back: `GET …/inventory/putaway/{move_id}` shows
   the bin and **no coordinates**; an ADMIN's `POST …/inventory/putaway/{move_id}/location:reveal`,
   **from a session that gave a one-time code** (see "The reveal needs a one-time code", below), writes
   exactly one new audit row, whose `fields` are the *names* `lat_micro`, `lng_micro`, `accuracy_m`:

   ```sql
   SELECT count(*) FROM operations.audit_logs
    WHERE action = 'entity.sensitive_read' AND entity = 'inventory_move' AND entity_id = '<move_id>';
   ```

   In an organization that existed before this release the reveal answers **403 until step 4**: that
   403 is the expected negative result (it writes no audit row, so the count stays 0); repeat the
   reveal once step 4 has run. Each successful reveal adds exactly one row (two reveals, two rows).
   A 403 whose body says `Additional authentication required: otp` is a different refusal, not a
   missing reconcile: the session had no one-time code.
4. **Reconcile the `operations` permissions** (ADR-0007; the same order as every permission rollout:
   the code that enforces them deployed, then reconcile, then the UI that assumes them). One code is
   new: `operations.inventory.putaway.location_read` — **ADMIN only** (`OPERATIONS_ADMIN` through
   `all()`; no other role template includes it; owner decision D3). Putting stock away needs the
   existing `operations.inventory.stock.transfer`, reconciled by the Phase 1 rollout, so workers
   need nothing new.

   ```bash
   make edge-reconcile HOST=deploy@<ip> CONFIRM=edge.opsgrids.com PRODUCTS=operations          # dry run
   make edge-reconcile HOST=deploy@<ip> CONFIRM=edge.opsgrids.com PRODUCTS=operations APPLY=1
   ```

   A fleet write (every organization's permission version bumps), so outside peak hours and never as
   part of `make deploy`.
5. **Publish the bundles**, operations first, then the worker app:

   ```bash
   make edge-web HOST=deploy@<ip> CONFIRM=edge.opsgrids.com APP=operations
   make edge-web HOST=deploy@<ip> CONFIRM=edge.opsgrids.com APP=worker
   ```

   Each is one symlink swap with no restart, and each is undone the same way
   (`make edge-web-rollback … APP=operations`).

**The reveal needs a one-time code, and an administrator without one gets a 403.** Showing a worker's
position takes the permission **and** a session that gave a one-time code (`amr` includes `otp`,
ADR-0023). This was added in review, beyond what the owner approved for D3, and is an **open owner
decision: keep it, or revert to permission plus audit only** (ADR-0028, "Owner decision"; the revert is one
line). It is `otp` alone and not the `{otp, stepup}` pair the CRM reveals use, because the operations SPA
has no step-up prompt. The failure mode: an `OPERATIONS_ADMIN` who has no authenticator enrolled, whose
session started before they enrolled one, or who signed in through single sign-on or a social login
(those sessions carry `sso` / `oauth`, never `otp`) is refused with **403** and the body
`Additional authentication required: otp`. Nothing is revealed and no audit row is written; the drawer says
it could not show the location. Tell this 403 from the one a missed reconcile gives by the body: that one reads
`Missing permission: operations.inventory.putaway.location_read` (step 4). The remedy for the person is to
enrol an authenticator on the dashboard's Security page and sign in again with a code. It is not an outage
and not a reason to roll back. The administrator's synthetic check in step 3 has to come from such a session
too.

**The Google Maps key (optional, set by the owner).** The Google Cloud project, billing, the spend
cap and the key in the box's `.env` are the owner's, separate from this release, and **the release does
not wait for them.** `publish-web.sh` reads `VITE_GOOGLE_MAPS_API_KEY` from the box's `.env` for
`APP=operations` **only** (never `worker`, never any other app) and bakes it into the bundle. If the
line is absent, empty or malformed it prints a loud `WARNING` and publishes the bundle **without the
map**: the sites page shows a table of warehouses, the warehouse dialog takes latitude and longitude by
hand, and nothing else changes. To turn the map on later, add the line to the box's `.env` (keep mode
600) and publish operations again — a key added after a publish does nothing to a bundle already live:

```bash
ssh deploy@<ip>
cd /srv/opsgrids/repo/infrastructure/edge
printf 'VITE_GOOGLE_MAPS_API_KEY=%s\n' '<the key>' >> .env && stat -c '%a' .env      # prints 600
# then, from your laptop:
make edge-web HOST=deploy@<ip> CONFIRM=edge.opsgrids.com APP=operations
```

With a key, the audited reveal also draws the worker's position on a Google map, and the browser asks
Google for the tiles around it, so **Google learns the viewport position** (street level for a good fix).
Without a key the reveal is text only and nothing is sent to Google; that is the way to keep a worker's
position out of a third party's hands (ADR-0028).

The key is **not a server secret**: it ships inside the bundle, so every visitor of `ops.opsgrids.com`
receives it. It must be restricted in the Google Cloud console — HTTP referrers limited to
`https://ops.opsgrids.com/*`, the API list limited to the Maps APIs the app calls, a budget and a quota
cap — and it must never be a server-side key. The build log says whether a key was found and how long
it is; it never prints it. **No Caddy change ships with this release:** `ops` and `worker` send no
`Content-Security-Policy` or `Permissions-Policy` today (a test pins that), so the map loads and the
worker's location prompt works without an allow-list. CSP hardening is a separate release and will have
to allow the Google Maps origins when it lands. For local development, put the key in
`frontend/product-operations/.env.local` (untracked); with none, the app runs map-less.

**Limitation (c): the order engine reserves only at the item's home bin.** `accept_order` reserves
each line at `inventory_items.home_location_id` (`orders/services.py`, `_reserve_lines_for`) and
nowhere else; there is no multi-location allocation yet. So stock that has been put away into any
other bin **is not seen by order accept**: if the home bin does not hold the quantity, the accept is
refused as insufficient stock although the warehouse holds it. Storage put-away is therefore allowed
only **from `receiving` or `staging` bins** (a 422 otherwise, error code
`source_not_receiving_or_staging`; owner decision D1), and an item whose home bin is one of those kinds
drops out of what orders can reserve as soon as it is put away. Know this before telling a warehouse to
start putting stock away: the way back is an ordinary bin transfer to the item's home bin, and the
limitation lifts when multi-location allocation exists. Also, on `main` today a goods receipt lands in
the item's home bin (`receive_into_home`; kind `bin`, not `receiving`), so stock reaches a
receiving/staging bin only through opening stock posted there, an explicit bin transfer, or Phase 2
receipts (not yet merged): until then the Put away screen has little to offer a warehouse that has not
set up receiving or staging bins. Both are tracked in `docs/backlog.md`.

**The 30-day purge (owner decision D3).** A capture holds a worker's GPS fix. It is kept **30 days**
from the server's `created_at`, then erased by an **admin-run procedure, not an in-app job**: the app
role cannot delete the rows, and erasure is a decision a person takes. Coordinates are not PII under
ADR-0000, so they are stored in plaintext; the controls are the ADMIN-only permission, a one-time-code
session, the audit row on every reveal, and this erasure (ADR-0028). The tool is
`tools/purge_putaway_captures.py`. It connects as `opsgrids_admin` (the only role that can delete), is a **read-only dry run unless `--apply`**,
prints **counts only** (never a coordinate, a worker or a move), and takes `--organization-id <uuid>`
to confine it to one organization; the 30-day window is deliberately not an option. On the box, from
`infrastructure/edge/`, run it the way `remote.sh reconcile-permissions` runs its tool, from the
`operations` image:

```bash
docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T \
  -e PYTHONPATH=/app -v "$(cd ../.. && pwd)/tools:/opt/tools:ro" \
  operations python /opt/tools/purge_putaway_captures.py            # dry run
# the same command with `--apply` appended deletes; add `--organization-id <uuid>` to confine either
```

Read the dry run first: `eligible` is what `--apply` deletes, `retained` is what stays, and `database`
names the host and database it is aimed at. Keep the printed output with the change record; the purge
writes no audit row of its own. It is safe to re-run and to interrupt (it deletes in batches, one
transaction each). **Run it weekly** (a recommendation; it is the owner's calendar entry, nothing runs
it for you): a capture becomes eligible 30 days after it is written and goes at the next run, so the
real retention is 30 days plus the gap between runs. After a purge, the reveal of a purged capture
answers 404 (the UI says "capture expired") and the put-away itself — the move, the bin, the ledger,
the audit rows — is untouched. The rows survive in backups taken before the purge until those age out
(`docs/engage-erasure-and-unsubscribe.md` says the same of every delete in this database, with the WAL
and dead-tuple caveats). The tool purges by age and organization only; a narrower erasure (one worker)
is a hand-run `DELETE` as `opsgrids_admin` after a backup, and an owner decision.

**Rollback.** **Code-only; the schema stays.** `make edge-rollback` redeploys the previous ref and
`remote.sh migrate` then skips `operations` with the loud `SKIPPED` warning, leaving the schema as this
release made it; the previous API ignores the new columns and table. Roll the **bundles** back first
(`make edge-web-rollback … APP=operations`, then `APP=worker`; one symlink each) so no screen calls a
route the older API does not have (by design the worker app parks a put-away the server answers 4xx
as "needs attention" instead of losing it). Put-aways already posted stay in the ledger as ordinary
relocations with `reference_type` `putaway`, and their captures stay. To turn only the map off, remove
the `VITE_GOOGLE_MAPS_API_KEY` line from the box's `.env` and publish operations again. **Never
`alembic downgrade` past `d7a3c9e5b184`** (`downgrade -1`, `downgrade c4a8e1f6d273`) once captures
exist: it drops `putaway_captures` and the warehouse columns — every put-away's proof and every site's
map position. The migration enforces this: its `downgrade()` refuses, with a message, when any capture
row exists **or any warehouse has coordinates** (so a fully purged table alone does not lift the
refusal), unless `OPSGRIDS_ALLOW_DESTRUCTIVE_DOWNGRADE=1` is set in the environment of the `alembic`
command — which you do only after a fresh backup and a decision to throw that data away. On a database
that never held either, it reverses completely.

**Roll back when** any of these is true after the deploy (counts and key names only, never a value):

- the migration failed, or `alembic current` is not the head;
- the put-away error rate (5xx on `…/inventory/putaway`) is above what the same hours looked like
  before the release;
- a **coordinate key** is found where none may be. Both counts must be **0** (the reveal's own audit
  row lists field *names* in an array, which these do not match). A **site's** position is approved
  data and legitimately appears as `lat_micro` / `lng_micro` keys in the `before` and `after` of a
  `warehouse.updated` audit row (`entity = 'warehouse'`) every time someone sets, changes or clears a
  warehouse's location, so the audit query leaves the `warehouse` entity out: it stays at 0 after the
  owner's first "Set location", and a hit in any *other* row (a put-away's `inventory_move` rows
  above all) is a worker coordinate that must not be there. The outbox needs no such exception: a
  `warehouse.updated` event lists the changed field *names* only.

  ```sql
  SELECT (SELECT count(*) FROM operations.audit_logs
           WHERE entity <> 'warehouse'
             AND (jsonb_path_exists(after,  '$.**.lat_micro') OR jsonb_path_exists(after,  '$.**.lng_micro')
               OR jsonb_path_exists(before, '$.**.lat_micro') OR jsonb_path_exists(before, '$.**.lng_micro'))) AS audit_rows,
         (SELECT count(*) FROM operations.outbox
           WHERE jsonb_path_exists(payload, '$.**.lat_micro') OR jsonb_path_exists(payload, '$.**.lng_micro')) AS outbox_rows;
  ```

  and `make edge-logs HOST=… CONFIRM=… SERVICE=operations TAIL=5000 | grep -c -E 'lat_micro|lng_micro'`
  must print 0 too (a hit may be a coordinate in a log: read it with care, it is personal data);
- **a reveal without an audit row**: every successful `…/location:reveal` (200) must have exactly one
  `entity.sensitive_read` row (`entity = 'inventory_move'`, `entity_id` the move) in
  `operations.audit_logs` at that moment, for that actor. A 403 or a 404 writes none, by design;
- **the proof is not being kept** (see "A put-away whose proof was not kept", next): `putaway.capture_failed`
  appears for every put-away since the deploy, or the query below keeps returning rows. Roll the **bundles**
  back so workers stop putting stock away with no proof, then fix the cause.

**A put-away whose proof was not kept does not look like a failure.** The capture row is written in a
savepoint after the stock has moved. If that write fails (a missing grant, a constraint, a database
error), the move **stands**, the response is a **201** with the warning `geo_not_recorded`, and the service
logs one `putaway.capture_failed` line (move id and the error's class name, never a value). Nothing a
rollback trigger above watches would notice it: not a 5xx on `…/inventory/putaway`, not a coordinate, not a
reveal. **No alert fires on it:** `ops-monitor`'s six checks are not log-based, so this is a check someone
runs, as `payment.invoice.collected_twice` is. After the deploy, and once a day for the first week:

```bash
make edge-logs HOST=… CONFIRM=… SERVICE=operations TAIL=5000 | grep -c 'putaway.capture_failed'   # 0
```

and, because logs rotate and this does not, the durable form, from the same `psql` as step 3 (a role that
bypasses row-level security): the put-aways inside the purge window that have no capture row.

```sql
SELECT count(*) AS putaways_without_capture
  FROM operations.inventory_moves m
 WHERE m.reference_type = 'putaway'
   AND m.created_at > now() - interval '29 days'
   AND NOT EXISTS (SELECT 1 FROM operations.putaway_captures c
                    WHERE c.organization_id = m.organization_id AND c.move_id = m.id);
```

Both must be **0**. A hit means a put-away happened and its proof (who, which bin, the fix) was lost; the
move itself is correct. The log line names the move and the error class: `grep putaway.capture_failed`
beside it, then re-run step 3's privilege query (`app_can_insert` must be `t`) and read the error class.
The 29 days keep the window inside the 30-day purge, so a capture the purge removed is never counted.
Captures erased by hand (see the purge section) do show up here as hits, so check a hit against that
record first. A rate of one in a long while is a database blip to read; every put-away since the deploy is
the rollback trigger above. Wiring `putaway.capture_failed` to an alert is a follow-up
(`docs/backlog.md`).

### The billing run: first deploy, watching it, and what to do when it complains

The billing run (ADR-0005's 2026-09-23 addendum and its review fixes) is the loop that converts
ended trials, invoices every period, bills an upgrade's prorated difference, bills the closing
usage a cancellation or suspension leaves, and voids an invoice for a period the subscription
moved past. **It is off unless `.env` sets `BILLING_RUN_ENABLED=true`** — preflight warns while it
is off, and `/health/billing-run`'s `overdue` climbs — **and on, it needs `BILLING_RUN_NOT_BEFORE`**,
the cutover (below): without it preflight fails and the worker refuses to start the run
(`billing_run.refused` in the log; the payment consumer and dunning keep running). It lives in `subscription-worker` beside the payment consumer and the
dunning sweep — no container of its own. It calls billing (`POST /internal/v1/subscription-invoices`)
and payment (the mandate question) over the back network; billing in turn reads catalog and
metering. None of those calls holds a database transaction open.

**Order.** All additive: billing `b5e2c8d4f1a9` (the invoice idempotency key) and subscription
`d8b3f5a7c9e1` + `e4f6a8c0b2d4` (`billing_periods` with its proration/close kinds and void states,
`subscriptions.billing_interval` / `pending_change` / `usage_billed_through`, discovery indexes),
`f5a7b9c1d3e6` (the finalize step's states and `payment_failed_at`) and `a1c3e5f7b9d2` (a
proration's `against_period_id`, the `opening` kind, `subscriptions.suspended_at`).
`migrations/env.py` grants the outbox role `SELECT` on **named columns only** of the tables it
discovers from — in subscription, and in billing on `invoices(state, created_at)` for
`/health/billing-drafts` (an earlier grant of `id, organization_id` is revoked on every migrate). Billing, metering and payment must be serving the new routes before the worker's first
pass; a single `make deploy` does that, and a pass that runs early only backs off and retries.

**The cutover — `BILLING_RUN_NOT_BEFORE`.** Until now nothing ever converted a trial or rolled
a period, so without a cutover the first passes would invoice every period and conversion a box
has owed since before billing existed. The cutover is the moment billing goes live, set once, to
the moment you turn the run on, and **never moved later** (see *Moving the cutover* below). From it:

* **Recurring money for a period that began before it is never invoiced.** Such a period rolls
  `skipped` with outcome `pre_cutover` (a renewal, a proration, an opening). An existing
  subscription therefore starts paying at its **first period that begins after the cutover**; the
  period that straddles it is free.
* **A trial that ended before it converts to `active` with no invoice**, no mandate question and no
  dunning — its first paid period opens at the trial's end and is the free straddling period
  above. A trial that ends after it is invoiced and dunned exactly as before.
* **No usage from before it is billed.** A renewal's usage window starts no earlier than the
  cutover; a closing window that straddles it is billed from the cutover on; one that ended before
  it is skipped `pre_cutover`.

**The accepted syntax, exactly:** `YYYY-MM-DDTHH:MM:SS±HH:MM` or `YYYY-MM-DDTHH:MM:SSZ` — e.g.
`2026-10-01T09:00:00+05:30` or `2026-10-01T03:30:00Z` (seconds and a fraction of them are
optional). **Nothing else**: no space for the `T`, no bare date, no naive time, no epoch number
(pydantic would read `2026` as a second past 1970 and back-bill everything). A value the worker
refuses does **not** crash it — the payment consumer and dunning keep running — but the run does
not start (`billing_run.refused` in the log names the value and why), so check it before
recreating the worker, as below.

Stage it:

```bash
# 1. Deploy. The run is off by default; preflight says so.
make deploy HOST=… CONFIRM=…
# 2. Pick the cutover — the moment you will turn the run on — and preview against it. One JSON
#    line per due subscription, then a summary. Writes nothing.
docker compose -f compose.prod.yml --env-file .env run --rm -T subscription-worker \
  python -m app.billing_run --dry-run --not-before 2026-10-01T09:00:00+05:30
# 3. Decide. Set both.
printf 'BILLING_RUN_ENABLED=true\nBILLING_RUN_NOT_BEFORE=2026-10-01T09:00:00+05:30\n' >> .env
# 4. Check them BEFORE recreating the worker — both must pass:
./preflight.sh        # FAIL on a missing, malformed or impossible cutover; warn if it is future
docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T subscription-worker \
  python -c 'from app.config import settings; s = settings(); print(s.billing_run_not_before, s.billing_run_not_before_invalid)'
#    Expect the instant, then None. `None <reason>` means the worker would refuse the run.
# 5. Only then recreate the worker.
docker compose -f compose.prod.yml --env-file .env up -d subscription-worker
```

A cutover in the future is legal (preflight warns): the run rolls periods that began before it
without invoicing them and invoices nothing until periods begin after it.

**Moving the cutover** — don't move it later. Known limitation, not detected by any probe: the run
decides each row by the cutover in force when it first handles that row, and never revisits one
it has settled or already sent to billing (a retry sends exactly what billing priced). Moved
**later**, rows not yet handled that begin between the old and the new cutover become free, while
the same kind of row handled earlier was invoiced — inconsistent, and nothing alarms. Moved
**earlier**, nothing already skipped `pre_cutover` is re-billed.

Per subscription the dry run prints `periods_owed`, split into `periods_pre_cutover` (rolled with
no invoice), `periods_invoiced` and `periods_suspended_roll` (suspended, rolled with no invoice),
and `grace_already_expired` for a trial that ended after the cutover more than fourteen days ago
(without a usable mandate the dunning sweep suspends it on its next pass). The summary adds the
backlog of rows that are their own work — `backlog` by kind (`opening`, `close`, `proration`,
`finalize_pending`, `void_pending`), and how many of the billable ones would be invoiced
(`backlog_invoiced`, of which `backlog_usage_clamped` start at the cutover) or skipped
(`backlog_pre_cutover`). `periods_invoiced` above 1 for a subscription means it is more than one
period behind **after** the cutover — expected only if the run was held after its cutover; the run
bills from the stored boundary, one period per pass.

**While the run is off, plan changes on overdue subscriptions are refused** — cancellations never
are. A customer or operator plan change answers **409 "billing is catching up on this subscription
— try again shortly"** whenever a period end or a trial end is due and the run has not claimed it
(ADR-0005 addendum, round 3, M3). With `BILLING_RUN_ENABLED=false` nothing claims, so every
subscription past its boundary cannot change plan until the run is on and has caught up (one
period per pass). A customer cancel always succeeds: the period that was due and never opened is
never billed (the run bills the cancellation's closing usage only — ADR-0005, final review).

**A permanently failing claim freezes that subscription's plan changes.** A claim that fails
permanently (`failing_permanent` below) stays pending; once its own period has ended too, the
subscription is more than one period behind and its plan changes answer 409 until a person fixes
the claim. `failing_permanent > 0` is the alert; cancels still work.

**New on the first pass after `a1c3e5f7b9d2`:** a subscription that started `active` without a
trial was never charged its first period. From this revision every new such subscription is
charged an **opening** charge (the first period, at the pin in force) on the run's next pass —
unless it began before the cutover, when it is skipped `pre_cutover` like any other early period.

**A payment is not a key to every suspension.** A suspension by dunning (the window ran out —
written by the sweep's service actor) is lifted only by a capture of **the invoice that window was
about**; the run's closing-usage invoice and any other capture are recorded by payment and move
nothing (`dunning.capture.no_resume` in the log). An **operator's** suspension is never lifted by a
payment at all — a person lifts it. Which one is in force is read from the transition's actor,
never its reason, and an operator may not use a reason the machinery writes (`dunning_exhausted`,
`grace_expired`, `payment_failed`, … — refused with 422).

`suspended -> suspended` is not a legal move, so an operator cannot put a fraud hold **on top of**
a dunning suspension: if the customer then pays the owed invoice, the dunning suspension lifts and
the subscription is active. To hold such a customer, suspend again as soon as it resumes (watch
`dunning.capture` in the log), or cancel.

**Lifting an operator's suspension by hand.** There is no internal resume route yet (a follow-up is
recorded). Until one exists, lifting it is a reviewed one-off that must write **all** of the
following in **one tenant-bound transaction** — partial is worse than none:

1. `subscription.subscriptions`: `state = 'active'`, `suspended_at = NULL`.
2. `subscription.subscription_transitions`: one row `from_state = 'suspended'`,
   `to_state = 'active'`, the plan version as both pins, a reason in words (not a reserved one),
   and `actor = {"type": "operator", "id": "<the person>"}`. The table is append-only.
3. `subscription.audit_logs`: the matching `commerce.subscription.activated` row.
4. `subscription.outbox`: a `commerce.subscription.activated` event carrying the subscription's
   lifecycle payload (`services._lifecycle_payload`). **Without it product-service never
   re-entitles the customer and every request stays a 402.**
5. `subscription.billing_periods`: an `opening` row for `[now, current_period_end)` at the pin in
   force — unless a live recurring charge already covers now, or the current period began before
   the billing cutover (it is free). Without it the rest of the period is never billed.

Do it with the service's own code, not hand-written SQL — `services._transition(...)` (which writes
1–4 and the state) followed by `services.charge_uncovered_remainder(...)` (5), exactly as
`record_payment_captured` does when a dunning suspension lifts, run inside
`session_scope(organization_id=…)` in a `docker compose run --rm subscription-worker python`
session, with the subscription locked first (`selectors.lock_subscription(..., skip_locked=False)`).

**Watching it.**

```bash
docker compose -f compose.prod.yml --env-file .env exec subscription \
  python -c "import urllib.request as u; print(u.urlopen('http://127.0.0.1:8000/health/billing-run').read().decode())"
make edge-logs SERVICE=subscription-worker | grep billing_run
```

| Field | Means | Alert |
|---|---|---|
| `overdue` | subscriptions due for over an hour and not yet handled | **> 0** — the run is stopped, held (`billing_run.disabled` in the log), or cannot keep up |
| `pending` / `oldest_pending_seconds` | claims taken and not yet recorded | oldest > 1 h |
| `failing` | claims whose last attempt failed; retried with backoff (5 min doubling to 6 h) | > 0 for a day |
| `failing_permanent` | a data defect (a 4xx from billing, a `one_time` cadence) or five failures; still retried at the cap | **> 0** — a person must fix the data |
| `void_pending` | invoices for superseded periods waiting to be voided in billing | > 0 for an hour |
| `finalize_pending` | recorded claims whose draft invoice billing has not finalized yet — nothing is chargeable until it is | > 0 for an hour |
| `close_charges_failed` | closing-period charges that failed after the customer cancelled — owed, not dunned | **> 0** — a person decides |
| `proration_charges_failed` | upgrade or opening charges that failed after the subscription was cancelled or suspended — owed, not dunned | **> 0** — a person decides |
| `prorations_on_voided_parent` | a proration charged although the period it was priced against was voided or skipped — must be zero by construction | **> 0** — refund it; report a bug |
| `invoiced_24h` / `skipped_24h` / `last_recorded_at` | throughput; `skipped` is suspended periods rolled without an invoice, and anything skipped `pre_cutover` | — |

Billing has its own probe, `/health/billing-drafts` (`docker compose … exec billing python -c
"…u.urlopen('http://127.0.0.1:8000/health/billing-drafts')…"`): `drafts`, `drafts_stale` (older
than an hour) and `oldest_draft_seconds`. **Alert on `drafts_stale > 0`** — a draft the run never
finalized is a period nobody is being charged for.

**Rolling back past `f5a7b9c1d3e6` leaves drafts behind.** That downgrade removes the finalize
step's states from subscription: a claim that was `finalize_pending` at that moment becomes
`invoiced`, the older code has no finalize step, and billing keeps its **draft** invoice,
un-finalized and uncharged, forever. Before downgrading,
let `finalize_pending` reach 0 (or stop the worker and accept it), and afterwards read
`/health/billing-drafts`: each stale draft needs a person — finalize it through the internal
route once the forward revision is back, or void it.

Run invoices are **drafts** until the run has recorded them; only then are they finalized (and
charged). A customer never sees a draft. A plan change to another billing cadence is refused —
cancel and subscribe again.

The worker exits non-zero if any of its loops (the payment consumer, the dunning sweep, the billing
run) stops; `restart: unless-stopped` brings it back. `worker.loop_failed` / `worker.loop_returned`
name the loop. A pass that fails is retried with backoff (`dunning.sweep.tick_failed`,
`billing_run.tick_failed`); one poisoned dunning cycle is `dunning.sweep.cycle_failed` and is
pushed back fifteen minutes.

Log lines: `billing_run.cutover` (the cutover the loop started with), `billing_run.refused` (enabled
without a cutover — the run is not running), `billing_run.pass` (counts, only when a pass did
something; `skipped_pre_cutover` among them), `billing_run.recorded`
and `billing_run.failed` (per subscription, with its ids), `billing_run.not_billable` (a
`one_time` plan's trial — nothing sells one today), `billing_run.period.superseded` /
`billing_run.period.stale_closed` (the subscription changed between claim and record — the run
voids whatever billing holds under the claim's key through `POST /internal/v1/subscription-invoices:void`
and the row ends `voided`, or `skipped` with outcome `nothing_to_void`; payment may already have
raised a charge from the finalized event, so check payment's charges for that invoice).

**`failing` is not dropping.** Read the reason on the row:

```sql
SELECT organization_id, subscription_id, period_start, attempts, last_error
FROM subscription.billing_periods WHERE state = 'pending' ORDER BY created_at;
```

A stuck row never holds up the others: discovery skips a subscription while its claim is leased
or backing off. `billing answered 503` — catalog or metering was down; it clears on its own. `billing answered
422: … not on plan version …` — the subscription's items do not match its pin (data; fix the
items). `no gst rate is in force …` — `BILLING_TAX_CODE` is set and `billing.tax_rates` has no row
in force for that date; insert the rate (a new row, never an edit) or unset the code.

**Tax.** `BILLING_TAX_CODE` / `BILLING_TAX_JURISDICTION` in `.env` choose the `tax_rates` row;
empty (the default) writes no tax line. Insert the rate row **before** setting the code.

### Staff plan switching (ADR-0029): the billing run must be on, and what to check first

The console's **Change plan** control (`/api/v1/admin/organizations/{id}/plan-options`,
`.../subscriptions/{product_key}/plan-change:preview` and `.../plan-change`; owner and billing_ops) lets
staff move a subscription to another plan. A **trial** switches immediately. A **paying** subscription
switches at its **next billing period**, and an **equal-price** change applies at once — staff cannot force
"now, prorated"; for goodwill sooner, use *Grant credit*. An equal-price change that applies now on a paying,
usage-priced subscription is not free: the usage metered so far is closed at the current plan's usage rates
and invoiced by the billing run (the confirm says so when it happens).

Only versions catalog **offers the organization now** can be chosen: a version superseded by a newer one, or
of a retired plan, is refused **422 `not_offerable`** even if an operator's open dialog still lists it (the
dialog then reloads the list). Withdrawing a scheduled change back to the current pin is always allowed. If
catalog cannot be reached the confirm answers **503 `catalog_unavailable`** and nothing is claimed or changed.

**"Next billing period" only happens while the billing run is running.** The change is stored as a
pending change and applied when the run claims the next period. So, before the first staff switch on the
box, confirm from the section above that:

```bash
# Expect `True`, then the cutover instant, then `None`. `BILLING_RUN_ENABLED` defaults to false.
docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T subscription-worker \
  python -c 'from app.config import settings; s = settings(); print(s.billing_run_enabled, s.billing_run_not_before, s.billing_run_not_before_invalid)'
```

* `BILLING_RUN_ENABLED=true` and a valid `BILLING_RUN_NOT_BEFORE`. With the run **off**, a scheduled change
  waits indefinitely, and a change on any subscription whose period is already due is refused with
  **409 `billing_catching_up`** — which is indefinite then, not "try again shortly".
* `/health/billing-run` is not climbing (`overdue`, `failing_permanent`). A permanently failing claim also
  freezes that subscription's plan changes until a person fixes the claim.

Deploy order: **catalog and subscription first, then console** (it runs the migration for
`console.idempotency_keys`) **and the `opsgrids-internal` frontend**. The admin-gateway is not recreated.
Rollback is the console and frontend images; the other changes are additive and written rows stay.

What to read when an operator reports a problem:

```sql
-- console: the claim and its outcome for one staff action (the Idempotency-Key the operator's browser sent)
SELECT key, status, response, created_at, updated_at FROM console.idempotency_keys
WHERE organization_id = :org ORDER BY created_at DESC LIMIT 20;
-- console: every attempt leaves an intent row, then exactly one outcome row
SELECT at, action, reason, acting_as, after FROM console.audit_logs
WHERE organization_id = :org AND action LIKE 'subscription.plan_change%' ORDER BY at DESC;
-- subscription: the scheduled change itself, and its operator
SELECT id, state, plan_version_id, pending_plan_version_id, pending_change
FROM subscription.subscriptions WHERE organization_id = :org;
```

A claim stuck `in_progress` is a request that died mid-call: after 60 seconds a retry with the same key
reconciles it from a live read. `unknown` means the answer was lost; the operator sees **503
`outcome_unknown`**, and repeating the same request is safe. A failed **preview** is never `outcome_unknown`
(it is a read): it answers **503 `preview_unavailable`**, nothing was changed. The operator's reason is on the
audit rows above (console's, and subscription's `audit_logs.reason`) and **not** on the
`commerce.subscription.*` event — a customer org's own notification templates render that payload. A
scheduled change emits **no event**, so the
account list does not show it — the Change plan card (the live pricing read) does. Product access follows a
change that has taken effect within about **15 minutes**.

### Staff add product and extend trial (ADR-0030): deploy the consumers first

Two more console controls on the account page, on the same claim table and the same rules as Change plan:

* **Add product** (`GET /api/v1/admin/organizations/{id}/add-product-options`, `POST .../subscriptions`;
  owner and billing_ops) gives an organization a product it has **no subscription row for in any state** —
  a cancelled one cannot be re-added yet (409 `already_subscribed`). A **trial by default**. A **paid** start
  bills at once: subscription claims the first period immediately and **no payment mandate is checked**,
  so a failed charge opens dunning; the operator must tick that, or the API answers 422
  `charge_not_acknowledged`. A `one_time` plan (422 `one_time`) and a currency other than the
  organization's live subscriptions' (422 `org_currency_mismatch`) are refused, and so is a version
  catalog no longer offers the organization (422 `not_offerable`; a catalogue outage is 503
  `catalog_unavailable`, which changed nothing).
* **Extend trial** (`POST .../subscriptions/{product_key}/extend-trial`; **owner only**) moves a trial's end
  to an absolute instant — the operator picks the last day in **IST**. Refused with 409 `state_changed` if
  the trial end moved since it was read (which is also why a repeat never adds the time twice), 422
  `not_trial`, `claim_in_flight` (the billing run is already pricing the conversion), `trial_not_extended`,
  or `trial_cap`. **The cap is 90 days from the trial's start** (`MAX_TOTAL_TRIAL_DAYS` in
  `services/subscription/app/domain.py`, an owner-adjustable default; changing it is a code change and a
  subscription redeploy).

Extend trial emits a **new event, `commerce.subscription.trial_extended`**, which product (the activation's
trial end) and console (the account's trial end) must consume. **Deploy order:**

1. **product** and **console** — their consumers bind the new routing key. A consumer bound before the
   producer exists simply receives nothing.
2. **subscription** — now it can emit `trial_extended`. Deployed before step 1, the event lands in queues
   not bound to it: product keeps the old trial end and a customer whose old end has passed stays locked
   out until the next lifecycle event.
3. the **opsgrids-internal** frontend (and **notification**, whose catalogue now lists the key so a rule
   can be written for it; nothing fires by default).

No migration (console reuses `console.idempotency_keys`; actions `add_product`, `extend_trial`). The
admin-gateway is not recreated. Rollback: the console and frontend images; consumers binding an extra key
are harmless, and written rows stay.

Checks after deploying, before the first staff use:

```bash
# Every consuming queue carries the new binding (on the box; rabbitmqctl, the broker's own CLI).
docker compose -f compose.prod.yml --env-file .env exec -T rabbitmq \
  rabbitmqctl list_bindings source_name destination_name routing_key | grep trial_extended
# Expect three rows: product.events, console.events and notification.events (notification binds
# every key in its SUBSCRIBABLE_EVENTS). Only the first two until notification is redeployed in
# step 3 — and product.events or console.events missing at any point means stop.
```

```sql
-- console: the claims and outcomes for one organization's staff actions
SELECT key, action, status, response, updated_at FROM console.idempotency_keys
WHERE organization_id = :org ORDER BY created_at DESC LIMIT 20;
SELECT at, action, reason, acting_as, after FROM console.audit_logs
WHERE organization_id = :org AND (action LIKE 'subscription.add_product%' OR action LIKE 'subscription.trial_extension%')
ORDER BY at DESC;
-- subscription: the extension itself (a trial -> trial transition) and its operator
SELECT occurred_at, from_state, to_state, reason, actor FROM subscription.subscription_transitions
WHERE organization_id = :org AND reason = 'trial_extended' ORDER BY occurred_at DESC;
```

### Operator MFA: mandatory rollout, the MFA reseal, and the one-off codes

The security review of the operator-MFA slice made a second factor mandatory for every staff
account (ADR-0003 addendum) and closed several holes in how it is enforced. It ships `auth`
migrations through the merge revision `f8e432a62a84` (the join of the operator-MFA chain ending
`5ba575e36b1a` and the domain-verification chain ending `0c1a721d4214`; it changes no table).

#### The reseal — every MFA factor, tenant and staff, and the deploy does it for you

A box deployed from `main` stores **every** TOTP secret — every tenant member's and every staff
account's — as raw base32 plaintext in `auth.mfa_factors.secret_enc`. The new auth code seals new
secrets under the PII key with a per-row AAD and **refuses** a plaintext row unless
`MFA_ACCEPT_LEGACY_PLAINTEXT=true` is set, because nothing authenticates such a row: anyone able to
write that column could choose a factor's secret. Left alone, that refusal would lock out every
member who has MFA, so the deploy reseals first. The order is:

```
build → migrate → RESEAL → up → health
```

`remote.sh migrate` ends by calling `reseal_mfa_factors_if_pending` (`lib/mfa-reseal.sh`). It counts
the rows still holding a raw secret with the statement below and **does nothing — not even start a
container — when that is 0**, which is every deploy after the first. When it is not 0, it runs a
one-off container **from the new image**, before any new container serves:

```bash
docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T \
  -e MFA_ACCEPT_LEGACY_PLAINTEXT=true auth python -m app.mfa_maintenance reseal
```

then counts again and **aborts the deploy unless the count is 0**. `reseal` prints `resealed=N`
and `remaining=N` (counts only) and exits 1 if `remaining` is above zero, so a run without the
flag can no longer read as success. Each row it touches is sealed under the current key with the
per-row AAD and audited (`auth.mfa_factor_resealed`). Envelopes sealed before the AAD existed are
resealed too; those need no flag, because GCM already authenticated them.

The pre- and post-check, which you can run by hand on the box at any time and which must end at
`0`:

```sql
select count(*) from auth.mfa_factors where secret_enc is not null and encode(secret_enc,'escape') ~ '^[A-Z2-7]{16,32}$'
```

```bash
docker compose -f compose.prod.yml --env-file .env exec -T postgres \
  psql -U opsgrids -d opsgrids -Atc "select count(*) from auth.mfa_factors where secret_enc is not null and encode(secret_enc,'escape') ~ '^[A-Z2-7]{16,32}\$'"
```

**The live auth container never gets `MFA_ACCEPT_LEGACY_PLAINTEXT`.** It is not in
`compose.prod.yml`, not in the shared service environment and not in `.env`; it exists only as a
`-e` on the one-off run above, and there is no tooling here that could put it on a running
container. If a count is ever above 0 on a live box (a row written by an old container during a
rollback, say), the next deploy reseals it; to do it now, run the one-off command above by hand.

*The window, stated plainly.* Between the reseal finishing and `up` recreating auth, the **old**
auth container is still serving and cannot read a sealed envelope: `main`'s
`_accept_second_factor` decodes `factor.secret_enc` as UTF-8 *before* it looks at recovery codes,
and a sealed envelope is never valid UTF-8. So for those seconds **every** MFA sign-in — an
authenticator code **and** a recovery code — for a member whose factor was just sealed ends in a
**500** (the old code has no MFA lockout, so nothing is counted against them). That is accepted:
it is seconds long, and the alternative order — `up`, then reseal — is a lockout. The deploy runs
the reseal a **second time after `health`**: a plaintext factor enrolled through the old container
inside that window would otherwise stay refused until the next deploy (it is idempotent, and only
warns if it fails, since the site is already up).

*If a plaintext row is ever met while the flag is off* — a restore from an old backup, a manual
insert — auth answers a distinct **503 "Multi-factor re-seal pending — use a recovery code"** and
logs `auth.mfa_factor_plaintext_refused`. That refusal is **not** counted against the person's MFA
failure counter and never locks the account: the code may well have been right, and it is our state
that is wrong. A recovery code still signs them in; a wrong code, or anything that is not six
digits, still counts as usual. Run the reseal to clear it.

#### The staff enrolment codes

**`bin/issue-enrollment-codes`** — every staff account that predates mandatory MFA has no
confirmed factor and needs an out-of-band enrolment code before it can enrol one (a password alone
is not enough to reach enrolment, by design). Run once, after the migration that revokes every live
operator session (`c731a475333d`) lands:

```bash
docker compose -f compose.prod.yml --env-file .env run --rm -T auth \
  python -c "from app.bootstrap import issue_codes_main; issue_codes_main()"
```

Prints `email code` pairs to stdout, once — hand each one to its owner out of band (chat, a phone
call — anything that is not this box's own logs). Skips any account that already has a confirmed
factor, so re-running after some staff have enrolled issues codes only for the rest.

#### Not a deploy step: `bin/reset-staff-mfa --all`

This exists only for a development, test or CI environment that ran the pre-fix version of this
slice (before `5ba575e36b1a` separated the MFA lockout counter from the password one) and should
not trust the factors and recovery codes it enrolled under that bug. It wipes every staff
account's MFA and clears their MFA locks. It accepts exactly the environment names `local`, `test`
and `ci` and **refuses in every other one — `staging`, `uat` and `production` included** — so there
is nothing to run on this box. A production account recovered from that bug, or one whose owner
lost their device, goes through the audited `POST /auth/operator/staff/{id}/mfa:reset` owner
route instead, one account at a time; that route also clears the account's MFA lock.

#### `ADMIN_GATEWAY_HOP_KEY` — auth refuses to start without it

In `production`, `staging` and `uat` auth's settings refuse to load — the service does not start —
if it is unset or empty, and also if it equals `INTERNAL_SERVICE_TOKEN` (in any environment): the
hop signature auth's operator routes verify is the second lock behind the route table's own block
of `/api/v1/auth/operator`, and an unset key would silently switch it off. `compose.prod.yml`
passes it to the `auth` service only, as `${ADMIN_GATEWAY_HOP_KEY:-}` — **an empty default, on
purpose**: a `:?` there would abort *every* compose command on a box without the key, `backup.sh`'s
`pg_dump` and a rollback included. The key is **not** in the shared service environment, so no
other container — app-gateway included — receives it.

**Where a missing key is caught is `preflight.sh`**, before a build or a migration: it fails on an
empty `ADMIN_GATEWAY_HOP_KEY` (every key in `.env.example` must be set), on any of
`INTERNAL_SERVICE_TOKEN`, `PII_ENCRYPTION_KEY` and `ADMIN_GATEWAY_HOP_KEY` shorter than 32 bytes,
and on a hop key equal to the internal token — the same rules the services enforce at startup, so
the deploy stops instead of shipping an auth container that will not start. Generate it as
`INTERNAL_SERVICE_TOKEN` is in "Secrets — generate them here, now, and never anywhere else"
above: a fresh, separate 32+ byte value, never reused. admin-gateway, the only signer, is in this
compose file behind the `admin` profile (off by default — see "Turning on the admin console"
below) and also reads this key, so operator sign-in stays unreachable on this box until that
profile is turned on, and works the moment it is, with no key rotation needed in between.

#### A locked-out MFA is unlocked by waiting, or by an owner

After `LOCKOUT_MINUTES` (default 15) of a lock the failure count starts from zero, so one typo
afterwards is one strike, not an immediate re-lock. A staff account can be unlocked sooner by an
owner through `POST /auth/operator/staff/{id}/mfa:reset` (which also wipes the factors). A tenant
member's MFA lock has no admin endpoint by design — it would need a new permission and a
cross-service membership check to guard a fifteen-minute wait — so expiry *is* the unlock. The lock
refuses recovery codes as well as authenticator codes, so there is nothing to do but wait.

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

### Turning on the admin console

`admin-gateway`, `console` and `console-worker` ship in `compose.prod.yml` behind a new compose
profile, `admin` — not one of the ten sellable-product profiles (`memory-budget.json`'s own
`profiles` list is unchanged by it), so it costs this box nothing until you ask for it. Turning
it on is the same shape as any other profile, plus the one thing every other profile does not
need: a human, because there is no self-serve sign-up for staff.

**A box already running `COMPOSE_PROFILES=*` ("every profile") turns this on too, today, with no
further change.** `admin` is a real compose profile like any other; the wildcard expansion
`preflight.sh` does reads it straight off `docker compose config --profiles`, which does not stop
at the ten sellable products. If that is not the intended moment to expose the admin surface, set
`COMPOSE_PROFILES` (or `EDGE_PROFILES`) to an explicit list instead of `*` before this ref reaches
that box.

1. **DNS.** `admin.opsgrids.com` → the box's reserved IP, A record only, not proxied (see "DNS
   for the platform zone" above — the row is already in that table). Owner does this by hand in
   Hostinger, same as every other platform name.

2. **`COMPOSE_PROFILES` (or `EDGE_PROFILES`).** Add `admin` to the box's `.env`, comma-separated
   with whatever else is already there:
   ```
   COMPOSE_PROFILES=inventory,admin
   ```

3. **`TLS_PLATFORM_HOSTS`.** `.env.example` lists `admin.opsgrids.com` by default now; a box
   whose `.env` predates this change needs it appended by hand, or preflight fails with "the
   Caddyfile serves admin.opsgrids.com but TLS_PLATFORM_HOSTS does not list it" the moment the
   site block reaches this box (which it does on every deploy from this ref, whether or not the
   profile is on — a Caddyfile site block is not profile-gated, only what it proxies to is).

4. **`EDGE_BUDGET_ACCEPTED_MB`.** Run `preflight.sh` after steps 2-3 (before `deploy`, which runs
   it for you anyway) and read what it prints for the post-deploy total. The admin profile itself
   now prices at **197 MB** (`memory_budget_totals.py memory-budget.json --profiles admin` minus
   the always-on core total below — down from 286 MB: `console` folds `console-worker`'s one
   event consumer in the same way the worker-folding follow-up folded ten product services' own
   workers in, `RUN_CONSUMERS_IN_API`, so it is one `api_with_worker` container (99 MB) plus
   `admin-gateway`'s plain `api` (98 MB), not two `api` containers and a separate `background`
   one): added to whatever this box already runs, e.g. `inventory` alone
   (1870 MB with the worker fold applied → **2067 MB**, 53.5% of the documented 3862 MB box —
   comfortably under the 85% soft line) or every sellable profile at once
   (3747 MB, per this file's own "what this release costs" table → **3944 MB**, 102.1% — just
   over the 100% hard line, and comfortably under the 4827 MB / 125% absolute ceiling preflight
   enforces: `EDGE_BUDGET_ACCEPTED_MB=3944` covers it, `warn`ing rather than refusing). Which of
   those you are near depends entirely on what this box already sells — read the number
   `preflight.sh` prints for *your* `COMPOSE_PROFILES`, do not assume either figure above, and if
   it refuses outright at the absolute ceiling the fix is de-listing or consolidating a product
   first, not raising the accepted number past what it will let you.

5. **Deploy.**
   ```bash
   make deploy HOST=deploy@<ip> CONFIRM=edge.opsgrids.com
   ```
   `remote.sh migrate` picks up `console`'s schema automatically (`SERVICES_WITH_SCHEMA` now
   names it); `reconcile-roles` creates `opsgrids_console` and its directory reader
   `opsgrids_console_directory` on a box whose Postgres volume predates this release, the same
   way it already does for every other service added after first boot ("New service on an
   existing box", above).

6. **Publish the staff SPA.**
   ```bash
   make edge-web HOST=deploy@<ip> CONFIRM=edge.opsgrids.com APP=internal
   ```
   Ships `frontend/opsgrids-internal`'s build to `/srv/web/internal/current`, which is what
   `admin.opsgrids.com`'s Caddyfile block serves. Until this runs, the site answers "The
   OpsGrids admin console is not published on this box yet." rather than a 404 or a 502 — the
   same `handle_errors` shape every other named platform app uses before its own first publish.

7. **Create the first staff operator.** There is no seed for this in production —
   `app.bootstrap.seed_dev_owner` (`bin/seed-staff`) refuses outright outside
   `local`/`test`/`ci` on purpose, so a real box never gets a known-password account. Call the
   same underlying, environment-agnostic function it wraps
   (`app.bootstrap.bootstrap_staff_owner`) directly, once, from an interactive prompt —
   **never inline in the command itself**: a password typed as a `python -c` argument is written
   to this shell's own history file in plain text the moment the command runs, which
   `getpass.getpass()` avoids by reading it from the terminal with no echo and nowhere it is
   ever stored. This needs a real TTY to prompt on, so — unlike every other one-off `docker
   compose run` in this runbook — leave off `-T`:
   ```bash
   docker compose -f compose.prod.yml --env-file .env run --rm --no-deps auth python3 -c "
   import asyncio
   import getpass
   from app.bootstrap import bootstrap_staff_owner
   from app.config import settings
   from platform_core.db import dispose_engine, init_engine, session_scope

   async def main() -> None:
       email = input('Email: ')
       display_name = input('Display name: ')
       password = getpass.getpass('Password (not echoed, not logged): ')
       init_engine(settings())
       try:
           async with session_scope() as session:
               staff, created = await bootstrap_staff_owner(
                   session, email=email, password=password, display_name=display_name,
                   roles=['owner'],
               )
               print(f'staff_id={staff.id} created={created}')
       finally:
           await dispose_engine()

   asyncio.run(main())
   "
   ```
   `roles=['owner']` is every operator capability (the ADR-0003 addendum's role matrix); a
   narrower first account is not useful, since only `owner` may issue another staff member's
   enrolment code. Idempotent by email — re-running with the same email returns the existing row
   (`created=False`) rather than a second account, and the password prompt is skipped on nothing:
   `bootstrap_staff_owner` never rewrites a password on an existing row, so retyping one is safe.

8. **Issue that account's enrolment code.** A password alone is not enough to reach MFA
   enrolment (ADR-0003 addendum); a brand-new staff account has no confirmed factor yet, so it
   is exactly who `bin/issue-enrollment-codes` is for ("The staff enrolment codes", above):
   ```bash
   docker compose -f compose.prod.yml --env-file .env run --rm -T auth \
     python -c "from app.bootstrap import issue_codes_main; issue_codes_main()"
   ```
   Prints `email code` — this one line is the only place the code is ever shown. Hand it to
   yourself out of band if someone else is at the keyboard; there is no "resend".

9. **Enrol MFA and sign in**, at `https://admin.opsgrids.com`, through the console UI's own
   sign-in screen. Underneath, it drives the same operator auth routes any client would:
   `POST /api/v1/auth/operator/login` (email, password) → a `pre_auth_token` with no confirmed
   factor yet → `POST /api/v1/auth/operator/mfa/enrol` (`pre_auth_token`, the enrolment code from
   step 8) → a QR/secret and a `factor_id` → `POST /api/v1/auth/operator/mfa/enrol/confirm`
   (`pre_auth_token`, `factor_id`, the six-digit code from an authenticator app) → a real access
   and refresh token. Every one of these is public on admin-gateway's route table by design —
   there is no token yet to check — and every one of them runs on a pre-auth token that is
   useless for anything else (`services/auth/app/api/routes.py`'s own module docstring on
   `PRE_AUTH`). From here on, sign-in is the ordinary email/password/TOTP flow every later staff
   member also uses, once an `owner` has issued *their* enrolment code the same way (step 8, or
   the audited `POST /auth/operator/enrollment-codes` route from inside the console itself).

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

**Do not roll back past the release that introduced the MFA reseal** (Operator MFA, above). Every
MFA factor on the box is sealed after that deploy, and a ref from before it cannot read a sealed
envelope: its `_accept_second_factor` decodes `secret_enc` as UTF-8 before it looks at recovery
codes, so **every MFA sign-in — authenticator code and recovery code alike — is a 500 until you
roll forward again.** There is no unseal tool. If the release itself is what broke, roll
*forward* with a fix; if you must go back, expect MFA sign-in to be down for the duration and say
so.

**The bundle** (a broken storefront page, but the API is fine) is much faster — one symlink:

```bash
make edge-web-rollback HOST=deploy@<ip> CONFIRM=edge.opsgrids.com
```

**A migration** is the one thing rollback does not undo for you. `make edge-rollback` runs
`remote.sh migrate` at the *older* ref, and it does **not** downgrade. For a service whose
database is already at a revision the older image does not contain, `migrate` used to stop on
alembic's "Can't locate revision"; it now **skips that service with a loud `!!! <svc>: SKIPPED`
warning on stderr** and carries on, leaving the schema exactly as the newer release made it (see
`infrastructure/edge/lib/migration-revisions.sh`). Read those lines: they are the list of schemas
now ahead of the code. If the schema change is what broke things:

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

**Payments: an invoice collected twice.** Not one of the six checks above — this is
`payment-service`'s own structured log line, `payment.invoice.collected_twice` (ERROR), and an
audit row of the same action in `payment.audit_logs` (ADR-0004, PAY-A round 2 — owner decision:
same-payment `failed -> captured` is applied automatically; a double collection it or a dunning
race causes is alerted, never auto-corrected). It fires when a bank's late authorisation settles
an invoice a dunning retry already captured, in either order — two real charges, both genuinely
captured at the provider, for one invoice.

`make edge-logs HOST=… CONFIRM=… SERVICE=payment | grep payment.invoice.collected_twice` finds it.
The audit row is the fuller record — from an admin/BYPASSRLS connection,
`SELECT * FROM payment.audit_logs WHERE action = 'payment.invoice.collected_twice' ORDER BY at
DESC` — and its `after` names the invoice, every *other* charge that also captured it, and the
amount and currency each one collected. **Nothing here refunds anything automatically**, by
design: confirm both charges at the provider (the Razorpay dashboard, by their
`provider_payment_id`) and refund whichever one should not have collected, the same way any other
refund reaches the customer — `payment`'s own refund path (`services.create_refund`) is exactly
what to call once the charge to refund is decided.

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
make edge-logs   HOST=… CONFIRM=… SERVICE=storefront | grep domain.sweep | tail -20   # is the sweep running?
```

**What each failure looks like:**

| Symptom | What it means |
|---|---|
| Browser TLS error on a merchant's domain, `ask` returning 403 | The name is not `verified`/`live`/`failing` in the database, or the store is not published. The origin is refusing on purpose |
| Browser TLS error, `ask` returning 200 | Issuance is failing. Usually the merchant's DNS does not point here, or port 80 is blocked, or the record is proxied through Cloudflare |
| The shop loads but every API call 503s | Not TLS. Go to [triage](#is-it-the-app-the-database-caddy-or-dns) |
| A merchant's domain says "pending" forever | The sweep is not running. It lives inside the `storefront` container now (`RUN_SWEEPS_IN_API`; there is no `storefront-cron` to `ps`): does `make edge-logs SERVICE=storefront` show a `storefront.domain.sweep.started` after the last `service.starting`? If not, `docker compose exec storefront printenv RUN_SWEEPS_IN_API` |

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
#    `storefront.domain.sweep.tick_failed` in the `storefront` log is the sweep, not the shop:
#    domains stop being checked (it backs off and retries on its own), nothing that is already
#    live goes down. The sweep shares that container with request traffic — a *repeatedly
#    restarting* `storefront` is the shop.

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
measured steady-state RSS — see the file's own header for the method (a built production image,
run against an isolated Postgres/Redis/RabbitMQ, `/proc/1/status`'s `VmRSS` read after startup
settles; never the shared dev database). `preflight.sh` prices **the box this deploy is about to
leave behind** — containers that are running, restarting or paused today and that the new compose
file still defines, plus whatever the declared profiles configure (`post_deploy_compute`,
`lib/memory-budget.sh`; the same function `remote.sh record-budget` uses) — against the box's live
`/proc/meminfo`, and prints one of:

- **ok** — comfortably under budget.
- **warn** — over the 85% soft line, or over the 100% hard line **with an owner-accepted ceiling
  that covers it** (below). The deploy proceeds.
- **refuse** — over the 100% hard line and either no ceiling is set, or the deploy is over it, or
  the total is over the absolute cap (`host.max_accepted_percent`, 125% of MemTotal).

**The gate fails closed.** If it cannot see — `docker ps` failing, `docker compose config
--services` exiting non-zero (compose's own error is printed above the row), an empty service
list, or an unreadable `MemTotal` — the row is `FAIL memory budget could not be priced — <why>`,
never a green "0M (0%)". Fix the reason and re-run; nothing was changed.

All figures below are **MiB, from images built on a laptop (arm64)**: the box is amd64. They are
the best measurement this repository has, not the box's own. Before writing a ceiling, read the
box: `free -m` and `docker stats --no-stream --format '{{.Name}} {{.MemUsage}}'` (read-only).

**How to read the box — a rule, not a feeling.** Run this once, on the box, at a quiet hour, on the
current production ref (the 40 containers priced at 4044 MB below):

```bash
docker stats --no-stream --format '{{.MemUsage}}' | awk '{ u=$1; v=u+0;
  if (u ~ /GiB$/) v*=1024; else if (u ~ /KiB$/) v/=1024; else if (u !~ /MiB$/) v/=1048576;
  s+=v } END { printf "%.0f MiB\n", s }'
vmstat 5 3        # the si / so columns: swap-in / swap-out per second
```

1. **Scale the release.** The real sum is the measured cost of whatever `preflight.sh` prices for
   the box's own declared profiles right now — read it off `make edge-preflight`'s own output, not
   off any number printed in this file, which is a worked example for the ten-profile-plus-`admin`
   case (3944 MB) as of this commit, not a live figure. If the box's real measured sum is higher
   than what preflight prices, the release is about **preflight's figure x measured / preflight's
   figure** — the arm64 prices this repository ships are the best available, not the box's own.
2. **If that scaled figure is above 4827 MB (the 125% cap), do not write a ceiling.** Preflight
   will refuse any total above the cap even with one, and a box that far over is not "swapping
   more", it is killing things: **resize or de-list** instead. Do the same if `vmstat 5` shows
   `si`/`so` above zero at a quiet hour — the box is already thrashing before this release changes
   anything.
3. **Otherwise write the number preflight printed**, not the scaled one: the ceiling is what
   preflight prices, and preflight's prices are what the tests pin. The scaled figure is for
   deciding *whether* to run at all.

### What this release costs, on the merged tree (the worker-folding follow-up, 2026-09-24)

Re-priced from the real images: **api 98, api_with_worker 99, background 90, ops-monitor 93**
(api_with_worker is new this round — see memory-budget.json's own `kinds` header for the
account/notification before-after measurement). Against MemTotal **3862 MB** (soft line 3282,
hard line 3862, cap 4827):

| profiles enabled | this profile adds | cumulative total | of MemTotal |
|---|---|---|---|
| core (auth … ops-monitor, no profile) | — | 1772 | 45.9% |
| + storefront | 312 | 2084 | 54.0% |
| + inventory | 98 | 2182 | 56.5% |
| + hrms | 395 | 2577 | 66.7% |
| + crm | 295 | 2872 | 74.4% |
| + pulseboard | 294 | 3166 | 82.0% |
| + projects | 98 | 3264 | 84.5% |
| + support | 98 | 3362 | 87.1% |
| + marketing | 98 | 3460 | 89.6% |
| + finance | 189 | 3649 | 94.5% |
| **+ itsm — all ten, what this box declares** | 98 | **3747** | **97.0% — under the hard line, still over the soft one** |
| + every `planned` backlog item (+956) | | 4703 | 121.8% |

**This is the worker-folding follow-up's own effect, on top of the memory-headroom slice's
release** (which priced the same ten profiles at 4581 MB / 118.6% — over the hard line, 719 MB
over MemTotal, and the reason the owner-decision section below existed at all). Folding
`account-worker`, `product-worker`, `subscription-worker`, `payment-worker`,
`notification-worker`, `approval-worker`, `attendance-worker`, `leave-worker`, `customer-worker`
and `finance-worker`'s loops into their own API containers (`RUN_CONSUMERS_IN_API`, on by
default; the ten containers themselves still exist, unchanged, behind the `split-workers`
profile as the rollback — see "Rolling back the worker fold", below) removes ten containers at
`kinds.background`'s 90 MB (900 MB) and adds back only ten containers' worth of
`kinds.api_with_worker` minus `kinds.api` (99 - 98 = 1 MB each, 10 MB) — **net -890 MB, from
4581 to 3691, 118.6% to 95.6%.** The ten-profile scenario no longer needs an owner decision on
its own (see below); it is still tight, which is honest for "sell everything this platform can
sell on this box," not a bug.

**The minio-memory fix (2026-09-25) adds +56 MB on top of that**, bringing the real ten-profile
total to **3747 MB / 97.0%** (table above). minio's own price moved from a never-measured 60 MB
guess to 116 MB, the real RSS production showed right before its 128 MiB `mem_limit` OOM-killed
it about once a minute — see "MinIO's memory (2026-09-25)" below and `memory-budget.json`'s own
`datastores.minio.source`. Still comfortably under the 100% hard line and the 125% / 4827 MB cap.

Where the box is today, priced the ten profiles the way `preflight.sh` actually prices a running
box (`post_deploy_compute`, not the projection table above) will read differently from either
figure until this release is actually deployed — read `make edge-status` and this section's
"How to read the box" for the box's own live numbers, always, before writing a ceiling.

### The owner decision: `EDGE_BUDGET_ACCEPTED_MB` (B2, review round 2; restated round 3; the
worker-folding follow-up moved the ten-profile figure under the hard line, 2026-09-24)

**The ten profiles alone no longer need a ceiling.** At 3747 MB / 97.0% they are under the 100%
hard line (though still over the 85% soft one — `preflight` warns, and proceeds, with no
`EDGE_BUDGET_ACCEPTED_MB` set at all). If the owner wants an explicit ceiling anyway, silencing
the warn rather than reading past it on every deploy, the ten-profile figure alone is
`EDGE_BUDGET_ACCEPTED_MB=3747` — but nothing requires it now. A ceiling is only a live question
again once something is added on top — the `admin` profile (admin-gateway + console, console-worker folded into console
the same way this release folds the ten product workers in: +98 + 99 = 197 MB) brings the total to
**3944 MB / 102.1%** — just over the hard line — or once enough of the `planned` backlog above
lands. Nothing in a repository can shrink either scenario to fit on its own; what can change is
which of three things the owner chooses:

1. **Resize the VM** — the only option that adds headroom rather than redistributing it. The
   memory erratum (ADR-0013) recorded that the owner would not resize this box; worth asking again
   now that ten profiles plus `admin` is only 82 MB over the hard line (102.1%) and comfortably
   under the 125% cap (4827 MB) either way. An 8 GB VM (~7.7 GB MemTotal) puts the ten profiles
   near 49% and the whole planned backlog near 82%.
2. **De-list products** — drop their profiles from `COMPOSE_PROFILES`, *after* confirming with the
   catalogue that no organization has a live activation of them (preflight refuses otherwise: a
   paying customer would lose their product). The "adds" column above is what each one frees.
3. **Accept a deliberate overcommit** — set `EDGE_BUDGET_ACCEPTED_MB` in `.env` to the number
   preflight's own projection printed for the deploy actually being made (ten profiles plus
   `admin`: `EDGE_BUDGET_ACCEPTED_MB=3944`), *after reading it*. Understand what it means: the
   working set exceeds RAM by a little (82 MB at 102.1%), the kernel decides which pages live in
   swap, and the box swaps a little more than it otherwise would. A deploy over the hard line then
   warns, loudly, naming the overcommit in MB and to one decimal, instead of refusing; a deploy
   asking for even one MB more than the ceiling still refuses.

**The ceiling is a decision record, not a limit to leave loose.** Its value is **exactly the
post-deploy total preflight printed for the commit being deployed** — no more. So:

- Preflight says when it is larger: `info EDGE_BUDGET_ACCEPTED_MB (…M) is NM above this deploy's
  total` — slack nobody decided on; set it to the printed total.
- It says when it is not needed at all (`set but not needed: this deploy is under the hard line`)
  — clear it once the box is resized or a product de-listed, so a later deploy that does go over is
  not silently pre-approved.
- It is **bounded**: a ceiling above `host.max_accepted_percent` of MemTotal (125% = 4827 MB) is a
  FAILED row naming the cap (`38940` for `3894` is a typo, not a decision), and a total above the
  cap is refused even with a ceiling. 125% is where the kernel stops swapping and starts killing.
- Raising it is a diff to `.env` — the audit trail. Nothing a deploy does moves it.

**Do this before the first deploy of this release**, not after it refuses:

```bash
make edge-preflight HOST=… CONFIRM=…     # read-only; prints the projection and, if it applies,
                                         # exactly which of the three options it is asking for
```

Owner pre-deploy key list — every one of these is either set deliberately or knowingly absent.
**A missing key fails preflight before anything changes** (`missing or empty in .env: …`), so the
worst case of forgetting one is a re-run, but `.env.example` has 22 keys the production ref's did
not (20 of them must be present on the box), and knowing them beforehand is cheaper. **Quote any
value that contains a space or a shell metacharacter** (`OPS_ALERT_EMAIL="ops team <ops@…>"`; the
characters are `; & | < > ( )`, a backtick and `$(`): `.env` is sourced as shell, and preflight fails
an unquoted one — and text after a closing quote, and CRLF line endings — by name, before reading
the file, instead of letting bash run the rest as a command (exit 127, no footer). Its own reading
of the file (last assignment wins, an inline ` # comment` is dropped) is what `remote.sh` uses too:

| Key | State | Note |
|---|---|---|
| `COMPOSE_PROFILES` | **required, explicit** | every product this box sells, written by hand (M4). `*` is valid for compose and is expanded by preflight |
| `EDGE_PROFILES` | optional, may be absent | overrides `COMPOSE_PROFILES` when a profile must run ahead of its first sale |
| `EDGE_BUDGET_ACCEPTED_MB` | optional, may be absent | the decision above; absent means "no acceptance" — a deploy over the hard line refuses |
| `RUN_SWEEPS_IN_API` | defaulted `true` by `compose.prod.yml`; not a `.env` key | leave alone: **`false` STOPS the operations, storefront and document sweeps** — there is no `*-cron` container left to run them. Preflight warns when it is not `true` |
| `EDGE_UP_PARALLEL_LIMIT` | optional, commented out in `.env.example` | how many containers `up` starts at once (default 4); set `2` for this release's deploy (below) |
| `OPS_ALERT_EMAIL` | key present, may be empty | empty = ops-monitor checks and logs but pages nobody (preflight warns in capitals); set it to an inbox a human reads |
| `OPS_MONITOR_API_CONTAINERS`, `OPS_MONITOR_DLQ_QUEUES` | keys present, normally empty | empty = derive from `COMPOSE_PROFILES`; setting either REPLACES the derived list |
| `SMTP_USE_SSL` | key present, `false` | `true` only for a provider that offers SMTPS on 465 and not STARTTLS; mutually exclusive with `SMTP_USE_TLS` |
| `ENGAGE_UNSUBSCRIBE_SECRET`, `ENGAGE_PUBLIC_API_BASE` | **need real values** | a fresh secret (rotating it breaks every unsubscribe link already sent); an `https://` base a mailbox can reach |
| `MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `DOCUMENT_S3_ACCESS_KEY`, `DOCUMENT_S3_SECRET_KEY` | **need real values** | generate fresh; both secrets ≥ 8 characters (MinIO's floor) |
| `DOCUMENT_S3_ORPHAN_ACCESS_KEY`, `DOCUMENT_S3_ORPHAN_SECRET_KEY` | keys present, may be empty | the by-hand orphan pass only; held by no always-on container |
| `S3_PUBLIC_BUCKET`, `PUBLIC_ASSET_BASE_URL` | defaults are right | `PUBLIC_ASSET_BASE_URL` must be exactly `https://<EDGE_HOST>/<bucket>` |
| `ASSET_PURGE_RETENTION_DAYS`, `DOCUMENT_ORPHAN_SWEEP_ENABLED`, `DOCUMENT_ORPHAN_SWEEP_DRY_RUN` | defaults are right | `30`, `false`, `true` |
| `BILLING_TAX_CODE`, `BILLING_TAX_JURISDICTION` | keys present, empty is the honest default | empty = the billing run writes no tax line; set both only once a `billing.tax_rates` row exists for them ("The billing run", below) |
| `ADMIN_GATEWAY_HOP_KEY` | **needs a real value, and `auth` will not start without it** | `openssl rand -hex 32`, at least 32 bytes and **different from `INTERNAL_SERVICE_TOKEN`** (preflight's secret-strength check refuses a weak or reused one). See "ADMIN_GATEWAY_HOP_KEY — auth refuses to start without it" under Operator MFA |

`last-ok` — `${EDGE_STATE_DIR:-/srv/opsgrids/state}/edge-budget.last-ok`, written by `remote.sh
record-budget` after each healthy deploy — is **informational only**: preflight prints it ("last
successful deploy priced at …") and never reads it for a verdict. Recovery after a failed health
check needs nothing special: the ceiling is `.env`'s and does not move.

**Every product this box sells must have its profile listed explicitly in `EDGE_PROFILES` or
`COMPOSE_PROFILES` — neither is ever computed for you (M4, review round 1).** An earlier version
computed `COMPOSE_PROFILES` from the catalogue's own activations when neither was set. That was
removed: `docker compose up` does not stop a container whose profile is no longer listed, so a
computed value never actually changed what ran — it only changed what preflight *believed* was
running. Write the full list by hand and keep it current.

`preflight.sh` still asks the catalogue two related, but separate, questions, both only once
Postgres exists:
- **Commercial listing agreement**: an `available` product with no profile enabled is a 504 the
  customer finds — **refused**.
- **Real-customer-impact advisory**: a product with at least one activation — `trial`, `active`
  **or `suspended`** (a suspended one flips back to `active` the moment a payment lands, onto a
  service this box would not be running: a paying customer returning to a 504) — and never an
  `e2e-…` test organization, whose profile is not enabled is **refused**, because turning it off
  would take a paying customer's product away. A profile that is enabled, has no activations, and
  is not even sold in the catalogue is reported as **information** only, safe to consider
  trimming, never forced. If the activations query itself fails, that is a **warning**, never
  silently "zero activations".

A container running with no `memory-budget.json` entry (a hotfix on the box, a compose file edited
by hand) is priced as `kinds.background` and named in a warning — never treated as free.

Never raise `hard_budget_percent` in `memory-budget.json` to make a refuse go away — that number is
the line past which the kernel starts reclaiming from Postgres, RabbitMQ or Redis instead of an
idle API container, and moving it does not move that fact.

`tests/infra/test_preflight_memory.py` runs the real `preflight.sh` and `remote.sh` against a fake
`docker` on `PATH` and pins each of the above; `tests/infra/test_compose_memory_budget.py`
(`make test-infra`) is the CI check that every compose service has a budget entry, and
`tests/infra/test_planned_headroom.py` that the RUNBOOK's worked example below is the number
preflight would print. Its worst-case test (`test_every_sellable_profile_at_once_is_refused_not_
silently_allowed`) *expects* a refuse for every profile at once, which is why `.env.example`'s
`COMPOSE_PROFILES` ships empty rather than "everything on."

### The three sweeps that live inside their API now: how to see them, and their gauges

`operations` (stock reconciliation), `storefront` (custom-domain verification) and `document`
(retention purge) run their sweep as a background task inside their own API container — there is
no `*-cron` container to `ps`. A sweep that dies or fails every tick leaves the API answering
healthily, so each API's own `/metrics` (reachable only on the compose `back` network) carries,
per sweep, labelled `sweep="<log-event prefix>"`:

| Gauge | Meaning | Labels |
|---|---|---|
| `sweep_alive` | 1 while the sweep's task is running, 0 once it has exited — or if it was never started because its precondition failed | `inventory.reconcile`, `storefront.domain.sweep`, `document.sweep` |
| `sweep_consecutive_failures` | failed ticks in a row (document also counts a tick where every purge attempt failed) | same |
| `sweep_last_success_timestamp_seconds` | Unix time of the last good tick; `0` = none since the process started | same |

These are for the **ops-monitor follow-up** (an alert on `sweep_alive == 0`, on a climbing
`sweep_consecutive_failures`, or on `time() - sweep_last_success_timestamp_seconds` beyond a few
poll intervals); nothing reads them yet, and this slice deliberately does not edit ops-monitor.
Until then: a sweep that exits by itself logs `supervise.background_sweep_exited` at **error**
(`make edge-logs SERVICE=<api> | grep background_sweep_exited`), and a sweep whose precondition
failed at startup logs `<sweep>.not_started` at error while **the API still starts** — a
misconfigured outbox role must cost a nightly report, never the customer-facing API.

Each in-API sweep runs on its own one-connection database engine (a bulkhead), so it can neither
take one of the API's 3 + 2 request-pool connections nor be starved by them — measured cost: one
extra Postgres connection per API, opened lazily.

### Rolling out this release on the box

What changes on the box: `--remove-orphans` removes **`approval-cron` and `subscription-cron`** —
the only two services that are running on the production ref and no longer exist in the file
(`notification-cron`, `operations-cron` and `document-cron` never reached this box, and
`storefront-cron` sat behind the unused `sweep` profile; a *deleted* service is an orphan, a merely
profile-disabled one is not, which is why preflight prices what is running). Eight containers are
added (717 MB), and `approval-worker`/`notification-worker` gain a stricter supervisor.

**`up` is not staged, and this release lands ~+540 MB at once on a box already swapping** (~+450 MB
at the old prices). So, for this one deploy:

```bash
# 0. The owner decision above — resize, de-list, or set EDGE_BUDGET_ACCEPTED_MB — BEFORE deploying.
#    Add the 22 new keys (20 must be present; EDGE_PROFILES and EDGE_BUDGET_ACCEPTED_MB may be
#    absent) from the table above, and confirm COMPOSE_PROFILES lists every product sold.
#    Then, on the box, for this deploy only (and remove the line afterwards):
#        echo 'EDGE_UP_PARALLEL_LIMIT=2' >> infrastructure/edge/.env

# 1. Read-only rehearsal: nothing changes on the box.
make edge-preflight HOST=… CONFIRM=…
#    Look for:  warn  memory budget (post-deploy: …): 4581M against 3862M MemTotal: 719M over MemTotal (118.6%)
#                     — OVER THE HARD BUDGET (100% = 3862M), accepted: EDGE_BUDGET_ACCEPTED_MB=4581 covers it.
#    (or the FAIL line naming what to set). A FAIL for "product(s) with active organizations have
#    no service running" means a profile is missing from COMPOSE_PROFILES — fix that first.

# 2. Deploy in a quiet window — off-peak, nobody mid-import. In a second session on the box watch
#    the swap columns while it runs:   vmstat 5      (si/so climbing and staying up = thrash)
make deploy HOST=… CONFIRM=…

# 3. Verify the merged sweeps started — logs, not `docker compose ps`:
C="docker compose -f compose.prod.yml --env-file .env"
$C logs --tail 200 notification-worker | grep -E 'notification.worker.starting|notification.cron.started'
$C logs --tail 200 operations | grep inventory.reconcile.started
$C logs --tail 200 storefront | grep storefront.domain.sweep.started
$C logs --tail 200 document   | grep document.sweep.started
#    A missing line means that container's RUN_SWEEPS_IN_API is not true, its sweep's precondition
#    failed (look for `<sweep>.not_started`), or the container restarted since — check `printenv`.

# 4. Confirm the two crons are gone and the recorded number moved:
$C ps | grep -c -- '-cron'                                         # 0
cat "${EDGE_STATE_DIR:-/srv/opsgrids/state}/edge-budget.last-ok"     # informational only
```

**There is no automatic rollback.** If `health` does not settle within `HEALTH_TIMEOUT` (600 s) the
deploy exits non-zero with the containers as they are — no `record_ref`, nothing undone. On a box
this tight a slow start is expected (containers importing on two cores while swapping); read the
unhealthy list before deciding. The rollback is the operator's: `make edge-rollback HOST=…
CONFIRM=…`.

**Rollback — and why this release's is not clean.** `make edge-rollback` redeploys the previous good
ref (`47143a0`): its compose file recreates `approval-cron` and `subscription-cron`, and
`--remove-orphans` removes the eight containers this release added (the previous image ignores
`RUN_SWEEPS_IN_API`). This slice changes no schema, and a sweep briefly running in two places
during either direction is safe (each is idempotent or claims its rows with `SKIP LOCKED`). **But
`47143a0` predates the MFA reseal** that `remote.sh migrate` runs in this release (Operator MFA,
above): once the factors are sealed, a ref from before it cannot read them, and *every* MFA
sign-in is a 500 until you roll forward again — the warning under "Roll back a bad deploy" applies
to this deploy in full, and applies from the moment `migrate` finishes, i.e. before `up`. So for
this release: **prefer rolling forward with a fix**; if you must go back, expect MFA sign-in to be
down for the duration and say so. That is one more reason to settle the memory decision and the
`.env` keys *before* deploying — the deploy has a point of no easy return that a slow `up` does not
help with. If the rollback target predates the *explicit-profiles* change and this box's
`COMPOSE_PROFILES` is not the full list, write it back by hand first — the older `remote.sh` may
still expect a computed default that no longer exists.

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

$C stop auth account product storefront app-gateway   # stop writing
# Keep Caddy up: buyers get an error page from the origin, not a TLS failure.
gunzip -c /srv/opsgrids/backups/<chosen>.sql.gz | \
  $C exec -T postgres psql -v ON_ERROR_STOP=1 -U opsgrids -d opsgrids
$C start auth account product storefront app-gateway
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
- **Resolved: the custom-domain sweep is built** (ADR-0012 §6, `services/storefront/app/cron.py`)
  and, since the memory-headroom slice's fix round 2, runs as a supervised background task
  inside the `storefront` API container (`RUN_SWEEPS_IN_API`) — there is no `storefront-cron`
  container any more. A merchant's claim advances `unverified` → `verified` on the sweep's own
  schedule; if one sits at `pending` forever, see "A merchant's domain says pending forever"
  above. **`<slug>.stores.opsgrids.com` is unaffected** — it needs no sweep.
- **No merchant admin SPA on this box.** The edge serves the anonymous buyer bundle only. The
  API is reachable at `https://<EDGE_HOST>/api/…`; the merchant console is ADR-0001's and is
  served elsewhere.
- **Resolved: `document` and `minio` are on this box, behind the `storefront` profile** (see
  "Document and MinIO" above), **and resolved: ADR-0009 §8's purge sweep is built and
  deployed**, inside the `document` API container (see "The purge sweep" above) — a soft-deleted asset's object
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
| Weekly | Caddy log for ACME errors; `storefront.domain.sweep.started` present in the `storefront` log since its last start; disk |
| Monthly | `apt-get update && apt-get upgrade` and **a planned reboot window** — kernel updates are a full outage, so choose when rather than having it chosen for you |
| Quarterly | The restore drill above. A backup nobody has restored is a hypothesis |
| When a merchant asks about uptime in writing | Stop. That is ADR-0012's stated trigger to move off one box |

## Development databases (not the box)

Nothing here touches production; it is recorded in this file only because it is the one runbook
the repository has.

**CRM branch databases that ran an earlier `slice/crm-leads` migration must be recreated.** Two
migrations were rewritten in place while that branch was still unmerged and never deployed
(ADR-0017): customer's `e230c025337e` (first a unique index on `lower(name)`, then a plain
non-unique index on `lower(name)`, now a plain non-unique index on `lower(btrim(name))` that
leaves `uq_companies_org_name` alone — so a database from *any* earlier point on the branch is
stale) and lead's whole chain (the deleted `e6b3c5bf7755` is gone from the tree, so a database
stamped with it cannot even resolve its own revision). Alembic goes by the revision id in
`alembic_version`, not by what the migration used to do, so a database that applied the old
versions is silently wrong (customer) or refuses to migrate at all (lead). The fix is to throw
the two schemas away and migrate again.

`DROP SCHEMA ... CASCADE` is not undoable, so name the database explicitly and prove which one
you are connected to *before* the drop — there is deliberately no environment variable to lean
on here:

```bash
# 1. The DSN of the branch database you mean to wipe. `psql` wants the plain `postgresql://`
#    form (the services' own settings use `postgresql+asyncpg://`). Never `opsgrids`, never
#    anything shared or anything you did not create for this branch.
BRANCH_DSN='postgresql://<admin-role>:<password>@<host>:<port>/<branch-database-name>'

# 2. Prove it. This must print exactly the branch database's name — stop if it prints anything
#    else, or if the connection fails.
psql "$BRANCH_DSN" -Atc 'SELECT current_database()'

# 3. Only then: drop the two schemas and migrate again, pointing the services at the same
#    database (`DATABASE_URL` in the `postgresql+asyncpg://` form of the DSN above).
psql "$BRANCH_DSN" -c 'DROP SCHEMA customer CASCADE' -c 'DROP SCHEMA lead CASCADE'
make migrate
```

`DROP SCHEMA ... CASCADE` deletes that service's `alembic_version` table with everything else,
which is what makes the re-migrate start from nothing. Development data only — seed it again
afterwards. A database created fresh from the branch as it stands needs none of this.
