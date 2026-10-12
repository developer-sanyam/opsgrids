"""India's GST rules as pure functions — shared by finance (tenant books) and billing (the
platform's own tax invoices), per ADR-0004 Q1 decision 3.

GSTIN parsing and its checksum, the state-code table, the financial year, the place-of-supply
test and the CGST/SGST/IGST split. Integer minor units throughout: a tax split must foot
*exactly*, because the heads are declared independently on a return and an e-invoice, and a
paisa of drift between them is a filing mismatch. Nothing here divides by 100, and nothing here
is a float.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

#: Supplies that are inter-state *whatever* the two state codes say. The SEZ case is the one
#: that catches people out: same state, still IGST.
INTERSTATE_SUPPLY_TYPES: frozenset[str] = frozenset({"export", "sez"})


# --------------------------------------------------------------------- GST identity
# A GSTIN is not a string, it is a structured identifier with a checksum. Treating it as a string
# is how a transposed digit reaches a return and comes back as a mismatch weeks later, so it is
# parsed into a value object at every boundary and stored only after it has been parsed.

#: GST state codes → the state or union territory they denote. The first two characters of every
#: GSTIN, and the vocabulary `place_of_supply` is drawn from.
#:
#: `25` (Daman and Diu) and `28` (undivided Andhra Pradesh) are **legacy**: both were retired by
#: reorganisations, and both still appear on registrations issued before them. They are listed so
#: a historical GSTIN parses rather than being refused as malformed.
GST_STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    # Suffixed so the name→code map has no ambiguous key: an unqualified "Daman and Diu" today
    # means the merged union territory, 26.
    "25": "Daman and Diu (legacy)",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh (undivided)",  # legacy — see 37
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}

#: Name → code, for the free-text `state` a customer record carries. Lower-cased and stripped of
#: punctuation on both sides so "Tamil Nadu", "tamil nadu" and "TAMIL NADU" all resolve.
_STATE_CODE_BY_NAME: dict[str, str] = {
    # Built from the table above, plus the everyday spellings that are not the statutory name.
    **{name.lower(): code for code, name in GST_STATE_CODES.items()},
    "andhra pradesh (undivided)": "28",
    "orissa": "21",
    "pondicherry": "34",
    "uttaranchal": "05",
    "new delhi": "07",
    "nct of delhi": "07",
    "jammu & kashmir": "01",
    "dadra and nagar haveli": "26",
    "daman and diu": "26",
    "andaman & nicobar islands": "35",
}

#: The base-36 alphabet the GSTIN check digit is computed over. Order is load-bearing: the
#: character's index *is* its value.
_GSTIN_ALPHABET: str = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"

#: Two state digits, a ten-character PAN (5 letters, 4 digits, 1 letter), a registration serial
#: for that PAN within the state, a fixed `Z`, and the check digit.
#:
#: The fixed `Z` is deliberate. It is `Z` on every regular registration; the exceptions are UIN
#: holders (embassies, UN bodies) and OIDAR registrations, neither of which is a customer this
#: product bills today. Refusing them loudly is better than accepting an identifier the rest of
#: the pipeline would then have to special-case — and the checksum catches an ordinary typo in
#: that position anyway.
_GSTIN_PATTERN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")


class GstinError(ValueError):
    """A GSTIN that is not one. Carries a reason a person can act on."""


@dataclass(frozen=True, slots=True)
class Gstin:
    """A parsed, checksum-verified GST identification number.

    Constructed only by `parse_gstin`, so holding one of these is proof the number is
    well-formed, names a real state, and carries the right check digit.
    """

    value: str
    state_code: str
    pan: str
    #: The registration serial for this PAN within this state — `1` for a first registration.
    entity_code: str
    check_digit: str

    @property
    def state_name(self) -> str:
        return GST_STATE_CODES[self.state_code]

    def __str__(self) -> str:
        return self.value


def gstin_check_digit(base: str) -> str:
    """The 15th character of a GSTIN, computed from the first fourteen.

    GSTN's own algorithm, and worth stating precisely because every plausible-looking variant of
    it produces a different answer. Walking the base **right to left** with the multiplier
    starting at 2 and alternating 1/2, each product is folded (quotient plus remainder over 36)
    before it is summed — the fold is what makes it a Luhn-style check over a base-36 alphabet
    rather than a plain weighted sum, and it is what catches a transposition.

    Raises `GstinError` if `base` is not fourteen characters from the GSTIN alphabet.
    """
    if len(base) != 14:
        raise GstinError("a GSTIN check digit is computed over exactly 14 characters")

    factor = 2
    total = 0
    for char in reversed(base):
        index = _GSTIN_ALPHABET.find(char)
        if index < 0:
            raise GstinError(f"{char!r} is not a character a GSTIN may contain")
        product = factor * index
        factor = 1 if factor == 2 else 2
        total += (product // 36) + (product % 36)
    return _GSTIN_ALPHABET[(36 - (total % 36)) % 36]


def normalise_gstin(raw: str) -> str:
    """Upper-case and strip the spaces and hyphens people paste in. No validation."""
    return re.sub(r"[\s\-]", "", raw).upper()


def parse_gstin(raw: str) -> Gstin:
    """Parse and verify a GSTIN, or raise `GstinError` saying which of the four checks failed.

    The four, in the order a wrong number usually fails them: length, structure, a state code
    that exists, and the checksum. The checksum is last because it is the one that catches the
    errors the eye does not — a transposed pair, one digit off.
    """
    value = normalise_gstin(raw)
    if len(value) != 15:
        raise GstinError(f"a GSTIN is 15 characters; {value!r} is {len(value)}")
    if not _GSTIN_PATTERN.match(value):
        raise GstinError(
            f"{value!r} is not shaped like a GSTIN "
            "(two state digits, a PAN, a registration serial, 'Z', a check digit)"
        )

    state_code = value[:2]
    if state_code not in GST_STATE_CODES:
        raise GstinError(f"{state_code!r} is not a GST state code")

    expected = gstin_check_digit(value[:14])
    if value[14] != expected:
        raise GstinError(
            f"{value!r} fails its checksum: the check digit should be {expected!r}, not "
            f"{value[14]!r}"
        )

    return Gstin(
        value=value,
        state_code=state_code,
        pan=value[2:12],
        entity_code=value[12],
        check_digit=value[14],
    )


def is_valid_gstin(raw: str) -> bool:
    """`True` iff `raw` parses. For call sites that want a predicate rather than the reason."""
    try:
        parse_gstin(raw)
    except GstinError:
        return False
    return True


def state_code_for_name(name: str | None) -> str | None:
    """Best-effort state name → GST state code, for the free-text `state` on a customer row.

    Returns None rather than guessing when the name is not recognised: an unknown state must
    become a missing place of supply that somebody fixes, never a wrong one that quietly picks a
    tax head.
    """
    if not name:
        return None
    key = re.sub(r"[.,]", "", name.strip().lower())
    return _STATE_CODE_BY_NAME.get(key)


def financial_year(on: date) -> str:
    """India's financial year for a date, as the IRP writes it: April to March, `2026-27`.

    Part of the IRN input, so it is a domain rule rather than a formatting detail.
    """
    start = on.year if on.month >= 4 else on.year - 1
    return f"{start}-{str(start + 1)[-2:]}"


# --------------------------------------------------------------------- the tax split
# Integer arithmetic end to end. The invariant every function here preserves: the components sum
# to the total, exactly, at every level — line, document, and return.


@dataclass(frozen=True, slots=True)
class TaxSplit:
    """One taxable amount's GST, split across the heads it is payable under.

    Exactly one of the two shapes is ever populated: CGST+SGST for an intra-state supply, IGST
    for an inter-state one. Both are carried on the same object so a caller never has to ask
    which shape it got before summing.
    """

    cgst_minor: int = 0
    sgst_minor: int = 0
    igst_minor: int = 0

    @property
    def total_minor(self) -> int:
        return self.cgst_minor + self.sgst_minor + self.igst_minor

    def __add__(self, other: TaxSplit) -> TaxSplit:
        """Component-wise, so a document's split is `sum(line splits)` and foots by construction."""
        return TaxSplit(
            cgst_minor=self.cgst_minor + other.cgst_minor,
            sgst_minor=self.sgst_minor + other.sgst_minor,
            igst_minor=self.igst_minor + other.igst_minor,
        )


