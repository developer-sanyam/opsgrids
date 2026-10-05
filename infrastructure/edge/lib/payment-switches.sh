#!/usr/bin/env bash
# Sourced by preflight.sh, and by tests/infra/test_payment_env_wiring.py directly — no side
# effects of its own (no `cd`, no `set -e`), the same shape as lib/secret-strength.sh.
#
# Mirrors, on purpose and exactly, the start-up validators of `services/payment/app/config.py`
# (ADR-0029), so a box that would crash-loop `payment` and `payment-worker` — `Settings` refuses to
# start, nothing serves, the stuck-charge sweep and the charge poller stop with it — fails
# *preflight* instead, before a build and before anything changes:
#
#   * `PAYMENT_PROVIDER` is razorpay or cashfree; `PAYMENT_WIRE` is stub or live; `CASHFREE_ENV` is
#     stub, sandbox or production. An unset or empty value is the safe default (stub).
#   * A half-set key pair (an id without its secret, or the reverse) is refused, whatever the wire.
#   * `PAYMENT_WIRE=live` needs a Razorpay key pair, and a Razorpay-shaped key id. Test keys
#     (`rzp_test_`) never reach a live wire on a production box — every compose service runs with
#     `ENVIRONMENT=production`, and a customer could pay a real invoice with a public test card.
#   * Live-shaped keys (`rzp_live_`, `cfsk_ma_prod_`) and `CASHFREE_ENV=production` need
#     `PAYMENT_ALLOW_LIVE_KEYS=true` — nothing moves real money until the owner says so.
#   * `CASHFREE_ENV=sandbox` never reaches a production box (sandbox accepts public test cards and
#     VPAs: real invoices payable for free); `production` refuses test-shaped keys;
#     `PAYMENT_PROVIDER=cashfree` on a production box needs `CASHFREE_ENV=production`.
#
# Only variable NAMES are printed — never a value, never part of one.

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

	case "$provider" in razorpay | cashfree) ;; *) printf 'PAYMENT_PROVIDER must be razorpay or cashfree\n' ;; esac
	case "$wire" in stub | live) ;; *) printf 'PAYMENT_WIRE must be stub or live\n' ;; esac
	case "$cf_env" in stub | sandbox | production) ;; *) printf 'CASHFREE_ENV must be stub, sandbox or production\n' ;; esac

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
		elif [ "$rzp_mode" = test ]; then
			printf 'PAYMENT_WIRE=live with test keys (rzp_test_...) is refused on a production box: real invoices would be payable with a test card\n'
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
		printf 'CASHFREE_ENV=sandbox is refused on a production box: sandbox accepts public test cards, so real invoices would be payable for free\n'
	fi
	if [ "$cf_env" = production ]; then
		[ "$allow" = true ] || printf 'CASHFREE_ENV=production needs PAYMENT_ALLOW_LIVE_KEYS=true\n'
		[ -z "$cf_test_shaped" ] || printf 'CASHFREE_ENV=production with test-shaped keys is refused\n'
	fi
	if [ "$provider" = cashfree ] && [ "$cf_env" != production ]; then
		printf 'PAYMENT_PROVIDER=cashfree on a production box needs CASHFREE_ENV=production (the stub and the sandbox cannot collect real money)\n'
	fi
}
