# infrastructure/

What actually runs the platform outside a developer's laptop.

```
edge/    one Linux VM: Caddy, Postgres+PostGIS, Redis and the five services that serve a
         merchant's storefront on their own domain (ADR-0012)
```

**This contradicts `CLAUDE.md`**, which describes `infrastructure/` as holding Helm charts and
kustomize overlays. ADR-0012 §1 makes that contradiction deliberately and explains it: there is
no Kubernetes cluster, there is nobody to run one, and a managed control plane costs more per
month than the entire deployment does. The line in `CLAUDE.md` should be amended to describe
what is here — that is a human's call, not this directory's.

When that changes, it changes with an ADR. ADR-0012's "Revisit when" names the triggers: a
merchant asking about uptime in a contract, or roughly 200 custom domains.

## Where to start

- `edge/RUNBOOK.md` — standing the box up, deploying, rolling back, restoring, triage. Read it
  before running anything in this directory.
- `edge/.env.example` — every key the box needs. Committed with no secret values; the real file
  lives only on the VM.
- `edge/compose.prod.yml` — what runs.
- `edge/Caddyfile` — TLS, on-demand issuance and the one gate on it.

## The commands

All of them are `make` targets at the repository root, all require `HOST=` and `CONFIRM=`, and
all are listed in the root `README.md` and in the runbook.

| Script | Runs on | Called by |
|---|---|---|
| `edge/ssh-deploy.sh` | your laptop | `make deploy`, `make edge-validate`, `make edge-rollback` |
| `edge/remote.sh` | the VM | everything above, and by hand on the box |
| `edge/preflight.sh` | the VM | `remote.sh preflight`, and every deploy |
| `edge/publish-web.sh` | your laptop | `make edge-web`, `make edge-web-rollback`, `make edge-releases` |
| `edge/backup.sh` | the VM | cron, and `make edge-backup` |
| `edge/postgres-init/30-role-passwords.sh` | the VM | the Postgres entrypoint, on a fresh volume |

## Adding a file here

`make secrets` scans `git ls-files`, so **a new file is invisible to the secret scan until it
is tracked** — it passes on your laptop and fails on the first commit that adds it. Before
committing anything into this directory, scan the paths directly:

```bash
uv run detect-secrets-hook --baseline .secrets.baseline \
  --exclude-files 'frontend/pnpm-lock\.yaml$' \
  --exclude-lines 'dev-only-|ci-only-|opsgrids:opsgrids@|(POSTGRES|MINIO_ROOT)_PASSWORD: "?opsgrids|PGPASSWORD=opsgrids|driver://user:pass@' \
  infrastructure/edge/<the new files>
```

The rule this protects: nothing in here carries a value. Every secret is an `${ENV_VAR}`
resolved from the box's own `.env`, and a finding from this scan is fixed, never baselined.
