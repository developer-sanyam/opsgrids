"""The storage put-away documents say what the code does (spec, ADR-0014 section 12, ADR-0028,
RUNBOOK).

These pages are the owner's contract and the operator's checklist for the warehouse-map release.
They had drifted from what was built in ways that matter to someone relying on them: an ADR that
named a response flag the API does not have, said the reveal was the only other place a coordinate
goes while a Google map receives the viewport, and cited an ADR section for a rule it does not
state; an amendment that counted "exactly three" differences from `bin_transfer` where there are
eight; a spec heading that the secret scan reads as a credential; and a rollout checklist with no
way to notice a put-away whose proof silently failed to save.

So the tests here pin the *names and numbers* the prose cites against the source that defines
them, rather than the prose's wording: if a response field, a log line, an error code or a
retention window moves, the page that cites it fails here instead of misleading the next reader.
The one exception is the security-sensitive claim that the reveal route needs a one-time code,
which is held both ways: the pages say so exactly when the route does, so reverting the owner
decision (a one-line change to the route) cannot leave a page describing a gate that is gone.

The last test runs the RUNBOOK's "proof not kept" query against a real Postgres and is opt-in,
like the coordinate detector's: `OPSGRIDS_RUNBOOK_TEST_DATABASE_URL` must point at a migrated,
throwaway database and a role that bypasses row-level security (the dev superuser). Everything it
inserts is rolled back.
"""

from __future__ import annotations

import re
import shutil
import uuid
from pathlib import Path

import pytest
from _helpers import real_database_dsn

REPO_ROOT = Path(__file__).resolve().parents[2]
SPEC = REPO_ROOT / "docs" / "superpowers" / "specs" / "2026-10-05-warehouse-map-putaway-design.md"
ADR_0028 = REPO_ROOT / "docs" / "adr" / "0028-putaway-gps-captures.md"
ADR_0014 = REPO_ROOT / "docs" / "adr" / "0014-inventory-and-manufacturing.md"
RUNBOOK = REPO_ROOT / "infrastructure" / "edge" / "RUNBOOK.md"
BACKLOG = REPO_ROOT / "docs" / "backlog.md"
LEDGER = REPO_ROOT / "docs" / "inventory" / "ledger.md"

OPS_APP = REPO_ROOT / "services" / "operations" / "app"
API = OPS_APP / "inventory" / "api.py"
STOCK_OPS = OPS_APP / "inventory" / "stock_ops.py"
PUTAWAY_SCHEMAS = OPS_APP / "inventory" / "putaway_schemas.py"
PUTAWAY_SERVICES = OPS_APP / "inventory" / "putaway_services.py"
PUTAWAY_SELECTORS = OPS_APP / "inventory" / "putaway_selectors.py"
AUTHZ_TESTS = REPO_ROOT / "services" / "operations" / "tests" / "test_putaway_authz.py"
EVENT_SCHEMA = (
    REPO_ROOT
    / "libs"
    / "contracts"
    / "events"
    / "operations.inventory.putaway.recorded"
    / "v1.schema.json"
)
PURGE_TOOL = REPO_ROOT / "tools" / "purge_putaway_captures.py"
PLATFORM_AUTH = REPO_ROOT / "libs" / "platform-core" / "src" / "platform_core" / "auth.py"

#: Every page that carries a claim about the feature, scanned for look-alike secrets below.
DOCUMENTS = [SPEC, ADR_0028, ADR_0014, RUNBOOK, BACKLOG, LEDGER]


def _text(path: Path) -> str:
    return path.read_text()


def _class_body(source: str, name: str) -> str:
    """The text of one top-level `class <name>` up to the next top-level definition."""
    match = re.search(rf"^class {name}\b.*?(?=^class |^def |^# -{{10}}|\Z)", source, re.M | re.S)
    assert match is not None, f"class {name} is no longer in the source"
    return match.group(0)


def _reveal_guard() -> str:
    """The `PutawayLocationRead` dependency, as written in the route module."""
    match = re.search(r"^PutawayLocationRead = Annotated\[.*?^\]", _text(API), re.M | re.S)
    assert match is not None, "PutawayLocationRead is no longer defined in app/inventory/api.py"
    return match.group(0)


# ----------------------------------------------------------------------------- the secret scan


