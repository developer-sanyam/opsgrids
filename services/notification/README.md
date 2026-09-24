# Notification service

Part of the OpsGrids platform. Owns the `notification` schema.

```bash
make run SERVICE=notification PORT=8110    # API
make worker SERVICE=notification           # event consumer
make relay SERVICE=notification            # outbox relay
```

## Owns

- **Transactional plumbing** (`notification` product, gated on the `notification` entitlement):
  `templates`, `rules`, `messages`, the append-only `deliveries` ledger, `preferences`, and the
  WhatsApp inbox (`conversations`, `inbound_messages`).
- **Engage** (`engage` product, gated separately at `/api/v1/engage`): an organisation's own
  sending identity (`email_connections`, `email_domains`, `senders`), its audience (`contacts`,
  `contact_lists`, `suppressions`), and its `campaigns`. Two products, one process, two
  permission catalogues (`app/catalogue.py`) — see the module docstring on `app/api/engage.py`.

## Engage sending — the decision that shapes this module

**Customers send through their own email provider — Google Workspace, Microsoft 365, or their
own SMTP relay. OpsGrids publishes no mail infrastructure of its own.** There is no platform MTA,
no bounce envelope, and no link-rewriting proxy anywhere in the send path. **Bounce processing
and open/click tracking are not built.** Stating this plainly here because a prior version of
this module generated DNS instructions that pointed at OpsGrids-hosted records that never
existed — see `docs/superpowers/specs/2026-09-21-engage-email-platform-design.md` §7 for the
incident and the fix.

What that means concretely:

- `app/domain.py`'s `expected_records` derives the DNS a domain must publish from the
  organisation's *current* email connection (`google` | `microsoft` | `smtp`), computed fresh at
  every read and every `verify` — never frozen at the moment the domain was added. A domain with
  no connection yet has nothing to publish, and the setup screen says so.
- The record set is SPF and DKIM (DMARC recommended, never required, never blocking). No
  return-path record, no tracking record, no DKIM key this platform mints — each provider proves
  DKIM its own way, and this service checks for that provider's shape rather than a value only it
  could have generated.
- SPF is verified properly: one apex TXT record, containing the provider's include as a whole
  mechanism token. Zero records is `missing`; more than one is `multiple` (a domain may publish
  only one, and the service suggests the merge); one that lacks the include is `mismatch` (edit
  it, don't add a second record).
- `app/dns_check.py` is the one place this service resolves DNS — both the domain-verification
  reads (`check_domain_records`) and the outbound dial guard (`assert_dialable`) that keeps a
  customer-named SMTP host from pointing at this service's own compose network.
- The campaign send gate (`services.request_send`) still requires a verified domain, a tested
  sender and a connected provider before it stages a fan-out — it is checking against rules a
  customer can actually satisfy now.
- `POST /engage/campaigns/{id}/test` sends a real, rendered-exactly-as-a-recipient-would-see-it
  copy without requiring domain verification: a test never reaches a real recipient, so it proves
  the content and the connection, not the DNS.
- `GET /engage/setup` folds connection, domain, sender, contacts and campaign into one
  get-started checklist for a fresh organisation's first screen.

## Publishes

Transactional: `notification.delivery.message.{queued,sent,failed,delivered,read}`,
`notification.whatsapp.inbound_received`.

Engage sending identity: `engage.setup.connection.{created,replaced}`,
`engage.setup.whatsapp.{created,replaced}`, `engage.setup.domain.{created,verified}`,
`engage.setup.sender.{created,verified}`. Engage campaigns:
`engage.campaign.send_requested`, `engage.campaign.cancelled`.

## Consumes

Every routing key named in `app/domain.py`'s `SUBSCRIBABLE_EVENTS` — the platform-wide list of
events a `Rule` may bind a notification to. Adding a producer means adding its key there first;
an unbound event type is refused at rule-authoring time rather than silently never firing.
