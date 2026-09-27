"""Configuration for the auth service."""

from __future__ import annotations

from pydantic import Field

from platform_core.config import ServiceSettings, settings_for


class Settings(ServiceSettings):
    service_name: str = "auth"
    db_schema: str = "auth"
    db_app_role: str | None = "opsgrids_auth"
    db_admin_role: str | None = "opsgrids_admin"
    db_outbox_role: str | None = "opsgrids_outbox"

    #: Where account-service lives. This service holds credentials; the directory — who
    #: exists and which organizations they belong to — is asked for over the internal API
    #: rather than read across a schema boundary.
    account_service_url: str = "http://localhost:8111"
    #: Where product-service lives. Its answer becomes the `ent` claim in every token, and
    #: one hop later the difference between a 200 and a 402 at the gateway.
    product_service_url: str = "http://localhost:8112"

    # --- lockout ------------------------------------------------------------------
    #: Enough attempts that a mistyped password is not punished, few enough that online
    #: guessing is hopeless. The delay is the defence; the count is just the trigger.
    max_failed_attempts: int = 5
    lockout_minutes: int = 15
    password_min_length: int = 10

    # --- resets -------------------------------------------------------------------
    password_reset_ttl_minutes: int = 30

    # --- single sign-on -----------------------------------------------------------
    #: Where the identity provider sends the browser back. **Configuration, never a
    #: parameter.** Taking a redirect target from the request — even one that "looks like
    #: ours" — is how an authorization code ends up posted to somebody else's host, and it
    #: is also the value the token exchange must repeat verbatim, so there is exactly one
    #: place it may come from.
    sso_redirect_uri: str = "http://localhost:8100/api/v1/auth/sso/callback"
    #: How long an authorization request stays open. Long enough for a person to type a
    #: password and answer an MFA prompt at the provider, short enough that a state value
    #: captured from a browser's history is already dead. The state is single-use as well —
    #: this is the ceiling, not the defence.
    sso_state_ttl_seconds: int = 600
    #: Tolerance for clock skew when validating an ID token's `exp`/`iat`. Small: the
    #: provider and this service are both NTP-disciplined, and a generous window is a
    #: replay window.
    sso_clock_skew_seconds: int = 60

    # --- sessions -----------------------------------------------------------------
    #: The absolute lifetime of a session (`Session.expires_at`, `start_session`) — not how
    #: long a *refresh token* lives (`refresh_token_ttl_seconds`, a sliding window that renews
    #: on every rotation), but the hard ceiling on the session behind it that no amount of
    #: refreshing may cross (owner decision, 2026-09-24: "signed in for 7 days, then a new
    #: sign-in", in every app). `issue_for`/`_issue_operator` clamp every refresh token they
    #: mint to whatever is left of this window, so a refresh late in the 7 days gets a
    #: correspondingly short-lived successor rather than another full `refresh_token_ttl_
    #: seconds` — the mechanism that actually makes this a hard cap and not just a longer
    #: sliding window. Shared by the operator/staff path too (`start_session` is the one
    #: function every session — tenant, operator, federated — is created through), so this
    #: change caps staff sessions at 7 days as well, not only tenant ones.
    session_ttl_days: int = 7
    #: Carries the refresh token: httpOnly, path-scoped to the auth routes, and Secure
    #: outside development. Never readable by JavaScript, which is the whole point.
    refresh_cookie_name: str = "opsgrids_refresh"
    refresh_cookie_domain: str | None = None
    #: SSO topology addendum (ADR-0003): the refresh cookie is shared by every tab and every
    #: product SPA open in the browser, but each tab refreshes on its own timer, and two of
    #: them racing (a proactive refresh in one tab landing mid-flight of a 401 retry in
    #: another) present the *same* refresh token to the server within milliseconds of each
    #: other. `rotate_refresh`'s theft response — revoke the whole family — is correct
    #: against a genuinely stolen token and wrong against this race: it would sign out a
    #: person for opening the app in two tabs. A short grace window lets a token presented
    #: again this soon after its own rotation mint a fresh access token instead of being
    #: treated as reuse (M1 of the 2026-09-24 SSO review: marker-only, access-only — see
    #: `services.py`); anything older than this still triggers
    #: the family-wide revocation. 30s comfortably covers a race between two requests in
    #: flight at once and is far too short to help an attacker who captured the token later.
    refresh_reuse_grace_seconds: int = 30

    # --- MFA ------------------------------------------------------------------------
    #: M4 of the third-round security review: MAJOR 3's tolerant legacy-plaintext read
    #: (`_open_factor_secret`) is opt-in. A raw base32 value sitting in `secret_enc` is only a
    #: legitimate legacy row on a box that predates PII sealing; anywhere else it is a
    #: tampered or hand-inserted row, and silently accepting it would let whoever can write
    #: that column choose a factor's secret. `False` by default everywhere; set `True` on a
    #: box only until `bin/reseal-mfa-factors` reports `remaining=0`, then remove it (RUNBOOK,
    #: "Operator MFA"). Tests and `local`/`test`/`ci` set it freely to exercise the legacy path.
    mfa_accept_legacy_plaintext: bool = False

    # --- member step-up (ADR-0023) --------------------------------------------------
    #: How long the access token `POST /auth/mfa/step-up` mints is good for. That token carries
    #: `amr=["otp"]` and is the only thing that opens the "reveal" routes for a member; it is
    #: neither refreshable nor revocable inside this window, so the window *is* the exposure of a
    #: stolen one. Long enough to reveal a handful of records in a row, short enough that walking
    #: away from the screen ends the privilege. Bounded on both sides at load: below 30 s a person
    #: cannot finish a task before it lapses (and the UI would re-prompt in a loop), above 600 s it
    #: is a second, unrevocable session in all but name. A value outside the range fails startup
    #: rather than being silently clamped, because the alternative is a security setting that does
    #: not do what its value says.
    step_up_token_ttl_seconds: int = Field(default=180, ge=30, le=600)

    def model_post_init(self, _: object) -> None:
        """M1(a) of the third-round security review: the admin-gateway hop signature
        (`require_admin_hop`) is a no-op when `admin_gateway_hop_key` is unset — deliberately,
        so a dev box that has never provisioned one still signs in. That same silence is fatal
        in a real deployment: an operator with the fix-round-2 code but no key configured gets
        no defence in depth at all, and nothing about that failure is loud. Refused here,
        the same way the base class refuses a placeholder secret.

        A hop key equal to `internal_service_token` is refused everywhere, not only in
        production — the whole point of a distinct secret is that a leak of one does not
        also open the other, and a developer who copy-pasted the same value into both should
        find out immediately rather than the day one of them leaks.
        """
        super().model_post_init(_)
        if self.admin_gateway_hop_key is not None and (
            self.admin_gateway_hop_key.get_secret_value()
            == self.internal_service_token.get_secret_value()
        ):
            raise ValueError(
                "admin_gateway_hop_key must not equal internal_service_token: a leak of one "
                "must not also hand over the other."
            )
        real_deployment = self.environment in ("production", "staging", "uat")
        if real_deployment and self.admin_gateway_hop_key is None:
            raise ValueError(
                "admin_gateway_hop_key must be set in production, staging and uat: auth's "
                "operator routes require a valid admin-gateway hop signature "
                "(require_admin_hop), and an unset key silently turns that defence off."
            )


def settings() -> Settings:
    return settings_for(Settings)  # type: ignore[return-value]
