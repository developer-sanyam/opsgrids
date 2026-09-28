"""Meta's `signed_request` scheme -- the trust boundary for the deauthorize and data-deletion
callbacks. No I/O: everything here is a function of its arguments, the same discipline
`app/webhooks.py` holds itself to and for the same reason (this is the other surface on the
platform that trusts a body with no token behind it).

Distinct from `X-Hub-Signature-256` (`app/webhooks.py`'s own scheme, which signs the WhatsApp
Business Platform's message/status webhook). These two callbacks are Facebook Login's own
mechanism -- the one Meta's Embedded Signup popup also goes through -- and Meta form-encodes one
field, `signed_request=<base64url-HMAC-SHA256>.<base64url-JSON>`, POSTed to whichever URL the App
Dashboard has on file. Decoding it:

    payload  = base64url_decode(<base64url-JSON half>)
    expected = HMAC-SHA256(App Secret, <base64url-JSON half, still encoded>)
    trust iff constant-time-equal(expected, base64url_decode(<sig half>))

and the JSON payload itself (only readable *after* that check passes) is::

    {"algorithm": "HMAC-SHA256", "expires": 1291840400, "issued_at": 1291836800,
     "user_id": "218471"}

Read off Meta's current documentation, not recalled -- see the module-level "Verified against"
note in `docs/whatsapp/webhooks.md`.

Five rules, mirroring `webhooks.verify_signature`'s own:

1. **Size, before anything else.** A signed_request is a couple hundred bytes; refusing anything
   past `MAX_SIGNED_REQUEST_BYTES` before a byte of it is base64-decoded or parsed as JSON is the
   same instinct as `webhooks.MAX_WEBHOOK_BODY_BYTES`, sized down because this body is form-encoded
   and tiny by construction -- Meta publishes no ceiling of its own for this field, so this one is
   ours, chosen generously and still three orders of magnitude under the WhatsApp webhook's.
2. **Shape.** Exactly one `.`, both halves present and valid base64url (with the padding Meta's
   encoder omits restored, decoded with `validate=True` so a non-alphabet character is refused
   rather than silently dropped) before either is trusted as anything.
3. **Not the SDK's own login-response shape.** Meta signs `FB.login`'s own `authResponse.
   signedRequest` (and the `fbsr_<appid>` cookie) with the *same* app secret, the same
   `algorithm`/`issued_at`/`user_id` fields, and one more: `code`, the one-time authorization code
   -- or, on an older SDK, `oauth_token`. Neither ever appears in Meta's server-to-server
   deauthorize or data-deletion callback. A payload carrying either is refused here, before a
   single other field is read as trustworthy: anyone who can get a fresh signed login response for
   a Facebook id (a departed employee, a consultant, anyone who was ever shown Embedded Signup's
   popup -- no OpsGrids account required) can otherwise mint an indistinguishable, validly-signed
   "callback" on demand, repeatedly, forever. See the module-level "Verified against" note in
   `docs/whatsapp/webhooks.md`.
4. **Algorithm.** The decoded payload's own `algorithm` field must read `HMAC-SHA256`, compared as
   exact ASCII bytes after an ASCII-only uppercase (`bytes.upper()`, not `str.upper()`, which is
   Unicode-aware and would treat, say, a Kelvin sign or a Turkish dotless i as equal to a plain
   ASCII letter it is not byte-identical to) -- case-insensitively, because Meta's own example
   capitalises it exactly this way but nothing about the contract says a comparison must not
   tolerate case, and refusing a technically-conforming payload over letter case would be a
   self-inflicted outage. Meta has never documented a second value; a payload naming anything else,
   or anything that is not plain ASCII at all, is refused rather than trusted to mean "verify some
   other way".
5. **Signature**, over the *raw, still base64url-encoded* payload segment -- exactly what Meta
   signed, the same "do not re-serialise before checking" rule `webhooks.verify_signature`'s own
   docstring explains -- compared with `hmac.compare_digest`, never `==`.

Malformed input, the SDK's own shape, the wrong algorithm, and a signature mismatch all raise the
same `SignedRequestError` with no detail about which check failed: the caller answers the same way
(refused) regardless, and a message that said *how* a forged request was wrong would help the next
attempt.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import parse_qsl

#: A signed_request is a couple hundred bytes of base64'd JSON. Meta documents no ceiling of its
#: own for this field (unlike the 3 MB it states for the WhatsApp webhook body), so this is our
#: own, chosen generously and refused before a single byte is decoded or parsed -- the same
#: "an unauthenticated caller must not be able to make this process buffer an unbounded body just
#: to be told no" reasoning `webhooks.MAX_WEBHOOK_BODY_BYTES` documents, sized for a field that is
#: form-encoded and tiny by construction rather than a batch of Meta's own events.
MAX_SIGNED_REQUEST_BYTES: Final = 4096

_EXPECTED_ALGORITHM: Final = "HMAC-SHA256"
_EXPECTED_ALGORITHM_ASCII: Final = _EXPECTED_ALGORITHM.encode("ascii")

#: Fields that only ever appear in the Facebook JS SDK's own login response
#: (`authResponse.signedRequest`, and the `fbsr_<appid>` cookie it also sets) -- never in Meta's
#: server-to-server deauthorize or data-deletion callback. See rule 3 in the module docstring.
_LOGIN_RESPONSE_ONLY_FIELDS: Final = ("code", "oauth_token")


class SignedRequestError(ValueError):
    """The `signed_request` is oversized, malformed, wrongly signed, names an algorithm this
    service does not trust, or has the Facebook JS SDK's own login-response shape rather than a
    server-to-server callback's. Never carries which of those it was -- see the module docstring."""


