"""Password hashing, password policy, and TOTP.

Argon2id rather than bcrypt: memory-hard, so a GPU farm gains far less, and the reference
implementation picks sane parameters. `verify` deliberately swallows every failure mode
into one `False` — distinguishing "wrong password" from "corrupt hash" at the call site is
how a timing or error-message oracle appears.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from platform_core.errors import ValidationProblem

_hasher = PasswordHasher()

#: Passwords that appear in every breach corpus. A short, high-signal list beats a long
#: one nobody maintains — the real defence is length plus lockout, not a dictionary.
_COMMON = frozenset(
    {
        "password",
        "password1",
        "password123",
        "12345678",
        "123456789",
        "qwerty123",
        "letmein123",
        "welcome123",
        "admin@123",
        "atmora123",
        "changeme123",
    }
)

MIN_LENGTH = 10


def hash_password(raw: str) -> str:
    return str(_hasher.hash(raw))


def verify_password(hashed: str | None, raw: str) -> bool:
    """Constant-ish time check that never says *why* it failed."""
    if not hashed:
        # No password set — an SSO-only account. Still burn a hash so the response time
        # does not reveal which accounts have passwords.
        _hasher.hash(raw)
        return False
    try:
        return bool(_hasher.verify(hashed, raw))
    except (VerifyMismatchError, InvalidHashError, ValueError):
        return False


def needs_rehash(hashed: str) -> bool:
    """True when the stored hash predates the current parameters.

    Called on every successful sign-in, so raising the cost factor propagates as people
    log in rather than needing a migration nobody can run (the plaintext is only ever
    available at this moment).
    """
    try:
        return bool(_hasher.check_needs_rehash(hashed))
    except (InvalidHashError, ValueError):
        return True


def validate_password(raw: str, *, min_length: int = MIN_LENGTH) -> None:
    """Reject a password, with every reason at once.

    Returning all failures together rather than the first is a usability decision: a form
    that reveals one rule per attempt trains people to pick the shortest thing that passes.
    """
    errors: list[dict[str, str]] = []
    if len(raw) < min_length:
        errors.append(
            {
                "field": "password",
                "code": "too_short",
                "message": f"Use at least {min_length} characters",
            }
        )
    if raw.lower() in _COMMON:
        errors.append(
            {
                "field": "password",
                "code": "too_common",
                "message": "That password appears in known breach lists",
            }
        )
    if raw.isalpha() or raw.isdigit():
        errors.append(
            {
                "field": "password",
                "code": "too_simple",
                "message": "Mix letters with numbers or symbols",
            }
        )
    if errors:
        raise ValidationProblem("That password cannot be used", errors=errors)


# --------------------------------------------------------------------- TOTP

TOTP_STEP = 30
TOTP_DIGITS = 6
#: One step either side, so a phone whose clock drifts slightly still works. Wider than
#: this and a captured code stays usable for minutes.
TOTP_WINDOW = 1


def generate_totp_secret() -> str:
    """A base32 secret, the format every authenticator app expects."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def totp_uri(secret: str, *, account: str, issuer: str = "Atmora") -> str:
    """The `otpauth://` URI behind the QR code."""
    from urllib.parse import quote

    label = quote(f"{issuer}:{account}")
    return (
        f"otpauth://totp/{label}?secret={secret}&issuer={quote(issuer)}"
        f"&algorithm=SHA1&digits={TOTP_DIGITS}&period={TOTP_STEP}"
    )


def _totp_at(secret: str, step: int) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8))
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(code % (10**TOTP_DIGITS)).zfill(TOTP_DIGITS)


def verify_totp(secret: str, code: str, *, last_used_step: int | None = None) -> int | None:
    """Check a code and return the step it matched, or None.

    Returning the **step** rather than a boolean is what lets the caller store it and
    refuse a replay: without that, a code shoulder-surfed from a screen stays valid for
    the rest of its window.
    """
    candidate = code.strip().replace(" ", "")
    if not candidate.isdigit() or len(candidate) != TOTP_DIGITS:
        return None

    now_step = int(time.time()) // TOTP_STEP
    for offset in range(-TOTP_WINDOW, TOTP_WINDOW + 1):
        step = now_step + offset
        if last_used_step is not None and step <= last_used_step:
            continue
        if hmac.compare_digest(_totp_at(secret, step), candidate):
            return step
    return None


# --------------------------------------------------------------------- recovery codes


def generate_recovery_codes(count: int = 10) -> list[str]:
    """Human-transcribable single-use codes."""
    return ["-".join(secrets.token_hex(2) for _ in range(3)) for _ in range(count)]


def hash_recovery_code(code: str) -> str:
    """Hashed like a password: these are credentials, and people write them down."""
    return hash_password(code.strip().lower())


def verify_recovery_code(hashed: str, code: str) -> bool:
    return verify_password(hashed, code.strip().lower())
