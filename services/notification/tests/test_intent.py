"""`app/intent.py` -- the policy, the response parser and the HTTP client. No database, no network:
the client is given an `httpx.MockTransport`."""

from __future__ import annotations

import json

import httpx
import pytest
from pydantic import SecretStr

from app import intent
from app.config import Settings


def _answer(label: str, p_opt_out: float, confidence: float = 0.99) -> dict:
    probabilities = dict.fromkeys(intent.LABELS, 0.0)
    probabilities[intent.OPT_OUT] = p_opt_out
    if label != intent.OPT_OUT:
        probabilities[label] = 1.0 - p_opt_out
    return {
        "answers": {
            "intent": {
                "type": "choice",
                "choice": label,
                "confidence": confidence,
                "probabilities": probabilities,
            }
        }
    }


# --------------------------------------------------------------------------- decide


@pytest.mark.parametrize(
    ("label", "p", "action"),
    [
        (intent.OPT_OUT, 1.0, intent.ACTION_SUPPRESS),
        (intent.OPT_OUT, 0.95, intent.ACTION_SUPPRESS),
        (intent.OPT_OUT, 0.94, intent.ACTION_REVIEW),
        (intent.OPT_OUT, 0.6, intent.ACTION_REVIEW),
        (intent.OPT_OUT, 0.59, intent.ACTION_NONE),
        # The label must agree: a fat opt-out tail under another winning label is flagged, never
        # acted on.
        (intent.QUESTION, 0.97, intent.ACTION_REVIEW),
        (intent.OTHER, 0.2, intent.ACTION_NONE),
        # Opting back in and cancelling an order are labels, not suppressions.
        (intent.OPT_IN, 0.0, intent.ACTION_NONE),
        (intent.ORDER_CANCEL, 0.01, intent.ACTION_NONE),
    ],
)
def test_decide(label: str, p: float, action: str) -> None:
    assert intent.decide(label, p) == action


# --------------------------------------------------------------------------- parse_response


def test_parse_response_reads_the_label_and_the_opt_out_probability() -> None:
    verdict = intent.parse_response(_answer(intent.OPT_OUT, 0.98, confidence=0.97))
    assert (verdict.label, verdict.p_opt_out, verdict.confidence) == (intent.OPT_OUT, 0.98, 0.97)
    assert verdict.action == intent.ACTION_SUPPRESS


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"answers": {}},
        {"answers": {"intent": {"choice": intent.OTHER}}},  # no probabilities
        # A label we never offered is a malformed answer, not a new category.
        _answer("delete_everything", 0.0),
        None,
        "nope",
    ],
)
def test_parse_response_refuses_anything_it_did_not_ask_for(body: object) -> None:
    with pytest.raises(intent.IntentUnavailable):
        intent.parse_response(body)


# --------------------------------------------------------------------------- the client


def _settings() -> Settings:
    return Settings(typesafe_api_key=SecretStr("k-test"))


def test_no_api_key_means_no_classifier() -> None:
    assert intent.make_classifier(Settings()) is None


def test_request_carries_the_message_and_nothing_identifying() -> None:
    body = intent.build_request("x" * 5000)
    assert set(body["state"]) == {"message"}
    assert len(body["state"]["message"]) == intent.MAX_TEXT_CHARS
    assert set(body["questions"]["intent"]["criteria"]) == set(intent.LABELS)


async def test_classifier_posts_the_key_and_the_message() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers["authorization"]
        seen["url"] = str(request.url)
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_answer(intent.OPT_OUT, 0.99))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    classify = intent.make_classifier(_settings(), client=client)
    assert classify is not None
    verdict = await classify("please stop texting me")

    assert seen["auth"] == "Bearer k-test"
    assert seen["url"] == "https://api.typesafe.ai/v1/systemone"
    assert seen["body"]["state"] == {"message": "please stop texting me"}
    assert verdict.action == intent.ACTION_SUPPRESS


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(401, json={"error": "bad key"}),
        httpx.Response(200, text="<html>"),
        httpx.Response(200, json={"answers": {}}),
    ],
)
async def test_every_non_answer_is_unavailable_never_a_guess(response: httpx.Response) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response))
    classify = intent.make_classifier(_settings(), client=client)
    assert classify is not None
    with pytest.raises(intent.IntentUnavailable):
        await classify("stop")


async def test_a_network_failure_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    classify = intent.make_classifier(_settings(), client=client)
    assert classify is not None
    with pytest.raises(intent.IntentUnavailable):
        await classify("stop")