@dataclass(frozen=True, slots=True)
class SignedRequestPayload:
    """What one `signed_request` decodes to, once its signature has verified. Every field but
    `payload_segment` is Meta's own vocabulary; nothing else in the raw payload is read onto this
    type."""

    #: The Facebook-scoped id of the person the callback is about.
    user_id: str
    #: Meta's own Unix timestamps on the payload. Read for logging only, never for a security
    #: decision -- the signature above is the whole of that. A JSON `true`/`false` is never
    #: accepted here even though Python's `int` is technically a supertype of `bool`: a boolean is
    #: not one of Meta's own timestamps, whatever `isinstance` says.
    issued_at: int | None
    expires: int | None
    #: The still-base64url-encoded payload segment -- exactly the bytes the signature above
    #: covers. `app/w1_services`'s idempotency keys are derived from this, not from `issued_at`:
    #: Meta fixes the whole payload (`issued_at` included) at sign time and resends the identical
    #: bytes on a genuine redelivery, so a hash of this segment is idempotent on its own and never
    #: collapses two distinct requests that both happen to omit `issued_at`.
    payload_segment: str


def _b64url_decode(segment: str) -> bytes:
    """Base64url, with the `=` padding Meta's encoder omits restored. `validate=True` refuses a
    segment carrying a character outside the base64url alphabet instead of silently discarding it
    (the default, non-validating decoder's own behaviour) -- the same "do not tolerate a malformed
    input that happens to still decode to something" instinct as every other check here. Refuses
    rather than lets `binascii.Error` or a stray `ValueError` reach the caller as something other
    than a refusal."""
    padded = segment + "=" * (-len(segment) % 4)
    try:
        standard = padded.translate(_URLSAFE_TO_STANDARD).encode("ascii")
        return base64.b64decode(standard, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise SignedRequestError("signed_request is not valid base64url") from exc


#: `base64.urlsafe_b64decode` does this translation internally but does not expose `validate=True`
#: to its caller, so it is done by hand here and handed to the standard decoder instead.
_URLSAFE_TO_STANDARD = str.maketrans({"-": "+", "_": "/"})


def extract_signed_request(raw_body: bytes) -> str:
    """The `signed_request` field from an `application/x-www-form-urlencoded` POST body -- what
    Meta's own PHP example reads as `$_POST['signed_request']`.

    Uses the standard library's own query-string decoder (`urllib.parse.parse_qsl`) rather than a
    framework's request-body form parser: the caller has already capped and fully read `raw_body`
    (`MAX_SIGNED_REQUEST_BYTES`, before this function ever sees it), and a small, already-bounded
    byte string is simpler to hand to a pure decoder here than to thread a framework request object
    into a module that otherwise does no I/O at all.
    """
    if len(raw_body) > MAX_SIGNED_REQUEST_BYTES:
        raise SignedRequestError("body exceeds the signed_request size limit")
    try:
        text = raw_body.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SignedRequestError("body is not ASCII") from exc

    for name, value in parse_qsl(text, keep_blank_values=False, strict_parsing=False):
        if name == "signed_request" and value:
            return value
    raise SignedRequestError("no signed_request field in the body")


def _is_real_int(value: Any) -> bool:
    """`True` only for a genuine JSON number Python decoded as `int` -- never a `bool`, which
    `isinstance(x, int)` alone would wrongly admit (`bool` is a subclass of `int` in Python)."""
    return isinstance(value, int) and not isinstance(value, bool)


def parse_signed_request(value: str, app_secret: str) -> SignedRequestPayload:
    """Verify and decode one `signed_request` value. Raises `SignedRequestError` for every
    refusal (§ the five rules in the module docstring); returns the payload only once all five
    have passed."""
    if not app_secret:
        # A caller with no secret configured must refuse, not decode a payload it could never
        # actually verify -- the same instinct `webhooks.receive` already holds itself to; this
        # function does not trust its own caller to have checked first.
        raise SignedRequestError("no app secret configured")
    if len(value) > MAX_SIGNED_REQUEST_BYTES:
        raise SignedRequestError("signed_request exceeds the size limit")

    parts = value.split(".")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise SignedRequestError("not a signed_request")
    encoded_sig, encoded_payload = parts

    signature = _b64url_decode(encoded_sig)
    decoded_payload = _b64url_decode(encoded_payload)
    try:
        parsed: Any = json.loads(decoded_payload)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise SignedRequestError("payload is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise SignedRequestError("payload is not a JSON object")

    # Rule 3: refused before any other field of this (still merely *decoded*, not yet verified)
    # payload is trusted for anything -- see the module docstring for why a validly-signed payload
    # can still be the wrong thing entirely.
    if any(field in parsed for field in _LOGIN_RESPONSE_ONLY_FIELDS):
        raise SignedRequestError("signed_request has the SDK login-response shape")

    algorithm = parsed.get("algorithm")
    if not isinstance(algorithm, str):
        raise SignedRequestError("unsupported signed_request algorithm")
    try:
        algorithm_ascii = algorithm.encode("ascii")
    except UnicodeEncodeError as exc:
        raise SignedRequestError("unsupported signed_request algorithm") from exc
    # `bytes.upper()`, not `str.upper()`: the latter is Unicode-aware and would fold characters
    # outside plain ASCII (a Kelvin sign, a Turkish dotless i, ...) onto a letter they are not
    # byte-identical to, which is not the tolerance rule 4 in the module docstring means to grant.
    if algorithm_ascii.upper() != _EXPECTED_ALGORITHM_ASCII:
        raise SignedRequestError("unsupported signed_request algorithm")

    # Rule 5, last: the signature is over the *encoded* payload segment, exactly the bytes Meta
    # signed -- never the decoded JSON, which re-serialising would not reproduce byte-for-byte.
    expected = hmac.new(
        app_secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    if not hmac.compare_digest(expected, signature):
        raise SignedRequestError("signed_request signature does not match")

    user_id = parsed.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise SignedRequestError("no user_id in signed_request payload")

    issued_at = parsed.get("issued_at")
    expires = parsed.get("expires")
    return SignedRequestPayload(
        user_id=user_id,
        issued_at=issued_at if _is_real_int(issued_at) else None,
        expires=expires if _is_real_int(expires) else None,
        payload_segment=encoded_payload,
    )
