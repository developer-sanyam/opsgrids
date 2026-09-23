# Document service

Part of the OpsGrids platform. Owns the `document` schema. Fronted by `app-gateway` at
`/api/v1/documents`, with no `product` key — a platform surface every customer has.

```bash
make run SERVICE=document PORT=8150   # API
make worker SERVICE=document          # event consumer
make relay SERVICE=document           # outbox relay
make cron SERVICE=document            # the purge sweep (ADR-0009 §8, §9)
```

## Read this before the name misleads you

**This is the platform's public asset store, not a document vault** (ADR-0009 §2).

Every object it writes goes into `S3_PUBLIC_BUCKET`, which is **world-readable in its entirety**.
Anyone holding an object's URL can fetch it, forever, until it is deleted. There is no
per-request authorization, no expiry, and no revocation short of deletion. That is a decision,
not a leak: a storefront is a shopfront, and its hero image exists to be seen by an anonymous
buyer who has never heard of us.

The API offers **no way to store a private object**. The private half — `S3_PRIVATE_BUCKET`, with
no anonymous policy and a short-lived presigned GET behind an authorized route — is named in
ADR-0009 §2 and deliberately unbuilt, so the failure mode for a payslip or an ID scan is "the
feature is missing" rather than "the confidential file went somewhere surprising".

## Owns

| Table | What it is |
|---|---|
| `document.assets` | One merchant-uploaded image: its object key, the facts about the **stored** bytes (content type, size, dimensions, sha256), who uploaded it, and its soft-delete and purge timestamps |

Every row carries `organization_id` under `ENABLE` + `FORCE` row-level security.

### The rules that are not style preferences

- **The object key is never accepted from a caller.** Every route addresses an asset by row id;
  the row is fetched under RLS; the key is read off the row. A `?key=` parameter would be a
  cross-tenant delete primitive that RLS cannot see, because RLS does not know what a bucket is.
- **The bytes decide, and the bytes are re-encoded.** The declared `Content-Type` and the
  filename extension are ignored. Pillow decodes; the stored content type is what it says. Every
  image is re-encoded, which strips EXIF (including GPS) and neutralises polyglots. JPEG, PNG and
  WebP only — **SVG is refused permanently**, because it is a script container.
- **Object first, row second.** An upload is two writes that cannot be one transaction, and the
  order chooses which failure to have. An orphan object is invisible and sweepable; a library row
  pointing at nothing is neither.
- **Soft delete leaves the object alone.** "Delete" means "take it out of my library", not "break
  my live store". `?purge=true` is the immediate takedown, for the merchant who has just made a
  customer's ID scan world-readable.

## API

| Route | Permission | Notes |
|---|---|---|
| `POST /api/v1/documents/assets` | `core.content.asset.write` @ ORG | `multipart/form-data`, exactly one `file` part, ≤ 5 MiB. Rate limited per organization |
| `GET /api/v1/documents/assets` | `core.content.asset.read` @ ORG | Paginated, newest first, excludes soft-deleted. Carries storage usage |
| `GET /api/v1/documents/assets/{id}` | `core.content.asset.read` @ ORG | Metadata, **not bytes**. This service never serves image bytes |
| `DELETE /api/v1/documents/assets/{id}` | `core.content.asset.write` @ ORG | Soft delete; `?purge=true` for an immediate takedown |

Both permissions pin `default_scope` **and** `max_scope` at ORG, so OWN, TEAM and LOCATION are
unrepresentable rather than merely ungranted. There is no separate `delete` permission: **anyone
who can upload can also take an image down.**

### What the frontend stores

`AssetOut.public_path` is a root-relative `/a/{organization_id}/{asset_id}`. **That** is what
belongs in a store document. `public_url` is absolute and is for preview only — persisting it
bakes the hostname into merchant data, which is exactly what `public_path` exists to avoid.

`/a` is a sentinel, not a path segment of the object key: a resolver drops it and joins the rest
onto `ASSET_BASE`, so `/a/{org}/{asset}` resolves to `{ASSET_BASE}/{org}/{asset}`.

## Publishes

Nothing. ADR-0009 §7 refuses to emit `core.document.asset.uploaded`: an event type with no
consumer is a contract we are obliged to keep compatible forever in exchange for nothing, and the
usage history is exactly reconstructable from `assets` whenever a meter needs it.

## Consumes

Nothing yet.

## Deploying this

**A `core.*` permission does not reach an existing tenant on its own.** `install_registration`
skips role codes that already exist, so without ADR-0007's reconcile every upload 403s in every
live organization while a freshly created one works perfectly. Export the catalogue **before** the
reconcile — a grant naming a permission row that does not exist is dropped without error.

Locally, `make infra` creates `opsgrids-public` and applies its anonymous read-object policy. In
production that is a bucket policy granting `s3:GetObject` (and **not** `s3:ListBucket`) to `*`,
with `BlockPublicAccess` disabled for that bucket only.

## The purge sweep

`bin/cron` runs `app/cron.py` (ADR-0009 §8, and its 2026-09-23 addendum's §9): every asset
soft-deleted more than `ASSET_PURGE_RETENTION_DAYS` (default 30) days ago has its object
removed from the bucket and `purged_at` set, and — only when `ORPHAN_SWEEP_ENABLED=true` — the
bucket is listed for objects with no `assets` row at all, at least `orphan_sweep_min_age_hours`
old, reported (`ORPHAN_SWEEP_DRY_RUN=true`, the default) or actually deleted. See the ADR
addendum for the credential the orphan pass needs and why it is a second, narrower one than the
API process ever holds, and `infrastructure/edge/RUNBOOK.md`'s "The purge sweep" for how to
check it and run it once by hand in production.

## Not built here, and named so it is not rediscovered

- **A request-body cap in `build_gateway`**, owned by `libs/platform-core`: the gateway buffers
  whole bodies, so this service's 5 MiB limit protects this service and not the gateway.
- **`S3_PRIVATE_BUCKET`** and everything behind it.
