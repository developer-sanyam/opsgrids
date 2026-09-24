"""Pure domain logic for the attendance service — no I/O.

Everything here is pure, so it is exhaustively testable without a database and read in one place
rather than scattered across the write path:

* the **attendance status machine** — which manual corrections are legal — mirroring the
  employee service's lifecycle machine;
* the **check-out ordering** rule — a check-out may not precede its check-in;
* the **reporting-line** helpers behind TEAM scope; and
* the **anti-spoofing** arithmetic (R22): distance, travel plausibility, address and range
  handling, user-agent classes.

The geo-fence test is deliberately *not* here: it is a PostGIS query in `services.check_in`, not
app-side maths (CLAUDE.md, plan A.4). The anti-spoofing rules further down are a different
question — "could one person have made these two punches" — with no fence to ask the database
about, so they are plain functions over the numbers the punches carry.
"""

from __future__ import annotations

import enum
import ipaddress
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime


class AttendanceStatus(enum.StrEnum):
    """The states a day's attendance record can hold.

    A punch opens the day as `present`. `absent` and `half_day` are the other observed states;
    `regularized` is the corrected end-state an authorised caller moves a record to, and — like
    the employee lifecycle's `exited` — it is terminal, so a correction of a correction is a
    fresh, audited act rather than a silent overwrite.
    """

    PRESENT = "present"
    HALF_DAY = "half_day"
    ABSENT = "absent"
    REGULARIZED = "regularized"


#: The only status corrections the business allows. A `present` day may be cut to `half_day` or
#: signed off as `regularized`; an `absent` day may be corrected to any observed state or
#: regularized; a `half_day` may be restored, marked absent, or regularized. `regularized` is
#: terminal — re-opening a signed-off day is a new record, not a resurrection, so history stays
#: clean. No state may transition to itself: a no-op correction is a mistake, not a change.
ALLOWED_TRANSITIONS: dict[AttendanceStatus, frozenset[AttendanceStatus]] = {
    AttendanceStatus.PRESENT: frozenset(
        {AttendanceStatus.HALF_DAY, AttendanceStatus.ABSENT, AttendanceStatus.REGULARIZED}
    ),
    AttendanceStatus.ABSENT: frozenset(
        {AttendanceStatus.PRESENT, AttendanceStatus.HALF_DAY, AttendanceStatus.REGULARIZED}
    ),
    AttendanceStatus.HALF_DAY: frozenset(
        {AttendanceStatus.PRESENT, AttendanceStatus.ABSENT, AttendanceStatus.REGULARIZED}
    ),
    AttendanceStatus.REGULARIZED: frozenset(),
}


def can_transition(current: AttendanceStatus, target: AttendanceStatus) -> bool:
    """Whether moving from `current` to `target` is a legal status correction."""
    return target in ALLOWED_TRANSITIONS[current]


