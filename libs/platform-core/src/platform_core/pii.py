"""The platform PII envelope — encrypt at rest, mask by default, decrypt only on purpose.

"PII is encrypted and masked by default; unmasking needs its own permission and writes an
audit row every time" is a load-bearing platform rule (CLAUDE.md, ADR-0000 §"Also settled").
This module is the mechanism the rule depends on. It generalises the envelope that
auth-service left as a note on its MFA secret — bytes column, encryption "belongs here" —
into something every service can reach for.

Two operations, deliberately asymmetric:

* `seal()` runs on **every write** of a sensitive field. It returns the ciphertext to store
  and a non-sensitive **mask** to store alongside it, so the ordinary read path has something
  safe to show without ever holding the key.
* `open_sealed()` runs only where a caller has proven a `read_sensitive` permission and stepped
  up. It is the single place plaintext comes back, and its caller is the single place a
  sensitive-read audit row is written.

There is **no** SQLAlchemy `TypeDecorator` here on purpose. A type that decrypts on load turns
every ordinary `SELECT` into a decryption — the exact footgun the rule forbids — and one missed
projection then leaks plaintext into a log or an event. Sealing and opening stay explicit in the
service layer, so the decrypt is always a visible, audited call.

## Envelope format

`seal()` produces `bytes` laid out as::

    version (1 byte) | nonce (12 bytes) | AES-256-GCM ciphertext + 16-byte tag

stored in a `LargeBinary` column. Version `0x01` is AES-256-GCM with the key derived from the
configured passphrase by HKDF-SHA256. The version byte is what lets a later scheme (a KMS data
key, a different cipher) coexist with rows sealed under this one, decided by the first byte
rather than by a migration.

## Key rotation

The key is derived from a passphrase, so `settings.pii_encryption_key` may be any string, not a
raw 32 bytes. `open_sealed()` derives from the current passphrase first, then from each
passphrase in `settings.pii_decrypt_keys`, and returns on the first GCM tag that verifies.
Rotation is therefore operational, not a data migration: move the current passphrase into
`pii_decrypt_keys`, put the new one in `pii_encryption_key`, and old rows keep opening while new
rows seal under the new key. GCM's authentication tag is what makes "try the next key" safe —
a wrong key fails to verify rather than returning garbage.
"""

from __future__ import annotations

import hashlib
import hmac
from collections.abc import Callable

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from platform_core.config import ServiceSettings

#: Current envelope version. A new scheme takes the next number and both stay readable, because
#: `open_sealed` dispatches on this byte rather than assuming one layout.
_VERSION = 1
_NONCE_LEN = 12  # 96 bits — the size AES-GCM is specified and fastest for.
_KEY_LEN = 32  # AES-256.
#: HKDF context. Fixed, because derivation has to be deterministic for a passphrase or rotation
#: could never reproduce the key that sealed an existing row. Distinct from any other HKDF use
#: on the same secret so the derived keys cannot collide with them.
_HKDF_INFO = b"opsgrids.pii.aes256gcm.v1"
#: A *different* context for the blind-index key, so the lookup key and the encryption key are
#: independent derivations of the same passphrase. Domain separation: a weakness found in one
#: must not hand over the other.
_HKDF_INFO_INDEX = b"opsgrids.pii.blindindex.v1"

BULLET = "•"  # • — the masking glyph, one constant so every mask reads the same.


class PiiError(Exception):
    """A sealed value could not be opened, or its envelope was malformed.

    Raised rather than returning a partial or a wrong-key plaintext: a masking failure that
    silently produced *some* string would be worse than an error, because it would look like
    real data. Deliberately not a `ProblemError` — decryption failing is an internal fault to
    log and alert on, not a shape to hand a client, and its message must never carry the value.
    """


def _derive_key(passphrase: str) -> bytes:
    """32 bytes from an arbitrary passphrase, by HKDF-SHA256.

    HKDF so the config can hold a human-set passphrase instead of exactly 32 raw bytes, and so
    two unrelated secrets never derive the same key. No salt: the derivation must be
    reproducible from the passphrase alone for an old key to still open old rows during rotation,
    and the passphrase is already a high-entropy secret rather than a password to stretch.
    """
    hkdf = HKDF(algorithm=hashes.SHA256(), length=_KEY_LEN, salt=None, info=_HKDF_INFO)
    return hkdf.derive(passphrase.encode("utf-8"))


