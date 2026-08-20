# admin-gateway — the operator surface

Internal, staff and partner APIs. Same machinery as `app-gateway` (both are
`platform_core.gateway` plus a route file), with a different audience and a much narrower
route table.

**Two gateways rather than one** because the alternative is a surface that grants
cross-customer access sitting one routing mistake away from the public internet. It also
lets consumer-facing latency work and operator-facing breadth be scaled and rate-limited
without trading off against each other.

```bash
make run SERVICE=admin-gateway PORT=8101
```

Owns no tables; no worker, no relay. See the `app-gateway` README for the check order,
header handling and permission resolution, which are identical.

## What differs

**A different audience.** Tokens must name `operator`. A token minted for a customer's own
application is refused here even though the network path between the two exists — which is
the entire point of the split.

**`staff_only` routes answer 404, not 403.** A 403 would confirm that
`/api/v1/admin/organizations` is a real endpoint. Somebody holding a valid operator-audience
token but no staff claim learns nothing: same status, same problem type, same title as a
path that does not exist.

The flag is **per route**, not per gateway. This gateway carries internal
service-to-service routes alongside staff routes, and service calls hold no staff claim, so
a gateway-wide flag would either break those calls or leave the staff routes unguarded.

**No product entitlement gate.** Staff act on organizations that have bought nothing at
all; gating this surface on entitlements would make an unsubscribed org unmanageable at
exactly the moment support most needs to reach it.

**A tighter rate limit** — 120/min against the product gateway's 300. A spike on this
surface is a signal, not traffic.

**A deliberately tiny public surface.** Only the sign-in flow and the health probes. Staff
authenticate through the same auth-service as everybody else; nothing else here is
reachable without a token, and a test asserts that no non-auth path ever becomes public.
