# API reference

Every route that exists today, extracted from the source rather than written from memory.

Two surfaces, and they are not interchangeable:

- **`app-gateway`** (`:8100`) — product UIs and mobile. Tokens must carry audience `app`.
- **`admin-gateway`** (`:8101`) — staff and internal. Audience `operator`. A product token is
  **refused** here even though the network path exists.

Public routes are under `/api/v1`. The generated OpenAPI document for a service is at
`/docs` on that service.

---

## Conventions

**Authentication.** `Authorization: Bearer <access token>`. Tokens are EdDSA, verified through
JWKS at `/.well-known/jwks.json` on auth-service. Only auth-service can mint.

**Tenancy.** The token carries `act_org`. There is no default organization and no way to name
one in a request — a token without `act_org` is refused.

**Permissions are not in the token.** The gateway resolves them per request and forwards them
as `x-perms`. Do not add a permission list to a token; five products of forty permissions is
5–6 KB of header, past common proxy limits, and it fails as a silent 500.

**Errors** are RFC 7807 problem documents, `application/problem+json`:

```json
{ "type": "https://opsgrids.tech/problems/product-not-entitled",
  "title": "Payment required", "status": 402,
  "detail": "crm is not active for this organization" }
```

Status codes that carry specific meaning here:

| Code | Means |
|---|---|
| `401` + type `token-stale` | Grants changed since the token was minted. **Refresh, do not re-login** |
| `402` | Product not activated. Permissions are fine, the subscription is not |
| `403` | Authenticated, not permitted |
| `404` on a staff route | May mean "not staff" — deliberately indistinguishable from "no such route" |
| `429` | Rate limited; `Retry-After` is set |
| `503` | An upstream is unreachable, or permissions could not be resolved. **Never a fail-open** |

**Pagination.** List endpoints return `{ items, total, page, size }`.

---

## auth-service — `:8110`

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/auth/login` | Public. Returns either tokens or an org choice |
| `POST` | `/api/v1/auth/select-org` | Finish signing in when the user has several organizations |
| `POST` | `/api/v1/auth/switch-org` | New token, same session |
| `POST` | `/api/v1/auth/refresh` | Rotates. Reusing a rotated token kills the whole family |
| `POST` | `/api/v1/auth/logout` | |
| `GET` | `/api/v1/auth/me` | Who the caller is |
| `POST` | `/api/v1/auth/mfa/verify` | Present a second factor |
| `POST` | `/api/v1/auth/mfa/enrol` | |
| `POST` | `/api/v1/auth/mfa/enrol/confirm` | |
| `POST` | `/api/v1/auth/password/change` | |
| `POST` | `/api/v1/auth/password/forgot` | Public |
| `POST` | `/api/v1/auth/password/reset` | Public |
| `GET` | `/api/v1/auth/info` | |
| `GET` | `/.well-known/jwks.json` | **Root, not `/api/v1`.** Public and unauthenticated |

**Sign-in is a two-step flow when a person belongs to more than one organization.** `login`
returns a pre-auth token and the list of memberships; `select-org` exchanges it for a real
access token. Do not assume one organization — that assumption is how a user in two companies
ends up in the wrong one.

## account-service — `:8111`

| Method | Path |
|---|---|
| `GET` `PATCH` | `/api/v1/organization` |
| `GET` `POST` | `/api/v1/org-units` |
| `POST` | `/api/v1/org-units/{unit_id}/move` |
| `GET` `POST` | `/api/v1/locations` |
| `GET` | `/api/v1/members` |
| `GET` `PATCH` `DELETE` | `/api/v1/members/{membership_id}` |
| `PUT` | `/api/v1/members/{membership_id}/roles` |
| `GET` `POST` | `/api/v1/roles` |
| `PUT` | `/api/v1/roles/{role_id}` |
| `GET` | `/api/v1/permissions` |
| `GET` `POST` | `/api/v1/invitations` |
| `DELETE` | `/api/v1/invitations/{invitation_id}` |
| `POST` | `/api/v1/invitations/accept` | **Public** — the token is the credential |
| `GET` | `/api/v1/account/info` |

**Four of these change what somebody may do** and bump the organization's permission version,
invalidating every token issued before them: setting member roles, revoking a member, replacing
a role's grants, and moving somebody between locations, departments or reporting lines. Clients
should expect a `token-stale` 401 shortly after any of them and refresh rather than re-login.

## Internal API — service-to-service only

**Not reachable through either gateway's public surface, excluded from the OpenAPI document,
and guarded by a shared internal token.** Mounted at the root of account-service.

| Method | Path | Called by |
|---|---|---|
| `GET` | `/internal/v1/users/by-email` | auth-service, at sign-in |
| `GET` | `/internal/v1/users/{user_id}` | auth-service |
| `GET` | `/internal/v1/memberships/{id}/grants` | **Both gateways, every authenticated request** |

These are the only endpoints that deliberately read across tenants. If this router is
unavailable, nobody can sign in and nothing is authorized — see
[production-readiness.md](production-readiness.md).

## Not yet implemented

No product API exists. HRMS, CRM, PulseBoard and the rest have route prefixes reserved in the
gateway route tables and **no service behind them** — a request to one resolves and then fails
to connect. See [backlog.md](backlog.md).