def seal(plaintext: str, *, kind: str, settings: ServiceSettings) -> tuple[bytes, str]:
    """Encrypt `plaintext` and derive its mask.

    Returns `(ciphertext, masked)`: the bytes to store in the `<field>_enc` column and the safe
    hint to store in `<field>_masked`. A fresh random nonce per call means sealing the same value
    twice yields different ciphertext — no equality oracle across rows.

    `kind` selects the masker (see `mask`); it does not affect the encryption.
    """
    key = _derive_key(settings.pii_encryption_key.get_secret_value())
    nonce = _random_nonce()
    ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), None)
    envelope = bytes([_VERSION]) + nonce + ciphertext
    return envelope, mask(plaintext, kind=kind)


def open_sealed(ciphertext: bytes, *, settings: ServiceSettings) -> str:
    """Decrypt a sealed value, trying the current key then each rotation key.

    This is the only path back to plaintext, and its callers are the only places a sensitive
    read is audited. Raises `PiiError` when the envelope is malformed or no configured key
    verifies — never a partial or a wrong-key result.
    """
    if len(ciphertext) < 1 + _NONCE_LEN + 16 or ciphertext[0] != _VERSION:
        # Too short to hold a nonce and a GCM tag, or a version this build does not know.
        raise PiiError("Malformed PII envelope")
    nonce = ciphertext[1 : 1 + _NONCE_LEN]
    body = ciphertext[1 + _NONCE_LEN :]

    current = settings.pii_encryption_key.get_secret_value()
    for passphrase in (current, *settings.pii_decrypt_keys):
        try:
            plaintext = AESGCM(_derive_key(passphrase)).decrypt(nonce, body, None)
        except InvalidTag:
            # The signature of a wrong key. Try the next; GCM makes this safe because a bad key
            # cannot forge a valid tag, so no wrong plaintext is ever accepted.
            continue
        return plaintext.decode("utf-8")
    raise PiiError("No configured key could open this value")


def _derive_index_key(passphrase: str) -> bytes:
    """The blind-index key. Same passphrase as encryption, different HKDF context."""
    hkdf = HKDF(algorithm=hashes.SHA256(), length=_KEY_LEN, salt=None, info=_HKDF_INFO_INDEX)
    return hkdf.derive(passphrase.encode("utf-8"))


def blind_index(plaintext: str, *, kind: str, settings: ServiceSettings) -> str:
    """A deterministic, keyed token for finding a sealed value without decrypting it.

    `seal` uses a fresh nonce per call — deliberately, so equal ciphertext cannot reveal that
    two rows hold the same value. The cost is that sealed columns are unsearchable, and some
    questions genuinely need to be asked of them: an inbound WhatsApp message arrives carrying
    a phone number, and *which contact is this* has to be answerable or every conversation
    creates a duplicate customer.

    So this returns `HMAC-SHA256(key, "<kind>:<plaintext>")` as hex. Deterministic, so an exact
    lookup is one indexed query; keyed, so it is not reversible; and it decrypts nothing, so
    finding a match reads no PII and writes no sensitive-read audit rows.

    **The key is the entire security of this.** A phone number is a ten-digit space: an
    *unkeyed* hash of one is reversible by brute force in seconds, and publishing such a column
    would be publishing the numbers. With the key it is not, and an attacker holding the key
    could decrypt the sealed column anyway — so the index adds no exposure that the ciphertext
    beside it does not already carry.

    What it does add, honestly: equality. Anyone who can read the column learns which rows hold
    the same value, which `seal` was specifically arranged not to reveal. For a phone number
    that is a deliberate trade — matching an inbound message *is* an equality question — and it
    is why this is a separate, opt-in function rather than something `seal` returns by default.

    `kind` is mixed into the message so two kinds holding an identical string still index
    differently.

    Rotation: the index is derived from the current key only, so rotating `pii_encryption_key`
    invalidates every stored index and they must be rebuilt from the sealed values. Decryption
    survives rotation through `pii_decrypt_keys`; lookup does not, and a rotation plan has to
    say so.
    """
    key = _derive_index_key(settings.pii_encryption_key.get_secret_value())
    message = f"{kind}:{plaintext}".encode()
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def _random_nonce() -> bytes:
    import os

    return os.urandom(_NONCE_LEN)


# --------------------------------------------------------------------- masking