ZERO_TAX = TaxSplit()


def gst_amount_minor(taxable_minor: int, rate_pct: Decimal) -> int:
    """Tax on a taxable amount, in minor units, rounded half-up **once**.

    Exact via `Decimal`: a rate is a rate (12, 18, 0.25) and a float would make 18 % of a large
    receivable land a paisa out often enough to matter. `ROUND_HALF_UP` on a `Decimal` rounds
    away from zero, so a credit note's negative tax rounds symmetrically with the invoice's.
    """
    exact = Decimal(taxable_minor) * rate_pct / Decimal(100)
    return int(exact.to_integral_value(rounding=ROUND_HALF_UP))


def split_gst(taxable_minor: int, rate_pct: Decimal, *, intra_state: bool) -> TaxSplit:
    """Split the GST on one taxable amount into the heads it is payable under.

    Intra-state, the rate is levied half as CGST (central) and half as SGST (state). The halves
    are **not** each rounded: the total is computed once and then divided, with the odd paisa
    going to SGST, so `cgst + sgst == gst_amount_minor(...)` for every input rather than for the
    even ones. Rounding each half independently is the bug this shape exists to prevent — it
    puts the document a paisa out from its own lines about half the time on odd rates.

    The odd paisa goes to SGST in both directions of sign, so a credit note reverses an invoice
    head for head instead of leaving a paisa stranded in CGST.
    """
    return split_gst_total(gst_amount_minor(taxable_minor, rate_pct), intra_state=intra_state)


def split_gst_total(total_minor: int, *, intra_state: bool) -> TaxSplit:
    """Split an already-computed GST total into its heads: IGST whole, or CGST the half rounded
    toward zero and SGST the remainder — so the heads always add back to `total_minor`.

    The half of `split_gst` for a caller that computes the total itself (billing computes it once
    on the invoice subtotal, ADR-0004 Q1).
    """
    if not intra_state:
        return TaxSplit(igst_minor=total_minor)
    half = abs(total_minor) // 2
    cgst = -half if total_minor < 0 else half
    return TaxSplit(cgst_minor=cgst, sgst_minor=total_minor - cgst)


def is_intra_state(*, supply_type: str, seller_state_code: str, place_of_supply: str) -> bool:
    """Whether a supply attracts CGST+SGST rather than IGST.

    Two state codes being equal is the ordinary test, but it is not the whole rule: an export or
    a supply to a Special Economic Zone is inter-state **by statute** even when the recipient
    sits in the seller's own state. That is the case a naive `seller == buyer` comparison gets
    wrong, and it is wrong in the expensive direction — tax paid under the wrong head is not a
    rounding difference, it is a refund claim.
    """
    if supply_type in INTERSTATE_SUPPLY_TYPES:
        return False
    return bool(seller_state_code) and seller_state_code == place_of_supply
