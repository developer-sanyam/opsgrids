#!/usr/bin/env bash
# Sourced by preflight.sh, and by tests/infra/test_payment_env_wiring.py directly — no side
# effects of its own (no `cd`, no `set -e`), the same shape as lib/secret-strength.sh.
#
# Mirrors, on purpose and exactly, the start-up validators of `services/payment/app/config.py`
# (ADR-0032), so a box that would crash-loop `payment` and `payment-worker` — `Settings` refuses to
# start, nothing serves, the stuck-charge sweep and the charge poller stop with it — fails
# *preflight* instead, before a build and before anything changes:
#
#   * `PAYMENT_PROVIDER` is razorpay or cashfree; `PAYMENT_WIRE` is stub or live; `CASHFREE_ENV` is
#     stub, sandbox or production; `PAYMENT_ALLOW_TEST_KEYS` is a boolean the way pydantic reads
#     one (true/false, 1/0, yes/no, on/off, y/n, t/f, any case). An unset or empty value is the
#     safe default (stub, false).
#   * A half-set key pair (an id without its secret, or the reverse) is refused, whatever the wire.
#   * `PAYMENT_WIRE=live` needs a Razorpay key pair, and a Razorpay-shaped key id. Test keys
#     (`rzp_test_`) never reach a live wire on a production box — every compose service runs with
#     `ENVIRONMENT=production`, and a customer could pay a real invoice with a public test card —
#     unless `PAYMENT_ALLOW_TEST_KEYS` says the box has no paying customers (a staging box).
#   * Live-shaped keys (`rzp_live_`, `cfsk_ma_prod_`) and `CASHFREE_ENV=production` need
#     `PAYMENT_ALLOW_LIVE_KEYS=true` — nothing moves real money until the owner says so.
#   * `CASHFREE_ENV=sandbox` never reaches a production box (sandbox accepts public test cards and
#     VPAs: real invoices payable for free) unless `PAYMENT_ALLOW_TEST_KEYS`; `production` refuses
#     test-shaped keys; `PAYMENT_PROVIDER=cashfree` on a production box needs
#     `CASHFREE_ENV=production` (or `sandbox` under `PAYMENT_ALLOW_TEST_KEYS`).
#   * `PAYMENT_ALLOW_TEST_KEYS` is refused beside anything that moves real money:
#     `PAYMENT_ALLOW_LIVE_KEYS`, a live-shaped key on either provider, `CASHFREE_ENV=production`.
#
# Only variable NAMES are printed — never a value, never part of one.

# _payment_flag NAME — prints `true`, `false` or `invalid` for the boolean variable NAME, read the
# way pydantic reads it (and `env_ignore_empty`: unset or empty is `false`).
_payment_flag() {
	local raw
	raw=$(printf '%s' "${!1:-}" | tr '[:upper:]' '[:lower:]')
	case "$raw" in
	"" | false | 0 | no | off | n | f) printf 'false' ;;
	true | 1 | yes | on | y | t) printf 'true' ;;
	*) printf 'invalid' ;;
	esac
}

# payment_test_keys_allowed — succeeds when PAYMENT_ALLOW_TEST_KEYS is on, as `payment` reads it.
# What preflight's loud warning keys off.
payment_test_keys_allowed() {
	[ "$(_payment_flag PAYMENT_ALLOW_TEST_KEYS)" = true ]
}