def test_the_put_away_documents_pass_the_repo_secret_scan(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The spec's section 3 heading once carried `down_revision = "<revision id>"`: a quoted hex
    string, which the entropy scanner reads as a credential, so `make secrets` (and CI) refused the
    branch. Reworded; this runs the same hook, with the same baseline, over every page of the
    feature. The baseline is copied: the hook rewrites a baseline it finds out of date, and a test
    must not edit a tracked file."""
    from detect_secrets.pre_commit_hook import main

    baseline = tmp_path / "secrets.baseline"
    shutil.copy(REPO_ROOT / ".secrets.baseline", baseline)
    monkeypatch.chdir(REPO_ROOT)  # the baseline keys files by repo-relative path
    code = main(["--baseline", str(baseline), *[str(p.relative_to(REPO_ROOT)) for p in DOCUMENTS]])
    assert code == 0, capsys.readouterr().out


# ----------------------------------------------------------------------------------- the spec


def test_the_spec_says_where_the_work_stands() -> None:
    # The Status paragraph wraps; read it up to the first blank line.
    status = " ".join(_text(SPEC).split("\nStatus:", 1)[1].split("\n\n", 1)[0].split())
    assert "implemented on branch, pending owner review" in status


def test_the_spec_names_every_amendment_the_review_rounds_made() -> None:
    spec = _text(SPEC)
    # 4.3: the suggestion's barcode and the barcode-only match; 4.3: stock rows' location_kind.
    assert "never suggested" in spec
    assert "A bin's `code` is not a label" in spec
    assert "`location_kind`" in spec
    # 4.6: the cap and the flag, with the real number.
    assert "`truncated`" in spec
    assert "LOCATE_MAX_ROWS = 500" in spec
    assert "LOCATE_MAX_ROWS = 500" in _text(PUTAWAY_SELECTORS)
    # 6: what the queue and request actually do.
    for fact in ("`organizationId`", "408 and 429", "occurred_on` is sent only when"):
        assert fact in spec, f"the spec's worker section lost: {fact}"


# ---------------------------------------------------- the one-time code, held both ways


def _says_the_reveal_needs_a_code(text: str) -> bool:
    return "one-time code" in text or "one-time-code" in text


def test_the_pages_describe_the_reveals_one_time_code_exactly_when_the_route_has_it() -> None:
    """The owner may revert the code (ADR-0028, "Owner decision": delete `amr={"otp"}` from the
    route). When they do this test fails, which is the prompt to take the sentences out of the pages
    too: a security page that describes a gate the route no longer has is worse than one that is
    silent."""
    route_requires_it = 'amr={"otp"}' in _reveal_guard()
    for page in (SPEC, ADR_0028, RUNBOOK, LEDGER):
        assert _says_the_reveal_needs_a_code(_text(page)) == route_requires_it, (
            f"{page.name}: the route {'requires' if route_requires_it else 'no longer requires'} "
            "a one-time code and this page says the opposite; edit the page with the route"
        )


def test_the_spec_and_adr_point_at_a_revert_that_exists() -> None:
    """The revert instructions name a file, a guard and a test class. They are only useful if those
    are still where the pages say."""
    for page in (SPEC, ADR_0028):
        text = _text(page)
        assert "OWNER DECISION" in text or "Owner decision" in text
        assert "TestTheRevealNeedsASessionThatGaveAOneTimeCode" in text
    assert "class TestTheRevealNeedsASessionThatGaveAOneTimeCode" in _text(AUTHZ_TESTS)
    assert "PutawayLocationRead" in _text(SPEC) and "PutawayLocationRead" in _text(ADR_0028)


def test_the_runbook_quotes_the_refusals_the_code_gives() -> None:
    """An operator tells the two 403s apart by body text, so the text must be the code's."""
    runbook = _text(RUNBOOK)
    assert "Additional authentication required: otp" in runbook
    assert 'f"Additional authentication required: {missing}"' in _text(PLATFORM_AUTH)
    assert "Missing permission: operations.inventory.putaway.location_read" in runbook
    assert 'f"Missing permission: {permission}"' in _text(PLATFORM_AUTH)


def test_the_adr_records_why_the_otp_and_stepup_pair_was_rejected() -> None:
    adr = " ".join(_text(ADR_0028).split())
    assert "{otp, stepup}" in adr
    assert "has no step-up UI" in adr
    # The guard really is `otp` alone, so the rejection describes the build.
    guard = _reveal_guard()
    assert "stepup" not in guard


# ------------------------------------------------------------------------------- ADR-0028


def test_adr_0028_names_the_response_flags_the_api_has() -> None:
    """It said the put-away response and the detail read carry `has_location`; the put-away response
    carries `geo_recorded`, and `has_location` is the detail read's and the event's."""
    schemas = _text(PUTAWAY_SCHEMAS)
    assert "geo_recorded" in _class_body(schemas, "PutawayOut")
    assert "has_location" not in _class_body(schemas, "PutawayOut")
    assert "has_location" in _class_body(schemas, "PutawayDetailOut")
    assert '"has_location"' in _text(EVENT_SCHEMA)

    adr = _text(ADR_0028)
    assert "`geo_recorded`" in adr
    flat = " ".join(adr.split())
    assert "the put-away response and the detail read say only `has_location`" not in flat


def test_adr_0028_does_not_cite_adr_0000_for_what_it_does_not_say() -> None:
    """ADR-0000 §9's related immutability rules say ledgers and usage events are append-only; that
    append-only is a *table privilege* is ADR-0002's migration step 0 and ADR-0014 §3. §10 says no
    cross-schema foreign keys and RLS on every table; "a schema per service" is not in it."""
    adr = " ".join(_text(ADR_0028).split())
    assert "§9 (append-only is a table privilege)" not in adr
    assert "§10 (a schema per service" not in adr
    assert "immutability rules" in adr
    # What ADR-0000 §9 and §10 really say is what the corrected citation relies on.
    adr_0000 = _text(REPO_ROOT / "docs" / "adr" / "0000-phase-0-decisions.md")
    assert "## 9. Plan versions are immutable" in adr_0000
    assert "the credit ledger is append-only" in " ".join(adr_0000.split())
    assert "no cross-schema foreign keys" in " ".join(adr_0000.split())
    adr_0002 = " ".join(_text(REPO_ROOT / "docs" / "adr" / "0002-row-level-security.md").split())
    assert "table privileges, not RLS, are the right tool for append-only" in adr_0002


def test_adr_0028_does_not_say_the_reveal_is_the_only_other_place_coordinates_go() -> None:
    adr = " ".join(_text(ADR_0028).split())
    assert "The only other place it travels is the inbound POST body" not in adr
    # The honest version: the map discloses the viewport to Google; text-only exists without a key.
    assert "Google Maps" in adr
    assert "viewport position" in adr
    assert "text-only reveal exists when the map is disabled" in adr


# ------------------------------------------------------------------------------- ADR-0014


def test_adr_0014_does_not_undercount_the_differences_from_a_bin_transfer() -> None:
    amendment = _text(ADR_0014).split("**Amended 2026-10-05", 1)[1].split("**Terminology.**", 1)[0]
    assert "exactly three" not in amendment
    # One bullet per difference; each names a thing the code really has.
    bullets = re.findall(r"^- \*\*(.+?)\.\*\*", amendment, re.M)
    assert len(bullets) >= 8, bullets
    stock_ops = _text(STOCK_OPS)
    for cited in (
        "putaway.recorded",
        "source_not_receiving_or_staging",
        "destination_not_storable",
        "geo_not_recorded",
        "suggestion_ignored",
        "captured_at_adjusted",
    ):
        assert cited in amendment, f"ADR-0014's amendment lost: {cited}"
        assert cited in stock_ops or cited in _text(PUTAWAY_SCHEMAS), f"the code lost: {cited}"
    assert 'PUTAWAY_REFERENCE_TYPE = "putaway"' in stock_ops
    assert 'PUTAWAY_KEY_PREFIX = "putaway:"' in stock_ops
    assert "`putaway:<key>`" in amendment
    assert "uuid5" in amendment and "uuid.uuid5(" in stock_ops
    # A bin transfer genuinely lacks the kind rules and the tracking check the amendment contrasts.
    transfer = stock_ops.split("async def transfer_stock(", 1)[1].split("# ----", 1)[0]
    assert "check_tracking" not in transfer
    assert "PUTAWAY_SOURCE_KINDS" not in transfer and "STORABLE_KINDS" not in transfer
    assert 'reference_type="bin_transfer"' in transfer
    putaway = stock_ops.split("async def putaway_stock(", 1)[1].split("# ----", 1)[0]
    assert "check_tracking" in putaway


# --------------------------------------------------------------------------------- RUNBOOK


def _logged_events() -> set[str]:
    sources = (_text(STOCK_OPS), _text(PUTAWAY_SERVICES))
    return {m for src in sources for m in re.findall(r'log\.\w+\(\s*"(putaway\.[a-z_]+)"', src)}


def test_the_runbook_tells_the_operator_how_to_notice_a_swallowed_capture_failure() -> None:
    """A capture that cannot be written leaves the move in place and answers 201, so no 5xx rate and
    no coordinate scan sees it. The page must name the line the code logs, say that nothing alerts
    on it, and give a check that does not depend on log retention."""
    runbook = _text(RUNBOOK)
    assert "putaway.capture_failed" in _logged_events(), "the service no longer logs this line"
    assert "putaway.capture_failed" in runbook
    assert "geo_not_recorded" in runbook
    flat = " ".join(runbook.split())
    assert "No alert fires on it" in flat
    assert "putaways_without_capture" in runbook


def _proof_query() -> str:
    for block in re.findall(r"```sql\n(.*?)```", _text(RUNBOOK), re.S):
        if "putaways_without_capture" in block:
            return block.strip()
    raise AssertionError("the RUNBOOK lost its 'proof not kept' query")


def test_the_proof_query_reads_what_the_code_writes_inside_the_purge_window() -> None:
    sql = _proof_query()
    assert "operations.inventory_moves" in sql and "operations.putaway_captures" in sql
    assert f"reference_type = '{_const(STOCK_OPS, 'PUTAWAY_REFERENCE_TYPE')}'" in sql
    # A move whose capture was legitimately purged must not count: the window is inside retention.
    window = re.search(r"interval '(\d+) days'", sql)
    assert window is not None, "the query lost its time window"
    days = int(window.group(1))
    retention = int(_const(PURGE_TOOL, "RETENTION_DAYS"))
    assert 0 < days < retention, (days, retention)
    # Joined the way the capture's own unique index is: (organization_id, move_id).
    assert "c.organization_id = m.organization_id" in sql and "c.move_id = m.id" in sql
    assert sql.count(";") == 1 and sql.endswith(";")


def _const(path: Path, name: str) -> str:
    match = re.search(rf'^{name}\s*(?::[^=]+)?=\s*"?([^"\n#]+?)"?\s*(?:#.*)?$', _text(path), re.M)
    assert match is not None, f"{name} is no longer defined in {path.name}"
    return match.group(1).strip()


def test_the_proof_failure_is_a_rollback_trigger_the_operator_can_find() -> None:
    runbook = _text(RUNBOOK)
    triggers = runbook.split("**Roll back when**", 1)[1].split("### The billing run", 1)[0]
    assert "the proof is not being kept" in triggers
    # The coordinate detector's bullet is untouched by this (its own test pins the SQL).
    assert triggers.count("a **coordinate key** is found where none may be") == 1


# --------------------------------------------------------------------- real Postgres (opt-in)
REAL_DSN = real_database_dsn("OPSGRIDS_RUNBOOK_TEST_DATABASE_URL")


@pytest.mark.skipif(
    not REAL_DSN,
    reason="set OPSGRIDS_RUNBOOK_TEST_DATABASE_URL to a migrated throwaway database",
)
async def test_against_a_real_postgres_the_query_counts_only_recent_unproved_put_aways() -> None:
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    assert REAL_DSN is not None
    query = _proof_query()
    org = uuid.uuid4()
    engine = create_async_engine(REAL_DSN)

    async def move(conn, *, reference_type: str, age_days: int) -> uuid.UUID:  # type: ignore[no-untyped-def]
        move_id = uuid.uuid4()
        await conn.execute(
            text(
                "INSERT INTO operations.inventory_moves "
                "(id, organization_id, kind, reference_type, note, occurred_on, created_at) "
                "VALUES (:i, :o, 'relocation', :r, '', current_date, "
                "now() - make_interval(days => :d))"
            ),
            {"i": move_id, "o": org, "r": reference_type, "d": age_days},
        )
        return move_id

    async def count(conn) -> int:  # type: ignore[no-untyped-def]
        return int((await conn.execute(text(query))).scalar_one())

    try:
        async with engine.connect() as conn:
            # Foreign keys are not what is under test; every row below is rolled back.
            await conn.execute(text("SET session_replication_role = replica"))
            base = await count(conn)

            await move(conn, reference_type="putaway", age_days=1)
            assert await count(conn) == base + 1, "a recent put-away with no capture must count"

            kept = await move(conn, reference_type="putaway", age_days=1)
            await conn.execute(
                text(
                    "INSERT INTO operations.putaway_captures "
                    "(id, organization_id, move_id, location_id, created_by, captured_at) "
                    "VALUES (gen_random_uuid(), :o, :m, gen_random_uuid(), "
                    "gen_random_uuid(), now())"
                ),
                {"o": org, "m": kept},
            )
            assert await count(conn) == base + 1, "a put-away with its capture must not count"

            # Purged captures are legitimately missing: older than the window, not counted ...
            await move(conn, reference_type="putaway", age_days=40)
            # ... and a bin transfer never has a capture.
            await move(conn, reference_type="bin_transfer", age_days=1)
            assert await count(conn) == base + 1

            await conn.rollback()  # nothing this test wrote is ever committed
    finally:
        await engine.dispose()
