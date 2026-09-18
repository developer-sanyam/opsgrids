"""Provider-template payload construction, tested without a database or a network.

Every field name asserted here was read off Meta's published example rather than recalled. The
tests are written so that if Meta's shape changes, they fail on the shape rather than on a
behaviour, which is the failure a reader can act on.
"""

from __future__ import annotations

import pytest

from app import domain
from app.domain import COMPONENT_BODY, COMPONENT_BUTTON, COMPONENT_HEADER, TemplateVariable


def _build(**over: object) -> dict:
    kwargs: dict = {
        "template_name": "invoice_due_reminder",
        "language": "en_US",
        "variables": [
            TemplateVariable(key="customer_name", position=0),
            TemplateVariable(key="invoice_number", position=1),
        ],
        "context": {"customer_name": "Priya", "invoice_number": "INV-9"},
    }
    kwargs.update(over)
    return domain.build_template_payload(**kwargs)  # type: ignore[arg-type]


# --------------------------------------------------------------------- the verified shape


def test_payload_matches_metas_published_shape() -> None:
    payload = _build()
    assert payload == {
        "name": "invoice_due_reminder",
        "language": {"code": "en_US"},
        "components": [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": "Priya"},
                    {"type": "text", "text": "INV-9"},
                ],
            }
        ],
    }


def test_body_parameters_are_emitted_in_position_order() -> None:
    """Declaration order must not matter; position is the contract."""
    payload = _build(
        variables=[
            TemplateVariable(key="invoice_number", position=1),
            TemplateVariable(key="customer_name", position=0),
        ]
    )
    assert [p["text"] for p in payload["components"][0]["parameters"]] == ["Priya", "INV-9"]


def test_header_component_precedes_body() -> None:
    payload = _build(
        variables=[
            TemplateVariable(key="customer_name", component=COMPONENT_HEADER, position=0),
            TemplateVariable(key="invoice_number", component=COMPONENT_BODY, position=0),
        ]
    )
    assert [c["type"] for c in payload["components"]] == ["header", "body"]


def test_button_index_is_a_string() -> None:
    """Meta's own example emits `"index": "0"` — a string, not the integer it looks like."""
    payload = _build(
        variables=[TemplateVariable(key="pay_link", component=COMPONENT_BUTTON, button_index=0)],
        context={"pay_link": "https://pay.example/9"},
    )
    button = next(c for c in payload["components"] if c["type"] == "button")
    assert button["index"] == "0"
    assert isinstance(button["index"], str)


def test_button_sub_type_follows_the_parameter_kind() -> None:
    url_button = _build(
        variables=[TemplateVariable(key="pay_link", component=COMPONENT_BUTTON, kind="text")],
        context={"pay_link": "https://pay.example/9"},
    )
    assert next(c for c in url_button["components"] if c["type"] == "button")["sub_type"] == "url"

    payload_button = _build(
        variables=[TemplateVariable(key="pay_link", component=COMPONENT_BUTTON, kind="payload")],
        context={"pay_link": "https://pay.example/9"},
    )
    button = next(c for c in payload_button["components"] if c["type"] == "button")
    assert button["sub_type"] == "payload"
    assert button["parameters"] == [{"type": "payload", "payload": "https://pay.example/9"}]


def test_each_button_gets_its_own_component() -> None:
    payload = _build(
        variables=[
            TemplateVariable(key="customer_name", component=COMPONENT_BUTTON, button_index=0),
            TemplateVariable(key="invoice_number", component=COMPONENT_BUTTON, button_index=1),
        ]
    )
    buttons = [c for c in payload["components"] if c["type"] == "button"]
    assert [b["index"] for b in buttons] == ["0", "1"]


# --------------------------------------------------------------------- strictness


def test_a_missing_value_refuses_the_whole_payload() -> None:
    """A template reaching Meta with a blank parameter is a message read with a hole in it."""
    with pytest.raises(domain.TemplateError, match="No value for"):
        _build(context={"customer_name": "Priya"})


def test_a_null_value_counts_as_missing() -> None:
    with pytest.raises(domain.TemplateError, match="No value for"):
        _build(context={"customer_name": "Priya", "invoice_number": None})


def test_nested_context_paths_resolve() -> None:
    payload = _build(
        variables=[TemplateVariable(key="invoice.number", position=0)],
        context={"invoice": {"number": "INV-42"}},
    )
    assert payload["components"][0]["parameters"] == [{"type": "text", "text": "INV-42"}]


def test_non_strings_are_stringified() -> None:
    payload = _build(
        variables=[TemplateVariable(key="amount", position=0)], context={"amount": 12000}
    )
    assert payload["components"][0]["parameters"] == [{"type": "text", "text": "12000"}]


# --------------------------------------------------------------------- variable validation


def test_a_position_gap_is_refused() -> None:
    """The important one. Meta fills parameters in order, so a gap does not leave a hole — it
    shifts every later value into the wrong placeholder and sends real data to the wrong slot."""
    with pytest.raises(domain.TemplateError, match="wrong placeholder"):
        domain.validate_template_variables(
            [
                TemplateVariable(key="a", position=0),
                TemplateVariable(key="b", position=2),
            ]
        )


def test_two_variables_cannot_claim_one_slot() -> None:
    with pytest.raises(domain.TemplateError, match="both claim"):
        domain.validate_template_variables(
            [
                TemplateVariable(key="a", position=0),
                TemplateVariable(key="b", position=0),
            ]
        )


def test_the_same_position_on_different_buttons_is_fine() -> None:
    domain.validate_template_variables(
        [
            TemplateVariable(key="a", component=COMPONENT_BUTTON, button_index=0, position=0),
            TemplateVariable(key="b", component=COMPONENT_BUTTON, button_index=1, position=0),
        ]
    )


def test_an_unknown_component_is_refused() -> None:
    with pytest.raises(domain.TemplateError, match="not a template component"):
        domain.validate_template_variables([TemplateVariable(key="a", component="footer")])


def test_a_negative_position_is_refused() -> None:
    with pytest.raises(domain.TemplateError, match="negative position"):
        domain.validate_template_variables([TemplateVariable(key="a", position=-1)])


# --------------------------------------------------------------------- formats and status


def test_named_format_refuses_rather_than_guessing() -> None:
    """§43 of the brief, enforced in code: the send-side shape of a named parameter is not
    published on any page consulted, so we refuse with an instruction instead of inventing it."""
    with pytest.raises(domain.TemplateError, match="not supported yet"):
        _build(parameter_format=domain.FORMAT_NAMED)


def test_an_unknown_format_is_refused() -> None:
    with pytest.raises(domain.TemplateError, match="not a template parameter format"):
        _build(parameter_format="freeform")


def test_only_an_approved_template_is_sendable() -> None:
    assert domain.template_is_sendable("approved")
    for status in ("draft", "pending", "rejected", "paused", "disabled"):
        assert not domain.template_is_sendable(status)


def test_every_declared_status_is_covered_by_the_sendable_check() -> None:
    """So a status added later cannot quietly become sendable by default."""
    sendable = [s for s in domain.TEMPLATE_STATUSES if domain.template_is_sendable(s)]
    assert sendable == [domain.STATUS_APPROVED]


def test_categories_match_metas_three() -> None:
    assert set(domain.CATEGORIES) == {"utility", "marketing", "authentication"}
