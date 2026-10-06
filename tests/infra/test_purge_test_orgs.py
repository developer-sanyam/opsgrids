"""`tools/purge_test_orgs.py` — the owner-run removal of dummy and test organisations (ADR-0036).

This file is everything that needs no database, like the rest of `tests/infra`:

* **The refusals.** `--apply` is the only way a row is ever deleted, and it needs a typed count, a
  backup attestation, and (on a live database) a flag of its own. Every refusal is proved to happen
  *before the tool connects*, by handing `main` an engine factory that fails if it is called.
* **Nothing is selected for you.** The option set is pinned, so a "delete everything that looks
  like test data" switch cannot be added without a test noticing.
* **The foreign-key ordering and the row selectors**, on synthetic catalogs: children before
  parents, a cycle broken only where an action does the work, a child table with no
  `organization_id` found through its parent, a `SET NULL` referrer left alone.
* **Counts only**, and the narrowness promises, read off the tool's own SQL: one `DELETE`, no
  `TRUNCATE`, no `session_replication_role`, no `DISABLE TRIGGER ALL`/`USER`, one role.
* **The documents** (RUNBOOK section, ADR-0036, decisions index) name the flags the tool has.

The real-Postgres half is `test_purge_test_orgs_db.py` (opt-in). Both load the tool as a script.
"""

from __future__ import annotations

import ast
import json
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from _purge_orgs_world import REPO_ROOT, TOOL, purge

Table = purge.Table
Fk = purge.Fk
Cond = purge.Cond

ORG_A = uuid.UUID("aaaaaaaa-0000-0000-0000-00000000000a")
ORG_B = uuid.UUID("bbbbbbbb-0000-0000-0000-00000000000b")
LOCAL_DSN = "postgresql+asyncpg://opsgrids_admin:pw@localhost:5434/pto"  # pragma: allowlist secret
PROD_DSN = (
    "postgresql+asyncpg://opsgrids_admin:pw@postgres:5432/opsgrids"  # pragma: allowlist secret
)
LOCAL = SimpleNamespace(environment="local", admin_dsn=LOCAL_DSN)
PROD = SimpleNamespace(environment="production", admin_dsn=PROD_DSN)


class Factory:
    """An engine factory that records every time the tool reaches for a database, and fails."""

    def __init__(self) -> None:
        self.dsns: list[str] = []

    def __call__(self, dsn: str) -> Any:
        self.dsns.append(dsn)
        raise ConnectionError("no database in this test")


def _main(argv: list[str], cfg: Any = LOCAL) -> tuple[int, Factory]:
    factory = Factory()
    code = int(purge.main(argv, cfg=cfg, engine_factory=factory, out=lambda _line: None))
    return code, factory


def _refused(argv: list[str], cfg: Any = LOCAL) -> None:
    code, factory = _main(argv, cfg)
    assert code == 2, f"expected a refusal, got exit {code}"
    assert factory.dsns == [], "the tool connected before it refused"


# ------------------------------------------------------------------------------- the refusals
APPLY = ["--apply", "--confirm-count", "1", "--i-took-a-backup"]


