"""The pure half of WhatsApp template management: Meta's shapes, validation, preview, and the
Graph helper against a mock transport. No database."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app import domain, w2_domain, w2_graph
from app.config import Settings


def _settings() -> Settings:
    return Settings()


# --------------------------------------------------------------------- vocabulary


def test_meta_vocabulary_maps_into_ours() -> None:
    assert w2_domain.status_from_meta("APPROVED") == "approved"
    assert w2_domain.status_from_meta("in_appeal") == "pending"
    assert w2_domain.status_from_meta("PENDING_DELETION") == "disabled"
    # A warning, not a state; and one we have never heard of: neither may invent a status.
    assert w2_domain.status_from_meta("FLAGGED") is None
    assert w2_domain.status_from_meta("SOMETHING_NEW") is None
    assert w2_domain.quality_from_meta("GREEN") == "high"
    assert w2_domain.quality_from_meta({"score": "RED", "date": 1}) == "low"
    assert w2_domain.quality_from_meta(None) == "unknown"
    assert w2_domain.category_from_meta("MARKETING") == "marketing"


def test_history_is_capped_and_never_mutates_its_input() -> None:
    history: list[dict[str, Any]] = []
    for _ in range(w2_domain.HISTORY_LIMIT + 5):
        history = w2_domain.appended_history(history, "pending", "sync")
        assert len(history) <= w2_domain.HISTORY_LIMIT
    before = list(history)
    w2_domain.appended_history(history, "approved", "sync")
    assert history == before


def test_a_second_language_gets_a_distinct_code_within_the_length_limit() -> None:
    long_name = "n" * 80
    assert w2_domain.unique_code("order", "en", set()) == "order"
    assert w2_domain.unique_code("order", "hi", {"order"}) == "order_hi"
    assert w2_domain.unique_code("order", "pt_BR", {"order"}) == "order_pt_br"
    assert w2_domain.unique_code("order", "hi", {"order", "order_hi"}) == "order_hi_2"
    assert len(w2_domain.unique_code(long_name, "en_US", {long_name})) <= 80


# --------------------------------------------------------------------- reading components


COMPONENTS: list[dict[str, Any]] = [
    {
        "type": "HEADER",
        "format": "TEXT",
        "text": "Order {{1}}",
        "example": {"header_text": ["A-1"]},
    },
    {
        "type": "BODY",
        "text": "Hi {{2}}, {{1}} is ready.",
        "example": {"body_text": [["Rahul", "Order"]]},
    },
    {"type": "FOOTER", "text": "Bye"},
    {
        "type": "BUTTONS",
        "buttons": [
            {"type": "QUICK_REPLY", "text": "Ok"},
            {
                "type": "URL",
                "text": "Go",
                "url": "https://x.test/{{1}}",
                "example": ["https://x.test/a"],
            },
        ],
    },
]


def test_extract_numbers_body_first_then_header_then_buttons() -> None:
    extracted = w2_domain.extract(COMPONENTS)
    assert extracted.body_preview == "Hi {{2}}, {{1}} is ready."
    assert [(s["index"], s["name"], s["kind"]) for s in extracted.specs] == [
        (1, "1", "text"),
        (2, "2", "text"),
        (3, "header", "text"),
        (4, "button_1", "url"),
    ]
    mapping = extracted.mapping
    # Body positions are contiguous from zero, so the existing send path accepts them.
    domain.validate_template_variables([domain.TemplateVariable(**m) for m in mapping])
    assert [(m["component"], m["button_index"], m["position"]) for m in mapping] == [
        ("body", 0, 0),
        ("body", 0, 1),
        ("header", 0, 0),
        ("button", 1, 0),
    ]


def test_a_synced_template_is_sendable_through_the_existing_payload_builder() -> None:
    mapping = w2_domain.extract(COMPONENTS).mapping
    payload = domain.build_template_payload(
        template_name="order_ready",
        language="en_US",
        variables=[domain.TemplateVariable(**m) for m in mapping],
        context={"1": "Order", "2": "Rahul", "header": "A-1", "button_1": "abc"},
    )
    types = [(c["type"], [p.get("text") for p in c["parameters"]]) for c in payload["components"]]
    # Meta fills {{1}} from the first parameter and {{2}} from the second, whatever order they
    # appear in the text.
    assert ("body", ["Order", "Rahul"]) in types
    assert ("header", ["A-1"]) in types


def test_extract_reads_named_parameters_and_survives_odd_shapes() -> None:
    named = w2_domain.extract(
        [
            {
                "type": "BODY",
                "text": "Hi {{customer_name}}",
                "example": {
                    "body_text_named_params": [{"param_name": "customer_name", "example": "Rahul"}]
                },
            }
        ]
    )
    assert named.specs == [
        {"index": 1, "name": "customer_name", "kind": "text", "example": "Rahul"}
    ]
    assert named.mapping[0]["parameter_name"] == "customer_name"
    assert w2_domain.extract([]).specs == []
    assert w2_domain.extract([{"type": "BODY"}, "junk", {}]).body_preview == ""  # type: ignore[list-item]


# --------------------------------------------------------------------- preview


def test_preview_fills_every_component_and_is_strict() -> None:
    out = w2_domain.render_preview(COMPONENTS, {"1": "Order", "2": "Rahul", "3": "A-9", "4": "z"})
    assert out["header"] == "Order A-9"
    assert out["body"] == "Hi Rahul, Order is ready."
    assert out["text"] == "Order A-9\n\nHi Rahul, Order is ready.\n\nBye"
    assert out["buttons"] == [
        {"type": "quick_reply", "text": "Ok"},
        {"type": "url", "text": "Go", "url": "https://x.test/z"},
    ]
    with pytest.raises(w2_domain.PreviewError) as caught:
        w2_domain.render_preview(COMPONENTS, {"1": "x"})
    assert caught.value.missing == ["2", "3", "4"]


# --------------------------------------------------------------------- validation


def _valid(**over: Any) -> dict[str, Any]:
    args: dict[str, Any] = {
        "name": "order_update",
        "category": "utility",
        "language": "en_US",
        "components": {"body": {"text": "Hi {{1}}, all good today.", "examples": ["Rahul"]}},
    }
    args.update(over)
    return args


def test_a_well_formed_template_has_no_problems() -> None:
    assert w2_domain.validate_create(**_valid()) == []


@pytest.mark.parametrize("name", ["Upper", "has space", "dash-name", "", "n" * 81])
def test_bad_names_are_refused(name: str) -> None:
    fields = [p["field"] for p in w2_domain.validate_create(**_valid(name=name))]
    assert fields == ["name"]


@pytest.mark.parametrize(
    "text",
    [
        "Hi {{1}} {{3}}",
        "Hi {{2}} only",
        "Hi {{a}}",
        "Hi {{1} broken",
        "Hi {{ }}",
        "{{1}} x",
        "x {{1}}",
    ],
)
def test_bad_variable_numbering_is_refused(text: str) -> None:
    problems = w2_domain.validate_create(
        **_valid(components={"body": {"text": text, "examples": ["a", "b"]}})
    )
    assert any(p["field"] == "components.body.text" for p in problems), text


def test_examples_must_match_the_variable_count_and_not_be_blank() -> None:
    too_few = w2_domain.validate_create(
        **_valid(components={"body": {"text": "Hi {{1}} and {{2}} ok", "examples": ["a"]}})
    )
    blank = w2_domain.validate_create(
        **_valid(components={"body": {"text": "Hi {{1}} ok", "examples": [" "]}})
    )
    stray = w2_domain.validate_create(
        **_valid(components={"body": {"text": "No variables here", "examples": ["a"]}})
    )
    assert [p["field"] for p in too_few] == ["components.body.examples"]
    assert [p["field"] for p in blank] == ["components.body.examples.0"]
    assert [p["field"] for p in stray] == ["components.body.examples"]


def test_button_limits() -> None:
    buttons = [{"type": "url", "text": "u", "url": f"https://x.test/{i}"} for i in range(3)]
    buttons += [{"type": "phone", "text": "p", "phone": "+919876543210"}] * 2
    problems = w2_domain.validate_create(
        **_valid(components={"body": {"text": "ok"}, "buttons": buttons})
    )
    messages = [p["message"] for p in problems if p["field"] == "components.buttons"]
    assert any("URL" in m for m in messages) and any("phone" in m for m in messages)

    dynamic = w2_domain.validate_create(
        **_valid(
            components={
                "body": {"text": "ok"},
                "buttons": [{"type": "url", "text": "u", "url": "https://x.test/{{1}}/more"}],
            }
        )
    )
    assert [p["field"] for p in dynamic] == ["components.buttons.0.url"]


def test_to_meta_components_round_trips_through_extract() -> None:
    ours = {
        "header": {"type": "text", "text": "Order {{1}}", "example": "A-1"},
        "body": {"text": "Hi {{1}}, ready today.", "examples": ["Rahul"]},
        "footer": {"text": "Bye"},
        "buttons": [
            {
                "type": "url",
                "text": "Go",
                "url": "https://x.test/{{1}}",
                "example": "https://x.test/a",
            },
            {"type": "phone", "text": "Call", "phone": "+919876543210"},
        ],
    }
    meta = w2_domain.to_meta_components(ours)
    assert [c["type"] for c in meta] == ["HEADER", "BODY", "FOOTER", "BUTTONS"]
    specs = w2_domain.extract(meta).specs
    assert [(s["name"], s["example"]) for s in specs] == [
        ("1", "Rahul"),
        ("header", "A-1"),
        ("button_0", "https://x.test/a"),
    ]


# --------------------------------------------------------------------- the Graph helper


def _graph(handler: Any) -> w2_graph.TemplateGraph:
    return w2_graph.TemplateGraph(
        _settings(),
        waba_id="waba-1",
        access_token="ci-only-token",
        transport=httpx.MockTransport(handler),
    )


async def test_list_follows_meta_cursors_and_never_the_next_url() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("after") == "c1":
            return httpx.Response(200, json={"data": [{"id": "2"}]})
        return httpx.Response(
            200,
            json={
                "data": [{"id": "1"}],
                "paging": {"cursors": {"after": "c1"}, "next": "https://evil.example/steal"},
            },
        )

    items = await _graph(handler).list_templates()
    assert [i["id"] for i in items] == ["1", "2"]
    assert {r.url.host for r in seen} == {"graph.facebook.com"}
    assert all(r.headers["authorization"] == "Bearer ci-only-token" for r in seen)
    assert all("ci-only-token" not in str(r.url) for r in seen)


async def test_an_error_page_raises_rather_than_returning_a_partial_list() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("after"):
            return httpx.Response(500, json={"error": {"message": "boom ci-only-token"}})
        return httpx.Response(
            200, json={"data": [{"id": "1"}], "paging": {"cursors": {"after": "c1"}, "next": "x"}}
        )

    with pytest.raises(w2_graph.GraphError) as caught:
        await _graph(handler).list_templates()
    assert caught.value.retryable is True
    assert "ci-only-token" not in caught.value.public_message


async def test_meta_user_message_is_surfaced_and_a_bad_token_is_not_retryable() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={"error": {"code": 100, "message": "internal", "error_user_msg": "Name taken."}},
        )

    with pytest.raises(w2_graph.GraphError) as caught:
        await _graph(refuse).create_template(
            name="a", language="en", category="utility", components=[]
        )
    assert caught.value.public_message == "Name taken." and caught.value.retryable is False

    def unauthorised(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"code": 190, "message": "expired"}})

    with pytest.raises(w2_graph.GraphError) as bad_token:
        await _graph(unauthorised).list_templates()
    assert bad_token.value.retryable is False and "expired" not in bad_token.value.public_message


async def test_a_network_failure_is_retryable_and_names_no_host() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("could not connect to graph.facebook.com")

    with pytest.raises(w2_graph.GraphError) as caught:
        await _graph(down).list_templates()
    assert (
        caught.value.retryable is True and "graph.facebook.com" not in caught.value.public_message
    )


async def test_create_sends_category_upper_cased_and_requires_an_id_back() -> None:
    bodies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"status": "PENDING"})

    with pytest.raises(w2_graph.GraphError):
        await _graph(handler).create_template(
            name="a", language="en", category="marketing", components=[]
        )
    assert bodies[0]["category"] == "MARKETING"
