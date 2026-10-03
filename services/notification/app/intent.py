"""What an inbound WhatsApp message wants, judged by TypeSafe -- a recall booster, not a gate.

`webhooks.STOP_KEYWORDS` is an exact match on purpose: a customer asking "how do I stop my
subscription" must not be unsubscribed for using the word in a sentence. The cost is recall:
"please stop texting me", "remove me from your list" and every non-English opt-out match nothing,
and that customer keeps receiving marketing they refused. This module reads the *meaning* of the
message instead and returns one typed judgment, which `decide` turns into an action.

**The keyword match stays authoritative.** This runs only on text the keyword check did not
already handle (`consumers.classify_inbound_intent`), after the message is committed, in the
event consumer -- never inside the webhook transaction, because a network call there would turn a
slow third party into a database problem.

**Only one thing ever acts: a high-confidence marketing opt-out.** Everything else is a label
for a person or a router to read. Specifically it never *restores* consent (`opt_in` is recorded
and nothing more -- consent restored from an inference is consent nobody gave), and "Cancel" with
no other words is deliberately left to the keyword rules, which read it as the order.

Measured on 38 hand-labelled messages (English, Hindi, Hinglish, French, plus hard negatives like
"STOP calling me about the payment, I already paid"): 34/38 correct against 18/38 for the keyword
list, zero false opt-outs. Thresholds are a starting point to re-check on real traffic.

What leaves the process: the message text, and nothing else -- no number, no name, no org id.
That is a deliberate data-sharing decision, so it is **off unless `TYPESAFE_API_KEY` is set**.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app.config import Settings
from platform_core.logging import get_logger

log = get_logger("notification.intent")

OPT_OUT: Final = "opt_out_marketing"
OPT_IN: Final = "opt_in"
ORDER_CANCEL: Final = "order_cancel"
COMPLAINT: Final = "complaint"
QUESTION: Final = "question"
OTHER: Final = "other"
LABELS: Final[tuple[str, ...]] = (OPT_OUT, OPT_IN, ORDER_CANCEL, COMPLAINT, QUESTION, OTHER)

#: What `decide` returns. Stored on the inbound row.
ACTION_SUPPRESS: Final = "suppress"
ACTION_REVIEW: Final = "review"
ACTION_NONE: Final = "none"

#: At or above this probability of a marketing opt-out the number is suppressed for marketing.
#: High on purpose: wrongly suppressing silently costs a customer's offers; wrongly not
#: suppressing costs a complaint, which `ACTION_REVIEW` below exists to catch.
SUPPRESS_THRESHOLD: Final = 0.95
#: From here up to `SUPPRESS_THRESHOLD`, flag for a person rather than act.
REVIEW_THRESHOLD: Final = 0.6

#: Messages are cut here before they leave the process. An opt-out is a short message; a long
#: one is not worth the tokens or the data.
MAX_TEXT_CHARS: Final = 1000

_QUESTION: Final[dict[str, Any]] = {
    "type": "choice",
    "instructions": (
        "What does this inbound WhatsApp reply from a customer to a business want? Opt-out means "
        "the customer wants to stop receiving the business's promotional or marketing messages "
        "in general. A request about one specific order, delivery, subscription billing or call "
        "is NOT an opt-out."
    ),
    "criteria": {
        OPT_OUT: (
            "Customer wants the business to stop sending promotional/marketing messages to them "
            "(any language, any phrasing)"
        ),
        OPT_IN: "Customer asks to start or resume receiving messages",
        ORDER_CANCEL: "Customer wants to cancel a specific order, booking or appointment",
        COMPLAINT: "Customer is complaining or unhappy about a product, service or delivery",
        QUESTION: "Customer is asking the business a question or for information or help",
        OTHER: (
            "Anything else: thanks, acknowledgements, requests about a specific transaction, "
            "delivery or subscription billing"
        ),
    },
}


@dataclass(frozen=True, slots=True)
class Verdict:
    """One judgment about one message."""

    label: str
    #: P(the customer wants to stop marketing), whatever label won.
    p_opt_out: float
    #: The model's own confidence in `label` (its distribution's concentration).
    confidence: float

    @property
    def action(self) -> str:
        return decide(self.label, self.p_opt_out)


def decide(label: str, p_opt_out: float) -> str:
    """The only policy in this module. Pure, so the thresholds are tested without a network.

    Suppress needs the winning label *and* a high probability -- two independent signals -- so a
    message the model reads as mostly something else with a fat opt-out tail is flagged, not acted
    on."""
    if label == OPT_OUT and p_opt_out >= SUPPRESS_THRESHOLD:
        return ACTION_SUPPRESS
    if p_opt_out >= REVIEW_THRESHOLD:
        return ACTION_REVIEW
    return ACTION_NONE


class IntentUnavailable(RuntimeError):
    """TypeSafe could not give a usable answer -- a timeout, a non-200, a malformed body. Never
    raised for "the answer is `other`"; that is a definite answer."""


#: `text -> Verdict`. A callable so tests substitute one without a network, the shape
#: `contacts.ContactResolver` uses.
IntentClassifier = Callable[[str], Awaitable[Verdict]]


def build_request(text: str) -> dict[str, Any]:
    """The request body. `state` carries the message and nothing identifying."""
    return {
        "state": {"message": text[:MAX_TEXT_CHARS]},
        "model": "jev-latest",
        "questions": {"intent": _QUESTION},
    }


def parse_response(body: Any) -> Verdict:
    """A `Verdict` from TypeSafe's response, or `IntentUnavailable` if the shape is not the one
    asked for. Strict: a label we did not offer is a malformed answer, not a new category."""
    try:
        answer = body["answers"]["intent"]
        label = answer["choice"]
        probabilities = answer["probabilities"]
        confidence = float(answer["confidence"])
        p_opt_out = float(probabilities.get(OPT_OUT, 0.0))
    except (KeyError, TypeError, ValueError, AttributeError) as exc:
        raise IntentUnavailable("unexpected response shape") from exc
    if label not in LABELS or not (0.0 <= p_opt_out <= 1.0):
        raise IntentUnavailable("answer outside the offered options")
    return Verdict(label=label, p_opt_out=p_opt_out, confidence=confidence)


def make_classifier(
    cfg: Settings, *, client: httpx.AsyncClient | None = None
) -> IntentClassifier | None:
    """The classifier for this process, or `None` when no API key is configured -- the feature is
    simply off, and the keyword rules are exactly what they were."""
    if cfg.typesafe_api_key is None:
        return None
    api_key = cfg.typesafe_api_key.get_secret_value()
    url = f"{cfg.typesafe_base_url.rstrip('/')}/v1/systemone"

    async def classify(text: str) -> Verdict:
        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=cfg.typesafe_timeout_seconds)
        try:
            response = await http.post(
                url, json=build_request(text), headers={"Authorization": f"Bearer {api_key}"}
            )
        except httpx.HTTPError as exc:
            raise IntentUnavailable(f"request failed: {type(exc).__name__}") from exc
        finally:
            if owns_client:
                await http.aclose()
        if response.status_code != 200:
            # The status only -- never the body, which can echo the request.
            raise IntentUnavailable(f"status {response.status_code}")
        try:
            body = response.json()
        except ValueError as exc:
            raise IntentUnavailable("response was not JSON") from exc
        return parse_response(body)

    return classify
