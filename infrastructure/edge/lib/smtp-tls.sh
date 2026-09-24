#!/usr/bin/env bash
# Sourced by preflight.sh, and by tests/infra/test_preflight_smtp_tls.py directly — no side
# effects of its own (no `cd`, no `set -e`), the same shape as lib/secret-strength.sh.
#
# Mirrors, on purpose and exactly, two refusals `notification-service`'s own `Settings.
# model_post_init` (services/notification/app/config.py) and `SmtpTransport.__init__`
# (services/notification/app/transports.py) enforce at process start, so a box that would refuse
# to boot the notification worker fails *preflight* — before a build, before a migration, before
# anything changes — instead of failing later as a container that will not start:
#
#   * `SMTP_USE_TLS` and `SMTP_USE_SSL` both true. STARTTLS upgrades a plain connection; SSL/SMTPS
#     is encrypted from the first byte; a connection cannot be both. Before NOTIF-A this box could
#     set both without either service caring — SMTP_USE_SSL did not exist for notification at all
#     — and now the notification worker refuses to start with it.
#   * `EMAIL_TRANSPORT=smtp` with a credential (`SMTP_USERNAME`/`SMTP_PASSWORD`) set and *neither*
#     flag true. `SmtpTransport` refuses to be built at all in that shape: it would mean sending
#     the platform's SMTP password over an unencrypted connection, and the worker never starts.
#
# Booleans read the way pydantic reads them (`true/1/yes/on/y/t`, any case; anything else is
# false) — the same convention preflight.sh's own `RUN_SWEEPS_IN_API` check already uses, and the
# one the services are actually built on. Defaults mirror `compose.prod.yml`'s own
# `${SMTP_USE_TLS:-true}` / `${SMTP_USE_SSL:-false}` — an unset key on the box behaves exactly as
# it will once compose fills in that default, not as bash's empty string would on its own.

# is_true VALUE DEFAULT — VALUE if non-empty, DEFAULT otherwise, read as a pydantic-style bool.
is_true() {
	local value="${1:-$2}"
	case "$(printf '%s' "$value" | tr '[:upper:]' '[:lower:]')" in
	true | 1 | yes | on | y | t) return 0 ;;
	*) return 1 ;;
	esac
}

# smtp_tls_problems — reads EMAIL_TRANSPORT, SMTP_USE_TLS, SMTP_USE_SSL, SMTP_USERNAME and
# SMTP_PASSWORD from the environment and prints one line per problem; prints nothing when the
# combination is one notification-service will actually start with.
smtp_tls_problems() {
	local use_tls use_ssl
	if is_true "${SMTP_USE_TLS-}" true; then use_tls=1; else use_tls=0; fi
	if is_true "${SMTP_USE_SSL-}" false; then use_ssl=1; else use_ssl=0; fi

	if [ "$use_tls" = 1 ] && [ "$use_ssl" = 1 ]; then
		printf 'SMTP_USE_TLS and SMTP_USE_SSL are both true: STARTTLS upgrades a plain connection, SSL/SMTPS is encrypted from the first byte, and a connection cannot be both — notification-service and ops-monitor refuse to start\n'
	fi

	if [ "${EMAIL_TRANSPORT-log}" = "smtp" ] \
		&& { [ -n "${SMTP_USERNAME-}" ] || [ -n "${SMTP_PASSWORD-}" ]; } \
		&& [ "$use_tls" = 0 ] && [ "$use_ssl" = 0 ]; then
		printf 'EMAIL_TRANSPORT=smtp with SMTP_USERNAME/SMTP_PASSWORD set but neither SMTP_USE_TLS nor SMTP_USE_SSL is true: notification-service refuses to send that credential over an unencrypted connection and will not start\n'
	fi
}
