"""Meta's `signed_request` scheme — the deauthorize and data-deletion callbacks' whole trust
boundary. Pure, no database, no HTTP: this is the module both routes call before they read the
callback's payload as anything trustworthy, so it is verified in isolation first.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest

from app import meta_signed_request as msr

# Named with the repository's `ci-only-` prefix, which the secret scanner's exclude pattern
# recognises — see tests/test_webhooks.py for the same convention.
SECRET = "ci-only-app-secret"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _signed_request(payload: dict, *, secret: str = SECRET, algorithm: str = "HMAC-SHA256") -> str:
    """Build a genuine `signed_request` value the way Meta's own reference implementation does:
    base64url(JSON) as the payload segment, HMAC-SHA256 of that segment (still encoded) as the
    signature segment."""
    body = {"algorithm": algorithm, **payload}
    encoded_payload = _b64url(json.dumps(body).encode("utf-8"))
    signature = hmac.new(
        secret.encode("utf-8"), encoded_payload.encode("ascii"), hashlib.sha256
    ).digest()
    return f"{_b64url(signature)}.{encoded_payload}"


def _form_body(value: str) -> bytes:
    return f"signed_request={value}".encode("ascii")


# --------------------------------------------------------------------- parse_signed_request


def test_a_genuine_signed_request_verifies_and_decodes() -> None:
    value = _signed_request({"user_id": "218471", "issued_at": 1750263773, "expires": 1750350173})
    payload = msr.parse_signed_request(value, SECRET)
    assert payload.user_id == "218471"
    assert payload.issued_at == 1750263773
    assert payload.expires == 1750350173


def test_the_algorithm_check_is_case_insensitive() -> None:
    value = _signed_request({"user_id": "1"}, algorithm="hmac-sha256")
    assert msr.parse_signed_request(value, SECRET).user_id == "1"


def test_a_tampered_signature_is_refused() -> None:
    """The attack this exists to stop: a forged deauthorize or data-deletion claim naming
    someone else's Facebook user id."""
    genuine = _signed_request({"user_id": "218471"})
    sig, _payload = genuine.split(".")
    forged_payload = _b64url(json.dumps({"algorithm": "HMAC-SHA256", "user_id": "999999"}).encode())
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(f"{sig}.{forged_payload}", SECRET)


def test_a_signature_from_the_wrong_secret_is_refused() -> None:
    value = _signed_request({"user_id": "218471"}, secret="ci-only-a-different-secret")
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(value, SECRET)


def test_an_unsupported_algorithm_is_refused_even_with_a_genuine_signature() -> None:
    value = _signed_request({"user_id": "218471"}, algorithm="MD5")
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(value, SECRET)


def test_malformed_base64_is_refused() -> None:
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request("not-base64!!.also-not-base64!!", SECRET)


def test_a_payload_that_is_not_json_is_refused() -> None:
    not_json = _b64url(b"not a json object")
    # Sign the not-JSON payload correctly, so only the JSON-parse step can be what fails.
    real_sig = _b64url(hmac.new(SECRET.encode(), not_json.encode("ascii"), hashlib.sha256).digest())
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(f"{real_sig}.{not_json}", SECRET)