def test_apply_needs_an_organisation_named(capsys: pytest.CaptureFixture[str]) -> None:
    _refused(["--apply", "--i-took-a-backup", "--confirm-count", "0"])
    assert "--apply needs at least one" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("argv", "message"),
    [
        (["--org-id", str(ORG_A), "--apply", "--i-took-a-backup"], "--confirm-count N is required"),
        (
            ["--org-id", str(ORG_A), "--apply", "--i-took-a-backup", "--confirm-count", "2"],
            "does not match the 1 organisation",
        ),
        (
            [
                "--org-id",
                str(ORG_A),
                "--org-id",
                str(ORG_B),
                "--apply",
                "--i-took-a-backup",
                "--confirm-count",
                "1",
            ],
            "does not match the 2 organisation",
        ),
        (["--org-id", str(ORG_A), "--apply", "--confirm-count", "1"], "--i-took-a-backup"),
        (["--org-id", str(uuid.UUID(int=0)), *APPLY], "platform's own sentinel organisation"),
        (["--org-id", str(ORG_A), "--keep", str(ORG_A), *APPLY], "listed in --keep/--keep-file"),
    ],
)
def test_every_apply_refusal_happens_before_the_tool_connects(
    argv: list[str], message: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _refused(argv)
    assert message in capsys.readouterr().err


def test_a_repeated_id_is_one_organisation_and_gets_through_to_the_database() -> None:
    """Not a refusal: the gates pass and the tool goes on to connect (and here, fails to)."""
    code, factory = _main(["--org-id", str(ORG_A), "--org-id", str(ORG_A), *APPLY])
    assert code == 1 and len(factory.dsns) == 1


def test_a_dry_run_is_not_refused_for_want_of_the_apply_flags() -> None:
    """Listing and the dry run read only; they need no count, no backup and no live flag."""
    for argv in (["--org-id", str(ORG_A)], [], ["--explain"], ["--json"], ["--candidates"]):
        code, factory = _main(argv, PROD)
        assert code == 1 and len(factory.dsns) == 1, argv


def test_the_dry_run_still_refuses_what_needs_no_flag(capsys: pytest.CaptureFixture[str]) -> None:
    """A mistake in the selection shows up on the dry run, not first on `--apply`."""
    _refused(["--org-id", str(uuid.UUID(int=0))])
    _refused(["--org-id", str(ORG_A), "--keep", str(ORG_A)])
    assert "refused" in capsys.readouterr().err


def test_a_live_database_needs_its_own_flag_to_apply(capsys: pytest.CaptureFixture[str]) -> None:
    argv = ["--org-id", str(ORG_A), *APPLY]
    _refused(argv, PROD)
    assert "--i-know-this-is-the-live-database is required" in capsys.readouterr().err
    code, factory = _main([*argv, "--i-know-this-is-the-live-database"], PROD)
    assert code == 1 and len(factory.dsns) == 1, "with the flag the tool goes on to connect"


@pytest.mark.parametrize(
    ("environment", "host", "needs_flag"),
    [
        ("production", "localhost", True),
        ("staging", "localhost", True),
        ("uat", None, True),
        ("local", "localhost", False),
        ("local", "127.0.0.1", False),
        ("test", "::1", False),
        ("ci", None, False),
        # A laptop with ENVIRONMENT=local and a DATABASE_URL copied from the box.
        ("local", "postgres", True),
        ("local", "db.example.internal", True),
    ],
)
def test_the_live_rule(environment: str, host: str | None, needs_flag: bool) -> None:
    assert purge.requires_live_flag(environment, host) is needs_flag


def test_the_live_environments_are_the_ones_settings_already_treats_as_real() -> None:
    """The set `ServiceSettings.model_post_init` refuses development secrets in."""
    source = (
        REPO_ROOT / "libs" / "platform-core" / "src" / "platform_core" / "config.py"
    ).read_text()
    assert 'self.environment not in ("production", "staging", "uat")' in source
    assert set(purge.LIVE_ENVIRONMENTS) == {"production", "staging", "uat"}


def test_a_remote_host_needs_the_flag_even_when_the_environment_says_local(
    capsys: pytest.CaptureFixture[str],
) -> None:
    remote = SimpleNamespace(
        environment="local", admin_dsn="postgresql+asyncpg://u:p@db.example.internal:5432/x"
    )
    _refused(["--org-id", str(ORG_A), *APPLY], remote)
    assert "live database" in capsys.readouterr().err


def test_the_ids_files_are_read_before_anything_connects(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "ids.txt"
    bad.write_text(f"{ORG_A}\nhunter2-not-a-uuid\n")
    _refused(["--ids-file", str(bad), *APPLY])
    err = capsys.readouterr().err
    assert "line 2" in err and "hunter2" not in err, "the refusal names the line, never its content"
    _refused(["--ids-file", str(tmp_path / "missing.txt"), *APPLY])

    keep = tmp_path / "keep.txt"
    keep.write_text(f"# mine\n{ORG_A}  # the owner's own\n")
    _refused(["--org-id", str(ORG_A), "--keep-file", str(keep), *APPLY])
    assert "listed in --keep/--keep-file" in capsys.readouterr().err


def test_an_organization_id_that_is_not_a_uuid_is_refused_by_the_parser(
    capsys: pytest.CaptureFixture[str],
) -> None:
    factory = Factory()
    with pytest.raises(SystemExit) as exit_info:
        purge.main(["--org-id", "e2e-0920a"], cfg=LOCAL, engine_factory=factory)
    assert exit_info.value.code == 2 and factory.dsns == []
    assert "invalid" in capsys.readouterr().err


# ---------------------------------------------------------------------- nothing is selected for you
def test_the_option_set_is_pinned_so_nothing_can_select_for_you() -> None:
    options = {s for a in purge.build_parser()._actions for s in a.option_strings}
    assert options == {
        "-h",
        "--help",
        "--org-id",
        "--ids-file",
        "--keep",
        "--keep-file",
        "--keep-user",
        "--keep-orphan-users",
        "--json",
        "--candidates",
        "--explain",
        "--apply",
        "--confirm-count",
        "--i-took-a-backup",
        "--i-know-this-is-the-live-database",
    }
    # No spelling of "all", "matching", "hinted" or "yes" anywhere.
    for option in options:
        assert not re.search(r"all|match|pattern|auto|hint|yes|force|like|every", option), option


def test_the_defaults_delete_nothing() -> None:
    args = purge.build_parser().parse_args([])
    assert args.apply is False and args.org_id == [] and args.ids_file is None
    assert args.confirm_count is None
    assert args.i_took_a_backup is False and args.i_know_this_is_the_live_database is False


def test_a_dry_run_cannot_be_asked_to_delete(capsys: pytest.CaptureFixture[str]) -> None:
    """The write path is `run(apply=True)`; `main` only reaches it with the flag."""
    source = TOOL.read_text()
    assert source.count("apply=args.apply") == 1
    assert "apply=True" not in source.split("def main(", 1)[1]


# ------------------------------------------------------------------------- parsing and selection
def test_ids_files_allow_comments_and_blank_lines() -> None:
    text_ = f"# header\n\n{ORG_A}  # the first\n   {ORG_B}\n"
    assert purge.parse_ids(text_, source="f") == [ORG_A, ORG_B]


def test_selection_is_sorted_and_deduplicated() -> None:
    assert purge.distinct([ORG_B, ORG_A, ORG_B]) == [ORG_A, ORG_B]


def test_the_selection_rules_report_every_problem_not_just_the_first() -> None:
    nil = uuid.UUID(int=0)
    problems = purge.check_selection([nil, ORG_A], [ORG_A], confirm_count=5)
    assert len(problems) == 3
    assert purge.check_selection([ORG_A], [ORG_B], confirm_count=1) == []


# ------------------------------------------------------------------------------------ hints
@pytest.mark.parametrize(
    ("name", "slug", "likely"),
    [
        ("e2e-0920a", "e2e-0920a", True),
        ("E2E signup 1234", "e2e-signup-1234", True),
        ("buyer-4f2a", "buyer-4f2a", True),
        ("Smoke crm", "smoke-crm", True),
        ("Acme Demo", "acme-demo", True),
        ("Test Organisation", "test-organisation", True),
        ("QA team", "qa-team", True),
        ("Sandbox", "x", True),
        ("Latest Textiles", "latest-textiles", False),
        ("Contest Winners Ltd", "contest-winners", False),
        ("Attestation Services", "attestation", False),
        ("Demonstrators of Delhi", "dd", False),
        ("Buyers Union", "buyers-union", True),  # a hint, not a rule: it is for a person to read
        ("Quality Assurance", "quality-assurance", False),
        ("Sharma Textiles Pvt Ltd", "sharma-textiles", False),
    ],
)
def test_name_and_slug_hints_are_token_wise(name: str, slug: str, likely: bool) -> None:
    assert bool(purge.name_hints(name, slug)) is likely


def test_throwaway_domains_are_recognised_without_reading_an_address_into_python() -> None:
    regex = re.compile(purge.THROWAWAY_EMAIL_RE)
    for address in (
        "a@example.com",
        "a@mail.example.org",
        "x@mailinator.com",
        "x@sub.test",
        "x@foo.invalid",
        "x@yopmail.com",
        "x@something.localhost",
    ):
        assert regex.search(address), address
    for address in (
        "owner@acme-textiles.in",
        "a@gmail.com",
        "a@examples.com",
        "a@notmailinator.io",
    ):
        assert not regex.search(address), address
    domains = re.compile(purge.TEST_DOMAIN_RE)
    assert domains.search("shop.example.com") and domains.search("x.test")
    assert not domains.search("acme-textiles.in")


def _row(**kw: Any) -> Any:
    base: dict[str, Any] = {
        "id": ORG_A,
        "name": "Latest Textiles",
        "slug": "latest-textiles",
        "status": "active",
        "created_at": datetime(2026, 9, 1, tzinfo=UTC),
    }
    return purge.OrgRow(**{**base, **kw})


def test_a_real_looking_organisation_is_not_hinted_unless_it_is_idle() -> None:
    assert _row(subscriptions={"active": 1}).hint == ""
    assert _row(captured_charges=2).hint == ""
    assert _row().hint == "idle"
    assert _row(subscriptions={"trial": 1}).hint == "idle"
    assert _row(name="e2e-1", slug="e2e-1", subscriptions={"active": 1}).hint == "LIKELY TEST"


def test_the_throwaway_member_reason_needs_every_member_to_be_one() -> None:
    assert not _row(members=0, throwaway_members=0).reasons
    assert _row(members=2, throwaway_members=2).reasons
    assert not _row(members=2, throwaway_members=1).reasons


def test_the_listing_prints_names_and_says_nothing_is_selected() -> None:
    rows = [_row(), _row(id=ORG_B, name="e2e-0920a", slug="e2e-0920a", kept=True)]
    listing = purge.render_listing(rows, "box:5432/opsgrids", "production")
    assert "Latest Textiles" in listing and "e2e-0920a" in listing and "KEEP" in listing
    assert "database: box:5432/opsgrids" in listing and "production" in listing
    assert "nothing is selected for you" in listing


def test_candidates_are_an_ids_file_to_review_not_a_selection(tmp_path: Path) -> None:
    rows = [
        _row(id=ORG_A, name="e2e-1", slug="e2e-1"),
        _row(id=ORG_B, name="e2e-2", slug="e2e-2", kept=True),
        _row(id=uuid.uuid4()),
    ]
    out = purge.render_candidates(rows)
    assert str(ORG_A) in out and str(ORG_B) not in out and "Latest Textiles" not in out
    assert "REVIEW EVERY LINE" in out
    # It round-trips as an ids file, comments and all.
    assert purge.parse_ids(out, source="candidates") == [ORG_A]


# ---------------------------------------------------------------------------- the foreign-key graph
def _fk(child: str, parent: str, action: str = "a", cols: tuple[str, str] = ("p_id", "id")) -> Any:
    return Fk(Table("s", child), Table("s", parent), (cols[0],), (cols[1],), action)


def test_children_are_deleted_before_their_parents() -> None:
    tables = [Table("s", n) for n in ("orders", "lines", "notes", "attachments")]
    fks = [_fk("lines", "orders"), _fk("notes", "orders", "c"), _fk("attachments", "notes", "r")]
    order = [t.name for t in purge.delete_order(tables, fks)]
    assert order.index("lines") < order.index("orders")
    assert order.index("attachments") < order.index("notes") < order.index("orders")


def test_the_order_is_stable_and_complete() -> None:
    tables = [Table("s", n) for n in "dcba"]
    fks = [_fk("a", "b"), _fk("c", "d")]
    first = purge.delete_order(tables, fks)
    assert first == purge.delete_order(list(reversed(tables)), list(reversed(fks)))
    assert sorted(t.name for t in first) == ["a", "b", "c", "d"]


def test_a_self_reference_is_not_an_edge() -> None:
    node = Table("s", "locations")
    assert purge.delete_order([node], [Fk(node, node, ("parent_id",), ("id",), "r")]) == [node]


def test_a_cycle_is_loosened_only_where_an_action_does_the_work() -> None:
    tables = [Table("s", "a"), Table("s", "b")]
    soft = [_fk("a", "b", "r"), _fk("b", "a", "c")]
    order = [t.name for t in purge.delete_order(tables, soft)]
    assert order == ["a", "b"], (
        "the restrict edge is kept (a before b); the cascade edge is dropped"
    )
    hard = [_fk("a", "b", "r"), _fk("b", "a", "a")]
    with pytest.raises(
        purge.SchemaDrift, match=re.escape("cycle with no safe delete order among: s.a, s.b")
    ):
        purge.delete_order(tables, hard)


def test_a_set_null_edge_is_an_ordering_preference_not_a_blocker() -> None:
    tables = [Table("s", "a"), Table("s", "b")]
    order = purge.delete_order(tables, [_fk("a", "b", "n"), _fk("b", "a", "n")])
    assert sorted(t.name for t in order) == ["a", "b"]


# ------------------------------------------------------------------------------ the row selectors
def _nodes() -> dict[Any, Any]:
    org_tables = [Table("s", "orders"), Table("s", "lines"), Table("auth", "refresh_tokens")]
    fks = [
        _fk("lines", "orders", "r"),
        Fk(Table("s", "notes"), Table("s", "orders"), ("order_id",), ("id",), "c"),
        Fk(Table("s", "attachments"), Table("s", "notes"), ("note_id",), ("id",), "r"),
        Fk(Table("s", "audit"), Table("s", "orders"), ("order_id",), ("id",), "n"),
        Fk(
            Table("s", "composite"),
            Table("s", "orders"),
            ("order_id", "org"),
            ("id", "organization_id"),
            "c",
        ),
        Fk(
            Table("auth", "refresh_tokens"),
            Table("auth", "sessions"),
            ("session_id",),
            ("id",),
            "c",
        ),
        Fk(Table("auth", "mfa_extra"), Table("account", "users"), ("who",), ("id",), "c"),
    ]
    nodes: dict[Any, Any] = purge.build_nodes(org_tables, fks, ["credentials", "sessions"])
    return nodes


def test_a_table_with_an_organization_id_is_selected_by_it_and_only_it() -> None:
    nodes = _nodes()
    assert nodes[Table("s", "orders")].predicate(nodes) == '(t."organization_id" = :org)'
    assert nodes[Table("s", "lines")].predicate(nodes) == '(t."organization_id" = :org)'


def test_a_child_without_an_organization_id_is_found_through_its_parent() -> None:
    nodes = _nodes()
    assert nodes[Table("s", "notes")].predicate(nodes) == (
        '(EXISTS (SELECT 1 FROM "s"."orders" p1 WHERE t."order_id" = p1."id" '
        'AND ((p1."organization_id" = :org))))'
    )
    # Two levels down, each level aliased apart: p1, then p2.
    assert nodes[Table("s", "attachments")].predicate(nodes) == (
        '(EXISTS (SELECT 1 FROM "s"."notes" p1 WHERE t."note_id" = p1."id" AND '
        '((EXISTS (SELECT 1 FROM "s"."orders" p2 WHERE p1."order_id" = p2."id" '
        'AND ((p2."organization_id" = :org)))))))'
    )


def test_a_composite_key_joins_on_every_column() -> None:
    nodes = _nodes()
    sql = nodes[Table("s", "composite")].predicate(nodes)
    assert 't."order_id" = p1."id" AND t."org" = p1."organization_id"' in sql


def test_a_table_that_is_only_nulled_by_the_delete_is_not_deleted_from() -> None:
    assert Table("s", "audit") not in _nodes(), "ON DELETE SET NULL rows are not the organisation's"


def test_global_tables_are_never_in_the_plan() -> None:
    nodes = _nodes()
    assert {t.schema for t in nodes} == {"s", "account", "auth"}
    assert Table("s", "catalog") not in nodes


def test_the_identity_selectors() -> None:
    nodes = _nodes()
    assert nodes[Table("account", "organizations")].predicate(nodes) == '(t."id" = :org)'
    assert nodes[Table("account", "users")].predicate(nodes) == (
        '(t."id" = ANY(CAST(:orphans AS uuid[])))'
    )
    assert nodes[Table("auth", "credentials")].predicate(nodes) == (
        '(t."user_id" = ANY(CAST(:orphans AS uuid[])))'
    )
    sessions = nodes[Table("auth", "sessions")].predicate(nodes)
    assert '"active_org_id" = :org' in sessions
    assert "ANY(CAST(:orphans AS uuid[]))" in sessions, "an orphan's sessions go with the user"


def test_a_table_with_an_organization_id_also_takes_its_sessions_children() -> None:
    """`auth.refresh_tokens.organization_id` is nullable and a token belongs to a session: both are
    ways a token is the organisation's, and a session's own rows are identity rows."""
    nodes = _nodes()
    sql = nodes[Table("auth", "refresh_tokens")].predicate(nodes)
    assert 't."organization_id" = :org' in sql
    assert 'FROM "auth"."sessions" p1 WHERE t."session_id" = p1."id"' in sql


def test_identity_children_of_a_user_are_found_without_being_named() -> None:
    nodes = _nodes()
    assert nodes[Table("auth", "mfa_extra")].identity
    assert 'FROM "account"."users" p1 WHERE t."who" = p1."id"' in nodes[
        Table("auth", "mfa_extra")
    ].predicate(nodes)


def test_nesting_has_a_ceiling() -> None:
    a, b = Table("s", "a"), Table("s", "b")
    nodes = {a: purge.Node(a), b: purge.Node(b)}
    nodes[a].add(Cond("parent", parent=b, pairs=(("b_id", "id"),)))
    nodes[b].add(Cond("parent", parent=a, pairs=(("a_id", "id"),)))
    with pytest.raises(purge.SchemaDrift, match="nest deeper"):
        nodes[a].predicate(nodes)


def test_identifiers_are_checked_before_they_reach_sql() -> None:
    with pytest.raises(ValueError, match="not a plain identifier"):
        Table("s", 'x"; DROP TABLE y; --')
    with pytest.raises(ValueError, match="not a plain identifier"):
        Fk(Table("s", "a"), Table("s", "b"), ('c"',), ("id",), "a")


# ------------------------------------------------------------------------------ counts only
def _code_strings(source: str) -> list[str]:
    """Every string in the code that is not a docstring: plain literals as their value, f-strings
    as their unparsed source (so `f"ALTER TABLE {x}"` is one string, not its fragments)."""
    tree = ast.parse(source)
    skip: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                skip.add(id(body[0].value))
        if isinstance(node, ast.JoinedStr):
            skip.update(id(v) for v in ast.walk(node) if v is not node)
    found: list[str] = []
    for node in ast.walk(tree):
        if id(node) in skip:
            continue
        if isinstance(node, ast.JoinedStr):
            found.append(ast.unparse(node))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            found.append(node.value)
    return found


def test_the_tool_has_one_delete_and_one_insert_and_nothing_global() -> None:
    strings = _code_strings(TOOL.read_text())
    sql = "\n".join(strings)
    assert sum("DELETE FROM" in s for s in strings) == 1
    assert sum("INSERT INTO" in s for s in strings) == 1, "the audit row, and only that"
    for forbidden in (
        "TRUNCATE",
        "session_replication_role",
        "DISABLE TRIGGER ALL",
        "DISABLE TRIGGER USER",
        "DROP ",
        "GRANT ",
        "REVOKE ",
        "CREATE ",
        "UPDATE ",
        "ALTER ROLE",
        "ALTER SYSTEM",
        "SET ROLE",
    ):
        assert forbidden not in sql, forbidden
    # The only DDL is the per-trigger switch, by name.
    alters = [s for s in strings if "ALTER TABLE" in s]
    assert (
        len(alters) == 1 and 'ALTER TABLE {trigger.table.sql} {verb} "{trigger.name}"' in alters[0]
    )


def test_the_tool_connects_as_the_admin_role_and_nothing_else() -> None:
    source = TOOL.read_text()
    code = re.sub(r'""".*?"""', "", source, flags=re.DOTALL)
    assert "cfg.admin_dsn" in code and 'db_admin_role="opsgrids_admin"' in code
    for other in ("cfg.app_dsn", "directory_dsn", "outbox_dsn", "database_url"):
        assert other not in code, other


def test_every_write_is_one_transaction_per_organisation() -> None:
    source = TOOL.read_text()
    assert source.count("engine.begin()") == 1, "the only writing transaction is `_purge_one`'s"
    assert "pg_try_advisory_xact_lock" in source
    assert "SET LOCAL lock_timeout" in source and "SET LOCAL statement_timeout" in source
    assert "set_config('app.organization_id', :o, true)" in source, "transaction-local, not session"
    # The read phase is read-only before it does anything else.
    assert "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY" in source


def test_output_lines_carry_counts_and_ids_only() -> None:
    plan = purge.OrgPlan(
        ORG_A,
        {Table("s", "orders"): 3, Table("s", "empty"): 0},
        [uuid.uuid4()],
        purge.UserReport(members=2, kept_shared=1, deleted=1),
        {"catalog.plan_versions.restricted_to_org_id": 1},
    )
    line = purge._org_line(plan, "apply", "deleted")
    assert line["deleted"] == {"s.orders": 3}, "zero-row tables are not listed"
    assert set(line) == {
        "organization_id",
        "mode",
        "status",
        "deleted",
        "rows",
        "users",
        "left_in_place",
    }
    assert set(line["users"]) == {
        "members",
        "kept_shared_with_another_organization",
        "kept_protected",
        "kept_by_choice",
        "deleted",
    }
    assert purge._org_line(plan, "dry-run", "would-delete").keys() >= {"would_delete"}


def test_an_id_with_nothing_to_delete_says_whether_it_was_ever_purged() -> None:
    """`already-clean` (an earlier purge left its audit row) is not `not-found` (a typo)."""
    full = purge.OrgPlan(ORG_A, {Table("s", "t"): 2}, [], purge.UserReport(), {})
    assert purge.status_of(full, "apply") == "deleted"
    assert purge.status_of(full, "dry-run") == "would-delete"
    empty = purge.OrgPlan(ORG_A, {Table("s", "t"): 0}, [], purge.UserReport(), {})
    assert purge.status_of(empty, "apply") == "not-found"
    empty.previously_purged = True
    assert purge.status_of(empty, "apply") == "already-clean"


def test_an_error_line_never_carries_the_database_message() -> None:
    class Db(Exception):
        sqlstate = "23503"
        table_name = "orders"
        constraint_name = "fk_orders_customer"

    class Wrapped(Exception):
        def __init__(self) -> None:
            super().__init__('Key (email)=(owner@acme.in) is still referenced from table "x"')
            self.orig = Db('Key (email)=(owner@acme.in) is still referenced from table "x"')

    line = purge._error_line(ORG_A, Wrapped())
    assert line == {
        "organization_id": str(ORG_A),
        "status": "failed",
        "error": "Wrapped",
        "sqlstate": "23503",
        "table_name": "orders",
        "constraint_name": "fk_orders_customer",
    }
    assert "owner@acme.in" not in json.dumps(line)
    # Our own refusals name a table and a count and are safe to print.
    own = purge._error_line(ORG_A, RuntimeError("s.orders: 2 rows remain after the delete"))
    assert own["detail"] == "s.orders: 2 rows remain after the delete"


def test_the_database_label_never_carries_the_password() -> None:
    label = purge._database_label("postgresql+asyncpg://opsgrids_admin:hunter2@db:5432/opsgrids")
    assert label == "db:5432/opsgrids" and "hunter2" not in label


def test_what_a_purge_leaves_outside_the_database_is_named() -> None:
    for table in ("document.assets", "payment.mandates", "storefront.custom_domain_claims"):
        assert table in purge.EXTERNAL_LEFTOVERS
    joined = " ".join(purge.GENERAL_LEFTOVERS)
    for word in ("RabbitMQ", "Redis", "Web bundles", "Backups"):
        assert word in joined
    assert "{bucket}/{org}/" in purge.EXTERNAL_LEFTOVERS["document.assets"]


# -------------------------------------------------------------------------------- the documents
RUNBOOK = REPO_ROOT / "infrastructure" / "edge" / "RUNBOOK.md"
ADR = REPO_ROOT / "docs" / "adr" / "0036-removing-test-organisations.md"
DECISIONS = REPO_ROOT / "docs" / "decisions.md"


def _runbook_section() -> str:
    text_ = RUNBOOK.read_text()
    heading = "### Removing test organisations"
    start = text_.index(heading)
    rest = text_[start + len(heading) :]
    match = re.search(r"^#{2,3} ", rest, re.M)
    return text_[start : start + len(heading) + (match.start() if match else len(rest))]


#: Flags in the section that belong to docker, make or the shell, not to the tool.
_NOT_THE_TOOLS = {
    "--rm",
    "--no-deps",
    "--env-file",
    "--help",
    "--entrypoint",
    "--recursive",
    "--force",
}


def test_the_runbook_names_only_flags_the_tool_has() -> None:
    section = _runbook_section()
    tool_flags = {s for a in purge.build_parser()._actions for s in a.option_strings}
    cited = set(re.findall(r"--[a-z][a-z0-9-]+", section)) - _NOT_THE_TOOLS
    assert cited, "the section shows no command"
    assert cited <= tool_flags, (
        f"the RUNBOOK cites flags the tool does not have: {cited - tool_flags}"
    )
    for flag in (
        "--apply",
        "--confirm-count",
        "--i-took-a-backup",
        "--i-know-this-is-the-live-database",
        "--org-id",
        "--ids-file",
        "--keep",
    ):
        assert flag in section, flag


def test_the_runbook_section_has_the_whole_procedure() -> None:
    section = _runbook_section()
    for needle in (
        "make edge-backup",
        "purge_test_orgs.py",
        "docker compose -f compose.prod.yml --env-file .env run --rm --no-deps -T",
        "Restore",
        "owner",
        "ADR-0036",
        "MinIO",
        "RabbitMQ",
    ):
        assert needle in section, needle
    # The order of the steps is the safety: look, choose, back up, dry-run, apply, verify.
    positions = [
        section.index(x)
        for x in ("1. Look", "2. Choose", "3. Back up", "4. Dry run", "5. Apply", "6. Verify")
    ]
    assert positions == sorted(positions)


def test_the_adr_states_that_destruction_is_owner_run_only() -> None:
    raw = " ".join(ADR.read_text().split())
    assert raw.startswith("# ADR-0036")
    text_ = raw.lower()
    for needle in (
        "owner",
        "never run by ci",
        "never run by a deploy",
        "never run by an agent",
        "backup",
        "nothing is selected for you",
        "tools/purge_test_orgs.py",
    ):
        assert needle in text_, needle


def test_the_decisions_index_lists_the_adr_and_the_next_free_number_moved() -> None:
    text_ = DECISIONS.read_text()
    assert "[0036](adr/0036-removing-test-organisations.md)" in text_
    assert "next free number is **0037**" in text_
    assert not (REPO_ROOT / "docs" / "adr" / "0037-removing-test-organisations.md").exists()
