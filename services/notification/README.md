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

## Whose connection a message goes out on

The process-wide transports (`transports.build`: one SMTP relay, one WhatsApp number, from the
environment) belong to the **platform**. They carry **platform-owned** messages only — the
set-password and reset email auth publishes under the platform tenant (the nil UUID, ADR-0013
§3) — plus the `in_app` feed, which has no provider.

Everything else is **tenant-owned**, and `app/dispatcher.py` sends it through that organization's
own connection, resolved per send by `app/tenant_transport.py`:

- `whatsapp` — the org's `provider_accounts` row: its `phone_number_id` and its sealed token,
  opened for the send, on `WhatsAppCloudTransport`.
- `email` — the org's `email_connections` row, through the same builder campaigns and test sends
  use, so a tenant-named SMTP host passes the same egress guard (ADR-0016) on every send. It is
  sent as the connection's login when that is an address (Google Workspace, Microsoft 365),
  otherwise as the org's oldest verified sender.

**The marker is `messages.organization_id`.** It is fixed when the message is staged and bound by
row-level security, so a customer cannot mint a platform message and nothing about a rule, template
or category can promote one. The default is fail-closed: only the nil tenant reaches the shared
transport; a new customer-facing flow is safe without a line changed here.

**No connection, no send — and no fallback.** A tenant-owned message whose org has no usable
connection is failed at once (`state = failed`, never retried) with the reason in
`messages.last_error`, the failed `deliveries` row and the `message.failed` event, and one
structured log line (`dispatch.channel_unavailable`, with the organization id and reason, never a
credential):

| `last_error` | Meaning |
|---|---|
| `channel_not_connected` | No connected account for that org and channel (missing, disconnected, no token, or a `platform`-kind email connection). |
| `channel_credentials_unreadable` | The stored token or password cannot be opened with this environment's `PII_ENCRYPTION_KEY`. Reconnect the channel. |
| `smtp_host_not_allowed` | The org's SMTP host failed the egress guard on this send. |
| `sender_not_configured` | An SMTP relay whose login is not an address, and the org has no verified sender to send as. |

`EMAIL_TRANSPORT=log` is still honoured for tenant email, as it is for campaigns (the connection
is required either way). `WHATSAPP_TRANSPORT` selects only the platform's own sender: a connected
WhatsApp account is what enables that org's sending.

A transport is built per send and closed with it; nothing is cached, so a disconnected or replaced
credential stops being used on the next message. `email_connections.daily_cap` is enforced for
campaigns only (`claim_campaign_batch`, which already counts rule-driven mail against it); a
rule-driven send is not refused at the cap.

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