def test_a_json_array_instead_of_an_object_is_refused() -> None:
    encoded_payload = _b64url(json.dumps(["not", "an", "object"]).encode())
    digest = hmac.new(SECRET.encode(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    sig = _b64url(digest)
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(f"{sig}.{encoded_payload}", SECRET)


def test_no_dot_is_refused() -> None:
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request("nodothere", SECRET)


def test_an_empty_half_is_refused() -> None:
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(".onlypayload", SECRET)
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request("onlysignature.", SECRET)


def test_missing_user_id_is_refused() -> None:
    encoded_payload = _b64url(json.dumps({"algorithm": "HMAC-SHA256"}).encode())
    digest = hmac.new(SECRET.encode(), encoded_payload.encode("ascii"), hashlib.sha256).digest()
    sig = _b64url(digest)
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(f"{sig}.{encoded_payload}", SECRET)


def test_an_oversized_value_is_refused_before_anything_is_decoded() -> None:
    huge = "a" * (msr.MAX_SIGNED_REQUEST_BYTES + 1)
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(huge, SECRET)


def test_issued_at_and_expires_are_optional_and_never_required_for_trust() -> None:
    """A payload missing the timestamps still verifies — they are read for logging only, never
    for the security decision or (since this round) the confirmation-code idempotency key, which
    is now derived from the payload segment itself (`payload_segment` below)."""
    value = _signed_request({"user_id": "1"})
    payload = msr.parse_signed_request(value, SECRET)
    assert payload.issued_at is None
    assert payload.expires is None


def test_a_json_bool_is_never_treated_as_issued_at_or_expires() -> None:
    """`bool` is a subclass of `int` in Python, so `isinstance(True, int)` is `True` — a JSON
    `true`/`false` in either field must not be accepted as one of Meta's own timestamps just
    because of that."""
    value = _signed_request({"user_id": "1", "issued_at": True, "expires": False})
    payload = msr.parse_signed_request(value, SECRET)
    assert payload.issued_at is None
    assert payload.expires is None


def test_payload_segment_is_the_still_encoded_payload() -> None:
    """Exposed so callers can derive an idempotency key from the exact bytes the signature covers,
    rather than from individual fields that may be missing or malformed."""
    value = _signed_request({"user_id": "1"})
    _sig, encoded_payload = value.split(".")
    payload = msr.parse_signed_request(value, SECRET)
    assert payload.payload_segment == encoded_payload


# --------------------------------------------------------------------- the SDK login-response shape


def test_a_payload_carrying_code_is_refused_outright() -> None:
    """The lever the security review flagged: Meta signs `FB.login`'s own response with the same
    app secret, in the same shape, plus `code` — a field the real server-to-server callback never
    carries. A validly-signed payload carrying it must still be refused."""
    value = _signed_request({"user_id": "218471", "issued_at": 1, "code": "one-time-code"})
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(value, SECRET)


def test_a_payload_carrying_oauth_token_is_refused_outright() -> None:
    """The older SDK's own equivalent of `code`."""
    value = _signed_request({"user_id": "218471", "oauth_token": "some-token"})
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(value, SECRET)


def test_a_genuine_callback_payload_with_neither_field_still_verifies() -> None:
    """The refusal above is specific to `code`/`oauth_token` — it must not become a refusal of
    every payload that happens to carry an extra field Meta might one day add."""
    value = _signed_request({"user_id": "218471", "issued_at": 1, "something_else": "1"})
    payload = msr.parse_signed_request(value, SECRET)
    assert payload.user_id == "218471"


# --------------------------------------------------------------------- algorithm, ASCII-only


def test_the_algorithm_check_does_not_fold_non_ascii_letters_onto_ascii_ones() -> None:
    """`str.upper()` is Unicode-aware: the Kelvin sign `"K"` upper-cases to itself and,
    depending on the Unicode version, some casefold tables can equate look-alike letters across
    scripts. `bytes.upper()` only ever touches ASCII `a`-`z`, so an algorithm string that is not
    plain ASCII must be refused outright rather than compared after a Unicode-aware fold."""
    value = _signed_request({"user_id": "1"}, algorithm="HMAC-SHA256K")
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(value, SECRET)


# --------------------------------------------------------------------- no app secret configured


def test_parse_signed_request_refuses_itself_with_no_app_secret_configured() -> None:
    """Refuses on its own rather than trust its one caller (the route) to have checked first."""
    value = _signed_request({"user_id": "1"})
    with pytest.raises(msr.SignedRequestError):
        msr.parse_signed_request(value, "")


# --------------------------------------------------------------------- extract_signed_request


def test_extracts_the_field_from_a_form_encoded_body() -> None:
    value = _signed_request({"user_id": "1"})
    assert msr.extract_signed_request(_form_body(value)) == value


def test_extracts_the_field_alongside_others() -> None:
    value = _signed_request({"user_id": "1"})
    body = f"other=1&signed_request={value}&more=2".encode("ascii")
    assert msr.extract_signed_request(body) == value


def test_a_body_with_no_signed_request_field_is_refused() -> None:
    with pytest.raises(msr.SignedRequestError):
        msr.extract_signed_request(b"nothing=here")


def test_an_oversized_body_is_refused_before_it_is_parsed() -> None:
    huge_body = b"signed_request=" + b"a" * msr.MAX_SIGNED_REQUEST_BYTES
    with pytest.raises(msr.SignedRequestError):
        msr.extract_signed_request(huge_body)


def test_a_non_ascii_body_is_refused() -> None:
    with pytest.raises(msr.SignedRequestError):
        msr.extract_signed_request("signed_request=café".encode())


# --------------------------------------------------------------------- end to end


def test_extract_then_parse_round_trips_a_genuine_callback() -> None:
    value = _signed_request({"user_id": "218471", "issued_at": 1750263773})
    body = _form_body(value)
    extracted = msr.extract_signed_request(body)
    payload = msr.parse_signed_request(extracted, SECRET)
    assert payload.user_id == "218471"
    assert payload.issued_at == 1750263773
