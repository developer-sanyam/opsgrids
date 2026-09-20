# The edge VM — runbook

One Linux VM serves every merchant's storefront: Caddy, Postgres+PostGIS, Redis, and five
service containers plus one sweep. ADR-0012 §1 is the decision and its reasoning; this file is
what you do about it at 3am.

**Read this first, once, before the box exists.** The parts you will actually need in an
incident are [roll back a bad deploy](#roll-back-a-bad-deploy), [the box is out of
disk](#the-box-is-out-of-disk), [restore from backup](#restore-from-backup) and [is it the app,
the database, Caddy or DNS](#is-it-the-app-the-database-caddy-or-dns).

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
- **Nobody is paged.** There is no alerting. If certificate issuance fails at 3am, nothing
  wakes anybody; see [certificates](#certificates-and-how-a-human-ever-finds-out).
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
| `make edge-status HOST=… CONFIRM=…` | What is running, which ref, how much disk is left |
| `make edge-logs HOST=… CONFIRM=… [SERVICE=caddy] [TAIL=500]` | Tail logs |
| `make edge-rollback HOST=… CONFIRM=… [TO=<sha>]` | Redeploy the previous ref that actually served |
| `make edge-web HOST=… CONFIRM=… [PLACEHOLDER=1]` | Build the buyer bundle here, ship it, swap the symlink |
| `make edge-web-rollback HOST=… CONFIRM=…` | Point the edge back at the previous bundle |
| `make edge-releases HOST=… CONFIRM=…` | Which bundles are on the box, which one is live |
| `make edge-backup HOST=… CONFIRM=…` | Run the nightly backup now |

On the box itself everything is `infrastructure/edge/remote.sh <command>` in
`/srv/opsgrids/repo`, and raw compose is
`docker compose -f compose.prod.yml --env-file .env …` from `infrastructure/edge/`.

---

## Standing up a new box

Roughly 45 minutes, most of it waiting for a build. Steps 1–6 are one-time.

### 1. The VM and its address

Hetzner CX32 (4 vCPU / 8 GB / 80 GB) or a DigitalOcean 4 GB droplet, **Ubuntu 24.04 LTS**, in
whichever region your buyers are.

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

# The only private signing key in the platform.
openssl genpkey -algorithm ed25519 -out /srv/opsgrids/secrets/jwt-ed25519.pem
chmod 600 /srv/opsgrids/secrets/jwt-ed25519.pem
```

Then edit `.env`: fill every empty value, set `EDGE_HOST` to this box's name, `EDGE_RESERVED_IP`
to the real address, `TLS_PLATFORM_HOSTS` to every platform name from the DNS table above
(comma-separated, no wildcard), and `ACME_EMAIL` to **an inbox a human reads** — in slice 1
Let's Encrypt's expiry warning is the only unsolicited notification that exists.

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

---

## Deploying

```bash
make deploy HOST=deploy@<ip> CONFIRM=edge.opsgrids.com REF=origin/main
```

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
6. **migrate** — `alembic upgrade head` for auth, account, product and storefront, **before**
   the new containers serve. For the length of the deploy the old code runs against the new
   schema, so **every migration must be backward compatible with the version currently
   running**: add a nullable column, backfill, and only drop the old one in a later deploy.
   `make migration-roundtrip` locally is what proves the `downgrade()` actually reverts.
7. **up** — containers are recreated. Brief downtime. Not zero.
8. **health** — every container running and healthy within 120 s, or the deploy reports a
   failure and leaves the decision to you.

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

## Certificates, and how a human ever finds out

Nothing is paged. These are the only instruments that exist in slice 1:

1. **The merchant's own domain screen.** Every failed probe writes a reason and a
   `next_check_at` onto the claim row, and the merchant sees exactly what we looked for and
   what we found. This is the fastest path to discovery and it is a customer doing the
   noticing, which is not good enough and is why slice 2 is the pager.
2. **Let's Encrypt's expiry email** to `ACME_EMAIL`, roughly 20 and 7 days before a
   certificate expires, but only when renewal has already been failing. **If that address is
   not read by a human, this design has no alerting at all.**
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

## Restore from backup

If `BACKUP_S3_ENDPOINT` is set (anything other than AWS itself — R2, B2, the provider's own
object store), add `--endpoint-url "$BACKUP_S3_ENDPOINT"` to every `aws s3` command below.
`backup.sh` does that for you; a command you type does not.

Nightly `backup.sh` puts two files in `s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/`: a
gzipped `pg_dump` of the whole database and a tar of Caddy's data volume. The provider's VM
snapshot is the third copy and the one to use when the box itself is gone.

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
  -v /srv/opsgrids/backups:/backup amazon/aws-cli:latest \
  s3 ls "s3://$BACKUP_S3_BUCKET/$BACKUP_S3_PREFIX/"     # pick the newest name, then:
docker run --rm \
  -e AWS_ACCESS_KEY_ID="$BACKUP_S3_ACCESS_KEY_ID" \
  -e AWS_SECRET_ACCESS_KEY="$BACKUP_S3_SECRET_ACCESS_KEY" \
  -e AWS_DEFAULT_REGION="$BACKUP_S3_REGION" \
  -v /srv/opsgrids/backups:/backup amazon/aws-cli:latest \
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

- **No alerting and no pager.** Slice 2.
- **No zero-downtime deploy.** Every deploy that changes a container restarts it.
- **Resolved 2026-09-20 (ADR-0013 slice 1): RabbitMQ, a relay per outbox and the consumers now
  run on this box**, so activation installs a product's roles by event and
  `tools/wire_product.py` is a repair tool again, not a step. The paragraph below is kept as
  the record of why. What is still true: services *not* deployed here (employee, customer,
  approval, …) publish nothing and consume nothing, and a product behind a disabled compose
  profile is exactly as absent as before.
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
- **No document service.** Uploaded images are not served from this box; a storefront that
  references them renders broken images.
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
| After every deploy | `make edge-status` — everything up, disk sane |
| Weekly | Caddy log for ACME errors; `storefront-cron` alive; disk |
| Monthly | `apt-get update && apt-get upgrade` and **a planned reboot window** — kernel updates are a full outage, so choose when rather than having it chosen for you |
| Quarterly | The restore drill above. A backup nobody has restored is a hypothesis |
| When a merchant asks about uptime in writing | Stop. That is ADR-0012's stated trigger to move off one box |
