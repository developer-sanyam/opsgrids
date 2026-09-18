"""The rules that matter, tested without a database.

Everything in `domain` is a function of its arguments, so these run anywhere — which is the
point: the strict-rendering rule and the suppression rule are the two that protect a customer
from receiving something wrong, and they should not need Postgres to be verified.
"""

from __future__ import annotations

import pytest

from app import domain

# --------------------------------------------------------------------- path resolution


def test_resolve_path_walks_nested_payloads() -> None:
    payload = {"customer": {"contact": {"email": "a@b.com"}}}
    assert domain.resolve_path(payload, "customer.contact.email") == "a@b.com"


def test_resolve_path_returns_none_for_a_missing_segment() -> None:
    """Missing is None, not an exception: a rule asking for a field this particular event does
    not carry is ordinary, and the caller decides whether that means skip."""
    assert domain.resolve_path({"customer": {}}, "customer.contact.email") is None
    assert domain.resolve_path({}, "anything") is None


def test_resolve_path_does_not_walk_into_a_non_dict() -> None:
    assert domain.resolve_path({"customer": "acme"}, "customer.name") is None


# --------------------------------------------------------------------- rendering


def test_render_substitutes_every_placeholder() -> None:
    out = domain.render(
        "Hi {{name}}, ticket {{ticket.number}} is open",
        {
            "name": "Priya",
            "ticket": {"number": "TK-1"},
        },
    )
    assert out == "Hi Priya, ticket TK-1 is open"


def test_render_tolerates_whitespace_in_the_braces() -> None:
    assert domain.render("Hi {{ name }}", {"name": "Priya"}) == "Hi Priya"


def test_render_refuses_an_unresolved_placeholder() -> None:
    """The rule the whole service leans on. A customer reading "Dear {{name}}" is worse than a
    message that never left, because the undelivered one is visible in a queue."""
    with pytest.raises(domain.RenderError) as exc:
        domain.render("Dear {{name}}", {})
    assert "name" in str(exc.value)


def test_render_treats_an_explicit_null_as_unresolved() -> None:
    """A key that exists with a null value is exactly as unsendable as a key that does not."""
    with pytest.raises(domain.RenderError):
        domain.render("Dear {{name}}", {"name": None})


def test_render_names_every_missing_placeholder_at_once() -> None:
    """One authoring pass should fix the whole template, not one placeholder per attempt."""
    with pytest.raises(domain.RenderError) as exc:
        domain.render("{{a}} {{b}} {{a}}", {})
    message = str(exc.value)
    assert "a" in message and "b" in message


def test_render_stringifies_non_strings() -> None:
    assert domain.render("{{n}} items", {"n": 3}) == "3 items"


def test_placeholders_lists_what_a_template_needs() -> None:
    assert domain.placeholders("{{a}} and {{b.c}}") == {"a", "b.c"}


# --------------------------------------------------------------------- recipients


def test_email_is_lowercased_and_trimmed() -> None:
    assert domain.normalize_recipient("email", "  Priya@Example.COM ") == "priya@example.com"


@pytest.mark.parametrize("bad", ["not-an-email", "a@b", "@example.com", "a b@example.com", ""])
def test_bad_email_is_refused(bad: str) -> None:
    with pytest.raises(domain.RuleError):
        domain.normalize_recipient("email", bad)


def test_whatsapp_strips_human_separators() -> None:
    assert domain.normalize_recipient("whatsapp", "+91 79766-03446") == "+917976603446"
    assert domain.normalize_recipient("whatsapp", "+1 (555) 010-1234") == "+15550101234"


def test_whatsapp_refuses_a_number_without_a_country_code() -> None:
    """Guessing a missing country code sends a stranger somebody else's invoice."""
    with pytest.raises(domain.RuleError) as exc:
        domain.normalize_recipient("whatsapp", "7976603446")
    assert "E.164" in str(exc.value)


@pytest.mark.parametrize("bad", ["+0123456789", "+1234", "+" + "9" * 20])
def test_malformed_e164_is_refused(bad: str) -> None:
    with pytest.raises(domain.RuleError):
        domain.normalize_recipient("whatsapp", bad)


def test_an_unreachable_channel_is_refused() -> None:
    with pytest.raises(domain.RuleError):
        domain.normalize_recipient("carrier_pigeon", "anything")


# --------------------------------------------------------------------- rule coherence


def _valid(**over: object) -> dict[str, object]:
    base: dict[str, object] = {
        "channel": "email",
        "event_type": "support.desk.ticket.created",
        "recipient_path": "requester_email",
        "recipient_literal": None,
    }
    base.update(over)
    return base


