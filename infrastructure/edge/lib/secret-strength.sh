#!/usr/bin/env bash
# Sourced by preflight.sh, and by tests/infra/test_preflight_secrets.py directly — no side
# effects of its own (no `cd`, no `set -e`), the same shape as lib/memory-budget.sh.
#
# Mirrors, on purpose and exactly, the startup rules the services enforce on these secrets, so a
# box fails *preflight* — before a build, before a migration, before anything changes — rather than
# failing later as containers that will not start:
#
#   * `platform_core.config.ServiceSettings.model_post_init`: `internal_service_token`,
#     `pii_encryption_key` and (when set) `admin_gateway_hop_key` must each be at least 32
#     **bytes** (not characters — it is `len(value.encode())` there, so `wc -c` here).
#   * auth's own `Settings.model_post_init`: `admin_gateway_hop_key` must not equal
#     `internal_service_token`, and must be set at all in a real deployment (the "set" half is
#     preflight's generic "every key in .env.example is set" check, which this key is subject to).
#
# Placeholder prefixes (`dev-only-`, `ci-only-`) are preflight.sh's own loop's job.

# secret_strength_problems — reads the three variables from the environment and prints one line
# per problem; prints nothing when they are fine. An *empty* value is skipped: that is already
# reported as missing, and a second complaint about the same key is noise.
secret_strength_problems() {
	local key value bytes
	for key in INTERNAL_SERVICE_TOKEN PII_ENCRYPTION_KEY ADMIN_GATEWAY_HOP_KEY; do
		value="${!key-}"
		[ -n "$value" ] || continue
		bytes=$(printf %s "$value" | wc -c | tr -d ' ')
		if [ "$bytes" -lt 32 ]; then
			printf '%s is %s bytes: it must be at least 32\n' "$key" "$bytes"
		fi
	done
	if [ -n "${ADMIN_GATEWAY_HOP_KEY-}" ] && [ "${ADMIN_GATEWAY_HOP_KEY-}" = "${INTERNAL_SERVICE_TOKEN-}" ]; then
		printf 'ADMIN_GATEWAY_HOP_KEY must differ from INTERNAL_SERVICE_TOKEN\n'
	fi
}