# A mask is a non-sensitive hint of a sealed value: enough for a human to recognise "yes, that
# is the account ending 3210" without disclosing the secret. Each kind reveals only its
# conventional tail (usually the last four), because that is the part people recognise and the
# part already printed on statements and cards.


def _last(value: str, n: int) -> str:
    """The last `n` characters, or the whole string when it is shorter — never an index error,
    and never revealing more than there is."""
    return value[-n:] if len(value) > n else value


def _tail(value: str, keep: int) -> str:
    """`keep` trailing characters behind a run of bullets standing in for the rest."""
    shown = _last(value, keep)
    hidden = max(len(value) - len(shown), 0)
    return BULLET * hidden + shown


def _mask_email(value: str) -> str:
    """`jane@doe.com` -> `j•••@d•••`. First letter of each side survives; the rest is hidden,
    including the TLD, because a full domain plus a name is often enough to identify a person."""
    local, sep, domain = value.partition("@")
    if not sep:
        return _mask_generic(value)
    head = (local[0] if local else "") + BULLET * 3
    dom = (domain[0] if domain else "") + BULLET * 3
    return f"{head}@{dom}"


def _mask_phone(value: str) -> str:
    """`9876543210` -> `•••••• 3210`. Last four, the digits a caller reads back to confirm."""
    return f"{BULLET * max(len(value) - 4, 0)} {_last(value, 4)}".strip()


def _mask_pan(value: str) -> str:
    """An Indian PAN (`ABCDE1234F`) -> last four characters shown, the rest bulleted.

    The precise glyphs are illustrative; the invariant is that only the tail survives, so the
    first five letters — the part tied to name and status — are never disclosed.
    """
    return _tail(value, 4)


def _mask_aadhaar(value: str) -> str:
    """A 12-digit Aadhaar -> `•••• •••• 3210`, grouped as it is printed. Only the last four,
    which is the most that may be shown of an Aadhaar under UIDAI guidance."""
    return f"{BULLET * 4} {BULLET * 4} {_last(value, 4)}"


def _mask_dob(value: str) -> str:
    """A date of birth -> `••/••/1990`: the year only. Day and month are the identifying part;
    the year alone is a coarse hint that cannot reconstruct the date."""
    year = ""
    for part in value.replace("/", "-").split("-"):
        if len(part) == 4 and part.isdigit():
            year = part
            break
    if not year:
        year = _last(value, 4)
    return f"{BULLET * 2}/{BULLET * 2}/{year}"


def _mask_ifsc(value: str) -> str:
    """An IFSC is a bank branch code, not a secret — it is public and printed on every cheque.
    Shown whole, so a masked record is still recognisable, and classified non-sensitive so the
    field may be stored in plaintext (see the employee model)."""
    return value


def _mask_generic(value: str) -> str:
    """The fallback for any kind without its own masker, and for unknown kinds: bullets plus the
    last two characters. Short enough to leak nothing, long enough to disambiguate a list."""
    return BULLET * max(len(value) - 2, 0) + _last(value, 2)


_MASKERS: dict[str, Callable[[str], str]] = {
    "email": _mask_email,
    "phone": _mask_phone,
    "pan": _mask_pan,
    "aadhaar": _mask_aadhaar,
    "uan": lambda v: _tail(v, 4),
    "esi": lambda v: _tail(v, 4),
    "bank_account": lambda v: _tail(v, 4),
    "bank_ifsc": _mask_ifsc,
    "dob": _mask_dob,
    "generic": _mask_generic,
}

#: The masker kinds this module knows. Anything outside it masks via `generic`.
PII_KINDS = frozenset(_MASKERS)

#: Kinds classified non-sensitive: their masked form is the whole value, and a service may store
#: them in plaintext rather than sealing them. Only `bank_ifsc` today — a public branch code.
NON_SENSITIVE_KINDS = frozenset({"bank_ifsc"})


def mask(plaintext: str, *, kind: str) -> str:
    """The non-sensitive hint for `plaintext`, chosen by `kind`. Unknown kind -> `generic`.

    Total for an empty string, so a stored blank never turns into a misleading hint.
    """
    if not plaintext:
        return ""
    return _MASKERS.get(kind, _mask_generic)(plaintext)


__all__ = [
    "NON_SENSITIVE_KINDS",
    "PII_KINDS",
    "PiiError",
    "mask",
    "open_sealed",
    "seal",
]
