# Getting started

Target: a running platform and a green test suite. Everything is local; nothing here talks to
a cloud service.

## Prerequisites

- **Python 3.13** and [uv](https://docs.astral.sh/uv/)
- **Docker** — for Postgres, Redis, RabbitMQ, MinIO, MailHog
- **Node 20+ and pnpm** — only if you are working on the frontend

## Three commands

```bash
make install     # sync the uv workspace
make infra       # start Postgres, Redis, RabbitMQ, MinIO, MailHog in Docker
make migrate     # apply every service's migrations
```

Then check it:

```bash
make check       # lint + typecheck + tests — this is what CI runs
```

Expect **194 passing tests**. If you get connection errors, `make infra` has not finished
coming up yet; give it a few seconds and retry.

## Ports

The infrastructure ports are **deliberately offset** from the defaults so this stack can run
next to another one without a fight. If you have a local Postgres on 5432, it is not this one.

| Service | Port |
|---|---|
| Postgres | **5434** |
| Redis | **6380** |
| RabbitMQ | **5673** |
| MinIO | **9100** |
| MailHog | **1026** |

Application ports: gateways on `8100` / `8101`, services from `8110` up. The authoritative
mapping is each gateway's `routes.json` — that file resolves a URL prefix to an upstream, so a
service that moves is one edit there rather than a hunt through code.

## Running a service

```bash
make run SERVICE=account PORT=8111
make run SERVICE=auth    PORT=8110
make run SERVICE=app-gateway PORT=8100
```

`make worker SERVICE=<name>` and `make relay SERVICE=<name>` run a service's event consumer and
outbox relay. Note that **no consumers are registered yet** — the machinery exists, the handlers
do not, so a worker currently starts and idles.

## Signing keys

auth-service is the only service that holds a private key. Generate a dev keypair once:

```bash
make devkeys
```

The keys land in `tools/dev-bootstrap/keys/` and are **gitignored**. Every other service
verifies tokens through JWKS and never sees a private key — if you find yourself needing one
somewhere else, something has gone wrong.

## The thing that will bite you first

**`import app` is ambiguous.** Every service exposes a top-level `app` package, so only one can
be on the path in a single process. Run, test and typecheck **per service** — this is why
`make test` and `make typecheck` loop over services rather than running once. If you run
`pytest` from the repo root you will get import errors that look like missing modules and are
not.

Two more, in rough order of how likely they are to catch you:

- **Redis is a hard dependency of any write that changes permissions.** Changing roles or
  revoking a member bumps a counter in Redis and **raises if it cannot**, deliberately: telling
  an administrator the revocation failed beats reporting success while the person keeps access.
  Tests use a fake; a running service needs the real thing.
- **The tenant filter is the only isolation there is.** Row-level security is designed
  ([ADR-0002](adr/0002-row-level-security.md)) and not implemented. If you write a query that
  forgets `organization_id`, nothing underneath will catch it.

## Frontend

```bash
cd frontend && pnpm install
pnpm --filter atmora-website dev     # http://localhost:4300
```

The marketing site is the only frontend that exists. `atmora-ui` and `atmora-client` are
declared workspace packages with **empty `src/` directories** — scaffolding for a design system
and a typed API client that have not been written.