def test_a_coherent_rule_validates() -> None:
    domain.validate_rule(**_valid())  # type: ignore[arg-type]


def test_a_rule_with_no_recipient_is_refused() -> None:
    with pytest.raises(domain.RuleError):
        domain.validate_rule(**_valid(recipient_path=None))  # type: ignore[arg-type]


def test_a_rule_with_two_recipients_is_refused() -> None:
    with pytest.raises(domain.RuleError):
        domain.validate_rule(  # type: ignore[arg-type]
            **_valid(recipient_literal="ops@example.com")
        )


def test_a_rule_on_an_unpublished_event_is_refused() -> None:
    """An unbound event type saves cleanly, looks like a working automation in the operator's
    list, and never fires. The failure is an absence, so it is caught at authoring time."""
    with pytest.raises(domain.RuleError) as exc:
        domain.validate_rule(**_valid(event_type="support.desk.ticket.exploded"))  # type: ignore[arg-type]
    assert "subscribe" in str(exc.value)


def test_a_literal_recipient_is_validated_at_authoring_time() -> None:
    with pytest.raises(domain.RuleError):
        domain.validate_rule(  # type: ignore[arg-type]
            **_valid(recipient_path=None, recipient_literal="not-an-email")
        )


# --------------------------------------------------------------------- the event catalogue


def test_the_catalogue_covers_every_product_line() -> None:
    products = {key.split(".", 1)[0] for key in domain.SUBSCRIBABLE_EVENTS}
    assert {"hrms", "crm", "support", "finance", "operations", "itsm"} <= products


def test_the_catalogue_never_includes_this_service_s_own_events() -> None:
    """Binding our own events would let a rule stage a message about staging a message."""
    assert not [k for k in domain.SUBSCRIBABLE_EVENTS if k.startswith("notification.")]


def test_the_catalogue_holds_no_fragments() -> None:
    """Several services build a routing key by concatenation. A bare suffix is not a key."""
    for key in domain.SUBSCRIBABLE_EVENTS:
        assert "." in key and not key.endswith(".")


# --------------------------------------------------------------------- suppression


def test_absence_of_a_preference_means_send() -> None:
    assert not domain.is_suppressed([], channel="email", category="billing")


def test_a_category_refusal_suppresses_only_that_category() -> None:
    prefs = [("email", "marketing")]
    assert domain.is_suppressed(prefs, channel="email", category="marketing")
    assert not domain.is_suppressed(prefs, channel="email", category="billing")


def test_a_wildcard_refusal_suppresses_the_whole_channel() -> None:
    prefs = [("whatsapp", domain.CATEGORY_ALL)]
    assert domain.is_suppressed(prefs, channel="whatsapp", category="anything")


def test_a_refusal_on_one_channel_does_not_suppress_another() -> None:
    prefs = [("whatsapp", domain.CATEGORY_ALL)]
    assert not domain.is_suppressed(prefs, channel="email", category="anything")


# --------------------------------------------------------------------- de-duplication


def test_dedupe_key_falls_back_to_the_event_id() -> None:
    key = domain.dedupe_key(rule_code="ack", event_id="e-1", payload={}, dedupe_key_path=None)
    assert key == "ack:e-1"


def test_dedupe_key_prefers_the_business_value() -> None:
    """So a producer that legitimately republishes an event about one invoice still sends once."""
    key = domain.dedupe_key(
        rule_code="ack",
        event_id="e-2",
        payload={"invoice": {"number": "INV-9"}},
        dedupe_key_path="invoice.number",
    )
    assert key == "ack:INV-9"


def test_dedupe_key_falls_back_when_the_path_is_absent() -> None:
    key = domain.dedupe_key(
        rule_code="ack", event_id="e-3", payload={}, dedupe_key_path="invoice.number"
    )
    assert key == "ack:e-3"


def test_two_rules_on_one_event_produce_two_keys() -> None:
    a = domain.dedupe_key(rule_code="ack", event_id="e", payload={}, dedupe_key_path=None)
    b = domain.dedupe_key(rule_code="notify", event_id="e", payload={}, dedupe_key_path=None)
    assert a != b


# --------------------------------------------------------------------- retry


def test_retry_is_bounded() -> None:
    assert domain.should_retry(0, max_attempts=3)
    assert domain.should_retry(2, max_attempts=3)
    assert not domain.should_retry(3, max_attempts=3)


def test_attempts_are_numbered_from_one() -> None:
    assert domain.next_attempt_no(0) == 1
