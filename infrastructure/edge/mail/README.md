# The platform's mail server — `mail.opsgrids.com`

docker-mailserver, behind the `mail` compose profile (see `compose.prod.yml`). One container:
Postfix (SMTP in on 25, submission on 587), Dovecot (IMAP on 993), Rspamd (spam filtering and
DKIM signing). Mailboxes and settings live in `config/` on the box, which is **never committed**
— it holds password hashes and the DKIM private key.

## Why it is shaped this way

- **4 GB box.** Antivirus and fail2ban are off; the container is capped at 1 GB. Mailcow wants
  6 GB and was ruled out for that reason alone.
- **Outbound port 25 is blocked by the cloud provider** on this subscription (verified: Gmail
  and Hostinger MX both time out). Mail to any other domain therefore leaves through
  `RELAY_*` — a relay service that accepts on 587 — and until one is configured such mail is
  deferred in the queue, not lost. Mail between our own mailboxes never leaves the box.
- **TLS is Caddy's.** `mail.opsgrids.com` is in `TLS_PLATFORM_HOSTS`, so Caddy obtains and
  renews the certificate; the mail server reads it from Caddy's data volume and reloads when
  it changes. One ACME account, one renewal loop, for every hostname on the box.

## DNS (Hostinger)

| Name | Type | Value |
|---|---|---|
| `mail` | A | the box's IP |
| `@` | MX 10 | `mail.opsgrids.com` |
| `@` | TXT | `v=spf1 include:<relay's spf> ~all` (plus `ip4:<box ip>` only if port 25 is ever opened) |
| `mail._domainkey` | TXT | the DKIM record printed by `setup config dkim` (below) |
| `_dmarc` | TXT | `v=DMARC1; p=none; rua=mailto:<an inbox a human reads>` |

## Everything you type (on the box, from `infrastructure/edge/`)

```bash
C="docker compose -f compose.prod.yml --env-file .env"

# First start only: generate the DKIM key, then add its TXT record to DNS.
$C exec -T mailserver setup config dkim keysize 2048 domain opsgrids.com
$C exec -T mailserver cat /tmp/docker-mailserver/rspamd/dkim/rsa-2048-mail-opsgrids.com.public.txt

# Mailboxes. Passwords are generated here and handed over out of band; never put one in chat.
$C exec -T mailserver setup email add support@opsgrids.com "$(openssl rand -base64 18)"
$C exec -T mailserver setup email list
$C exec -T mailserver setup email update support@opsgrids.com "<new password>"
$C exec -T mailserver setup email del support@opsgrids.com
$C exec -T mailserver setup quota set support@opsgrids.com 2G
$C exec -T mailserver setup alias add info@opsgrids.com support@opsgrids.com

# What is in the queue, and why (a deferred message names the relay error).
$C exec -T mailserver postqueue -p
$C logs --tail 200 mailserver | grep -iE "warning|error|deferred"
```

## The platform sends through it

In the box's `.env`: `EMAIL_TRANSPORT=smtp`, `SMTP_HOST=mailserver`, `SMTP_PORT=587`,
`SMTP_USE_TLS=true`, `SMTP_USERNAME=no-reply@opsgrids.com`, `SMTP_PASSWORD=<that mailbox's
password>`, `EMAIL_FROM=no-reply@opsgrids.com`; then recreate `notification`,
`notification-worker` and `notification-relay`. `SPOOF_PROTECTION=1` means a login may only send
as itself, so `EMAIL_FROM` must be the mailbox the platform logs in as.

## Clients (Gmail app, Outlook, Apple Mail, Thunderbird)

IMAP `mail.opsgrids.com` port 993 (SSL/TLS); SMTP `mail.opsgrids.com` port 587 (STARTTLS); the
full address as the username. Webmail is not deployed — a Roundcube container is the obvious
addition when somebody needs it, and it is another ~150 MB this box does not have today.

## Managing mailboxes from OpsGrids

Not built yet. The shape when it is: an internal endpoint in notification-service that runs the
`setup email …` commands above through a small host-side agent (the container has no Docker
socket and must not get one), audited like every other write. Until then, the commands are
the admin panel.