class IllegalTransition(ValueError):
    """A status correction the machine does not permit.

    Carries the offending pair so the service layer can turn it into a business-rule problem
    with a message that names both states.
    """

    def __init__(self, current: AttendanceStatus, target: AttendanceStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(
            f"An attendance record cannot move from {current.value!r} to {target.value!r}"
        )


def assert_can_transition(current: AttendanceStatus, target: AttendanceStatus) -> None:
    """Raise `IllegalTransition` unless the correction is allowed. The single validation point."""
    if not can_transition(current, target):
        raise IllegalTransition(current, target)


class CheckOutBeforeCheckIn(ValueError):
    """A check-out timestamped at or before its check-in — a day that ended before it began."""

    def __init__(self, check_in_at: datetime, check_out_at: datetime) -> None:
        self.check_in_at = check_in_at
        self.check_out_at = check_out_at
        super().__init__("A check-out cannot be at or before the check-in")


def checkout_after_checkin(check_in_at: datetime, check_out_at: datetime) -> bool:
    """Whether `check_out_at` is strictly later than `check_in_at`."""
    return check_out_at > check_in_at


def assert_checkout_after_checkin(check_in_at: datetime, check_out_at: datetime) -> None:
    """Raise `CheckOutBeforeCheckIn` unless the check-out strictly follows the check-in."""
    if not checkout_after_checkin(check_in_at, check_out_at):
        raise CheckOutBeforeCheckIn(check_in_at, check_out_at)


# --------------------------------------------------------------------- the reporting line (TEAM)

#: `employee.employees.status`'s closed vocabulary, copied onto the TEAM projection.
TEAM_STATUSES: frozenset[str] = frozenset({"probation", "active", "on_notice", "exited"})

#: A person who has left is not a current report, so the TEAM read paths exclude this status.
STATUS_EXITED = "exited"


def normalise_manager(employee_id: uuid.UUID, manager_id: uuid.UUID | None) -> uuid.UUID | None:
    """The manager to store for `employee_id`: `manager_id`, unless it points back at the person.

    Employee-service refuses "reports to themselves" on write, so this never fires on a healthy
    stream. It exists because the TEAM projection sits under an authorization check: a self-loop
    would put a person in their own team, and the projection's own CHECK would turn a corrupt event
    into a dead-lettered message that is never applied. Treating it as "no manager" fails closed —
    the person is in nobody's team — and the caller logs it.
    """
    return None if manager_id == employee_id else manager_id


# --------------------------------------------------------------------- anti-spoofing (R22)
#
# Everything below answers "could one person have made these punches" from numbers the punches
# carry. It only ever *describes* a punch — a reason code and a few counts — and never decides to
# refuse one: the rules are observe-only, and making a flag blocking is a later, separate decision.

#: Mean earth radius. A sphere is the right model here: the error against the WGS84 ellipsoid is
#: about 0.3 %, far below the GPS accuracy a phone reports, and this is a plausibility check, not
#: navigation. (The geo-fence test, which *is* a boundary, stays a PostGIS `geography` query.)
EARTH_RADIUS_M = 6_371_008.8

#: Two fixes closer than this are never "travel": consumer GPS wanders by tens of metres, and a
#: fix with no reported accuracy can wander by more. At the default 900 km/h ceiling, 1 km takes
#: four seconds, so this only stops a jittery fix from being read as teleportation.
MIN_TRAVEL_DISTANCE_M = 1_000.0


class FlagReason(enum.StrEnum):
    """Why a punch was flagged. A closed vocabulary: the database CHECKs it, and a new reason is a
    migration, so a client never meets a code it has no copy for."""

    MOCK_LOCATION = "mock_location"
    IMPOSSIBLE_TRAVEL = "impossible_travel"
    SHARED_DEVICE = "shared_device"
    IP_OUTSIDE_ALLOWED = "ip_outside_allowed"


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two points, in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi = phi2 - phi1
    d_lambda = math.radians(lng2 - lng1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    # `min(1.0, ...)` because rounding can push `a` a hair past 1 for antipodal points, and
    # `asin` of 1.0000000000000002 is a domain error rather than "half the planet".
    return 2 * EARTH_RADIUS_M * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True, slots=True)
class TravelAssessment:
    """What two consecutive located punches say about the trip between them."""

    distance_m: float
    #: The distance left once both fixes' own uncertainty is credited to the person: each fix
    #: could be up to `accuracy` metres from where the phone really was, so that much of the
    #: apparent gap is not evidence of movement.
    effective_m: float
    elapsed_s: float
    speed_kmh: float
    impossible: bool


def assess_travel(
    *,
    from_lat: float,
    from_lng: float,
    from_accuracy_m: float | None,
    from_at: datetime,
    to_lat: float,
    to_lng: float,
    to_accuracy_m: float | None,
    to_at: datetime,
    max_speed_kmh: float,
) -> TravelAssessment:
    """Is the trip from one punch to the next physically impossible?

    **Haversine over accuracy**: the raw great-circle gap is reduced by both fixes' reported
    accuracy radii before it is turned into a speed, so a phone that says "somewhere within 800 m"
    twice is not accused of covering the 800 m between its two guesses. A missing accuracy is
    treated as zero — the person gets no credit they did not claim — and `MIN_TRAVEL_DISTANCE_M`
    is the floor under which nothing is judged at all.

    Elapsed time is floored at one second so two punches stamped in the same instant (or a clock
    that ran backwards across a reconnect) divide by something; the speed is then enormous, which
    is the right answer for two far-apart fixes at "the same time" and moot for two near ones.
    """
    distance = haversine_m(from_lat, from_lng, to_lat, to_lng)
    effective = max(0.0, distance - (from_accuracy_m or 0.0) - (to_accuracy_m or 0.0))
    elapsed = max(1.0, (to_at - from_at).total_seconds())
    speed = (effective / 1000.0) / (elapsed / 3600.0)
    return TravelAssessment(
        distance_m=distance,
        effective_m=effective,
        elapsed_s=elapsed,
        speed_kmh=speed,
        impossible=effective >= MIN_TRAVEL_DISTANCE_M and speed > max_speed_kmh,
    )


def parse_ip(raw: str | None) -> str | None:
    """A normalised IP address, or None for anything that is not one.

    The value arrives from a forwarded header, which is text somebody typed: an empty string, a
    hostname, `unknown`, or a port-suffixed address. Anything that does not parse is treated as
    "no address" rather than stored or matched — a signal that cannot be judged is left out, not
    guessed at.
    """
    if not raw:
        return None
    try:
        address = ipaddress.ip_address(raw.strip())
    except ValueError:
        return None
    if isinstance(address, ipaddress.IPv6Address):
        # `fe80::1%eth0` parses in Python, but the suffix names an interface *on the sender's
        # host*: it is not part of an address anything can route or match a range against,
        # PostgreSQL's INET refuses it, and an arbitrarily long suffix would overflow the masked
        # column. A link-local zone id is no usable client address, so it is no address at all.
        if address.scope_id:
            return None
        # A dual-stack listener reports an IPv4 client as `::ffff:203.0.113.9`. Left in that form
        # it would sit in a different address family from every IPv4 range and read as "outside"
        # all of them, so it is folded back to the IPv4 address it is.
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
    return str(address)


def normalise_cidr(raw: str) -> str:
    """One allowed range in canonical form, or `ValueError`.

    `strict=False` accepts a host address written with a mask (`10.1.2.3/24`) and reads it as the
    network it lives in. A `/0` is refused: an allow-list that allows every address is a rule that
    can never fire, and the person who wrote it should hear that rather than believe they are
    protected.
    """
    network = ipaddress.ip_network(raw.strip(), strict=False)
    if network.prefixlen == 0:
        raise ValueError("A range that covers every address cannot be an allow-list")
    return str(network)


def ip_in_any(ip: str, cidrs: Sequence[str]) -> bool:
    """Whether `ip` lies inside at least one of `cidrs`. An address of one family never matches a
    range of the other (an IPv4 address is not inside an IPv6 range)."""
    address = ipaddress.ip_address(ip)
    return any(address in ipaddress.ip_network(c, strict=False) for c in cidrs)


class UserAgentClass(enum.StrEnum):
    BROWSER = "browser"
    MOBILE_APP = "mobile_app"
    AUTOMATION = "automation"
    UNKNOWN = "unknown"


#: Substrings, lower-cased, that mark a scripted client. Checked first: a headless browser still
#: says "Mozilla" and must not be read as a person at a keyboard.
_AUTOMATION_MARKERS = (
    "curl/",
    "wget/",
    "python-requests",
    "python-httpx",
    "httpx",
    "aiohttp",
    "go-http-client",
    "postman",
    "insomnia",
    "headlesschrome",
    "phantomjs",
    "selenium",
    "playwright",
    "puppeteer",
)
#: HTTP stacks a native app ships with, rather than a browser.
_MOBILE_APP_MARKERS = ("okhttp/", "cfnetwork/", "dart/", "opsgrids-android", "opsgrids-ios")


def classify_user_agent(user_agent: str | None) -> UserAgentClass:
    """A coarse class for a `User-Agent`, never the string itself.

    The class is what is stored: it answers "was this a person in a browser, a native app or a
    script" without keeping a fingerprint-grade string of the person's device. It is a heuristic
    over a header the client chose, so it is a hint for a reviewer and nothing a rule trusts.
    """
    if not user_agent:
        return UserAgentClass.UNKNOWN
    ua = user_agent.lower()
    if any(marker in ua for marker in _AUTOMATION_MARKERS):
        return UserAgentClass.AUTOMATION
    if any(marker in ua for marker in _MOBILE_APP_MARKERS):
        return UserAgentClass.MOBILE_APP
    if ua.startswith("mozilla/"):
        return UserAgentClass.BROWSER
    return UserAgentClass.UNKNOWN