# payment_switch_problems — reads the variables from the environment and prints one line per
# problem; prints nothing when they are fine (an unconfigured box is fine: it runs on the stub).
payment_switch_problems() {
	local provider="${PAYMENT_PROVIDER:-razorpay}"
	local wire="${PAYMENT_WIRE:-stub}"
	local cf_env="${CASHFREE_ENV:-stub}"
	local allow="${PAYMENT_ALLOW_LIVE_KEYS:-false}"
	local rzp_id="${RAZORPAY_KEY_ID:-}"
	local rzp_secret="${RAZORPAY_KEY_SECRET:-}"
	local cf_app="${CASHFREE_APP_ID:-}"
	local cf_secret="${CASHFREE_SECRET_KEY:-}"
	local allow_test allow_live_read
	allow_test=$(_payment_flag PAYMENT_ALLOW_TEST_KEYS)
	# The live flag as `payment` reads it — for the "never both" rule only. The rules above it keep
	# their exact `true`: a `True` there is refused here and accepted by the service, a safe
	# false-refuse; the "never both" rule must not pass what the service would refuse.
	allow_live_read=$(_payment_flag PAYMENT_ALLOW_LIVE_KEYS)

	case "$provider" in razorpay | cashfree) ;; *) printf 'PAYMENT_PROVIDER must be razorpay or cashfree\n' ;; esac
	case "$wire" in stub | live) ;; *) printf 'PAYMENT_WIRE must be stub or live\n' ;; esac
	case "$cf_env" in stub | sandbox | production) ;; *) printf 'CASHFREE_ENV must be stub, sandbox or production\n' ;; esac
	if [ "$allow_test" = invalid ]; then
		printf 'PAYMENT_ALLOW_TEST_KEYS must be true or false\n'
		allow_test=false
	fi

	# Razorpay.
	if { [ -n "$rzp_id" ] && [ -z "$rzp_secret" ]; } || { [ -z "$rzp_id" ] && [ -n "$rzp_secret" ]; }; then
		printf 'RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set together\n'
	fi
	local rzp_mode=""
	case "$rzp_id" in
	rzp_test_*) rzp_mode="test" ;;
	rzp_live_*) rzp_mode="live" ;;
	esac
	if [ -n "$rzp_id" ] && ! printf '%s' "$rzp_id" | grep -Eq '^rzp_(test|live)_[A-Za-z0-9]{6,40}$'; then
		rzp_mode=""
	fi
	if [ "$rzp_mode" = live ] && [ "$allow" != true ]; then
		printf 'RAZORPAY_KEY_ID is a live key (rzp_live_...) and PAYMENT_ALLOW_LIVE_KEYS is not true\n'
	fi
	if [ "$wire" = live ]; then
		if [ -z "$rzp_id" ] || [ -z "$rzp_secret" ]; then
			printf 'PAYMENT_WIRE=live needs RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET\n'
		elif [ -z "$rzp_mode" ]; then
			printf 'RAZORPAY_KEY_ID must be a Razorpay key id (rzp_test_... or rzp_live_...)\n'
		elif [ "$rzp_mode" = test ] && [ "$allow_test" != true ]; then
			printf 'PAYMENT_WIRE=live with test keys (rzp_test_...) is refused on a production box: real invoices would be payable with a test card (a staging box with no paying customers sets PAYMENT_ALLOW_TEST_KEYS=true)\n'
		fi
	fi

	# Cashfree.
	if { [ -n "$cf_app" ] && [ -z "$cf_secret" ]; } || { [ -z "$cf_app" ] && [ -n "$cf_secret" ]; }; then
		printf 'CASHFREE_APP_ID and CASHFREE_SECRET_KEY must be set together\n'
	fi
	local cf_prod_shaped="" cf_test_shaped=""
	case "$cf_secret" in
	cfsk_ma_prod_*) cf_prod_shaped=1 ;;
	cfsk_ma_test_*) cf_test_shaped=1 ;;
	esac
	case "$cf_app" in TEST*) cf_test_shaped=1 ;; esac
	if [ -n "$cf_prod_shaped" ] && [ "$allow" != true ]; then
		printf 'CASHFREE_SECRET_KEY is a production key and PAYMENT_ALLOW_LIVE_KEYS is not true\n'
	fi
	case "$cf_env" in
	sandbox | production)
		if [ -z "$cf_app" ] || [ -z "$cf_secret" ]; then
			printf 'CASHFREE_ENV=%s needs CASHFREE_APP_ID and CASHFREE_SECRET_KEY\n' "$cf_env"
		fi
		;;
	esac
	if [ "$cf_env" = sandbox ]; then
		if [ "$allow_test" != true ]; then
			printf 'CASHFREE_ENV=sandbox is refused on a production box: sandbox accepts public test cards, so real invoices would be payable for free (a staging box with no paying customers sets PAYMENT_ALLOW_TEST_KEYS=true)\n'
		fi
		[ -z "$cf_prod_shaped" ] || printf 'CASHFREE_ENV=sandbox with production-shaped keys is refused\n'
	fi
	if [ "$cf_env" = production ]; then
		[ "$allow" = true ] || printf 'CASHFREE_ENV=production needs PAYMENT_ALLOW_LIVE_KEYS=true\n'
		[ -z "$cf_test_shaped" ] || printf 'CASHFREE_ENV=production with test-shaped keys is refused\n'
	fi
	if [ "$provider" = cashfree ] && [ "$cf_env" != production ] && ! { [ "$cf_env" = sandbox ] && [ "$allow_test" = true ]; }; then
		printf 'PAYMENT_PROVIDER=cashfree on a production box needs CASHFREE_ENV=production (the stub and the sandbox cannot collect real money; a staging box with no paying customers may use CASHFREE_ENV=sandbox with PAYMENT_ALLOW_TEST_KEYS=true)\n'
	fi

	# Test mode never runs beside live money.
	if [ "$allow_test" = true ]; then
		if [ "$allow_live_read" = true ]; then
			printf 'PAYMENT_ALLOW_TEST_KEYS and PAYMENT_ALLOW_LIVE_KEYS cannot both be true: a box runs on test keys or on live money, never both\n'
		fi
		if [ "$rzp_mode" = live ]; then
			printf 'PAYMENT_ALLOW_TEST_KEYS=true with a live RAZORPAY_KEY_ID (rzp_live_...) is refused: test mode never runs beside a key that moves real money\n'
		fi
		if [ -n "$cf_prod_shaped" ] || [ "$cf_env" = production ]; then
			printf 'PAYMENT_ALLOW_TEST_KEYS=true with a production CASHFREE_SECRET_KEY or CASHFREE_ENV=production is refused: test mode never runs beside live money\n'
		fi
	fi
}
