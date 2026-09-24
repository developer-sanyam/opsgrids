"""Engage's sending-identity rules, tested without a database.

The setup wizard asks an organisation *how* it would like to send email, never for a port or a
TLS mode. Everything under that choice is derived here, which is what makes it checkable: a
provider moving its submission host, or publishing a different daily cap, is one table in one
pure module rather than a value copied into a form, a migration and a dispatcher.
"""

from __future__ import annotations

import pytest

from app import domain

# --------------------------------------------------------------------- transport derivation


def test_google_kind_derives_workspace_submission_settings() -> None:
    t = domain.transport_for("google", "info@company.com")
    assert (t.host, t.port, t.use_tls) == ("smtp.gmail.com", 587, True)


def test_google_daily_cap_reflects_a_mailbox_not_an_mta() -> None:
    """Google Workspace caps external recipients near 2,000/day. The cap is not a guess we make
    at send time — it is recorded here so a campaign can be refused before it burns a mailbox."""
    assert domain.transport_for("google", "a@b.com").daily_cap == 2000


def test_microsoft_kind_derives_its_own_submission_settings() -> None:
    t = domain.transport_for("microsoft", "a@b.com")
    assert (t.host, t.port, t.use_tls) == ("smtp.office365.com", 587, True)


def test_unknown_kind_is_rejected_rather_than_defaulted() -> None:
    with pytest.raises(ValueError):
        domain.transport_for("carrier-pigeon", "a@b.com")


def test_custom_smtp_leaves_the_host_for_the_customer_to_supply() -> None:
    """The one kind whose endpoint we cannot know. The shape is still derived — port and TLS are
    ours to insist on — so the wizard asks for a host and nothing else."""
    t = domain.transport_for("smtp", "a@b.com")
    assert t.host == ""
    assert (t.port, t.use_tls) == (587, True)


# --------------------------------------------------------------------- expected record set


def test_no_connection_means_nothing_to_publish() -> None:
    """A domain added before any provider is connected has nothing to check against yet — the
    "connect your email provider first" state, not an empty SPF record for nobody."""
    assert (
        domain.expected_records("company.com", connection_kind=None, dkim_selector="google") == []
    )


def test_google_requires_spf_and_one_dkim_txt_record() -> None:
    records = domain.expected_records(
        "company.com", connection_kind="google", dkim_selector="google"
    )
    kinds = [r["kind"] for r in records]
    assert kinds == ["spf", "dkim", "dmarc"]
    dkim = next(r for r in records if r["kind"] == "dkim")
    assert dkim["type"] == "TXT"
    assert dkim["fqdn"] == "google._domainkey.company.com"
    assert dkim["registrar_name"] == "google._domainkey"


def test_google_spf_value_includes_googles_own_host() -> None:
    records = domain.expected_records(
        "company.com", connection_kind="google", dkim_selector="google"
    )
    spf = next(r for r in records if r["kind"] == "spf")
    assert spf["fqdn"] == "company.com"
    assert spf["registrar_name"] == "@"
    assert "include:_spf.google.com" in spf["value"]
    assert spf["suggested_value"] == spf["value"]


def test_microsoft_requires_spf_and_two_fixed_dkim_cnames() -> None:
    records = domain.expected_records(
        "company.com", connection_kind="microsoft", dkim_selector="google"
    )
    dkim = [r for r in records if r["kind"] == "dkim"]
    assert {r["fqdn"] for r in dkim} == {
        "selector1._domainkey.company.com",
        "selector2._domainkey.company.com",
    }
    assert all(r["type"] == "CNAME" for r in dkim)
    spf = next(r for r in records if r["kind"] == "spf")
    assert "include:spf.protection.outlook.com" in spf["value"]


def test_smtp_with_no_include_yet_has_an_empty_spf_value() -> None:
    """Nothing to verify or publish until the customer names their provider's SPF host."""
    records = domain.expected_records(
        "company.com", connection_kind="smtp", dkim_selector="", spf_include=None
    )
    spf = next(r for r in records if r["kind"] == "spf")
    assert spf["value"] == ""
    assert spf["suggested_value"] is None


def test_smtp_with_an_include_and_selector_names_both() -> None:
    records = domain.expected_records(
        "company.com",
        connection_kind="smtp",
        dkim_selector="myselector",
        spf_include="amazonses.com",
    )
    spf = next(r for r in records if r["kind"] == "spf")
    dkim = next(r for r in records if r["kind"] == "dkim")
    assert "include:amazonses.com" in spf["value"]
    assert dkim["type"] == "TXT"
    assert dkim["fqdn"] == "myselector._domainkey.company.com"


def test_dmarc_is_recommended_rather_than_required() -> None:
    records = domain.expected_records(
        "company.com", connection_kind="google", dkim_selector="google"
    )
    by_kind = {r["kind"]: r for r in records}
    assert by_kind["dmarc"]["required"] is False
    assert by_kind["spf"]["required"] is True
    assert by_kind["dkim"]["required"] is True


def test_no_return_path_or_tracking_record_is_ever_generated() -> None:
    """OpsGrids publishes no mail infrastructure — no bounce envelope, no click tracking — so
    neither record exists in this vocabulary any more."""
    for kind in ("google", "microsoft", "smtp"):
        records = domain.expected_records(
            "company.com", connection_kind=kind, dkim_selector="sel", spf_include="amazonses.com"
        )
        assert {r["kind"] for r in records} <= {"spf", "dkim", "dmarc"}


# --------------------------------------------------------------------- status folding


def test_domain_is_verified_only_when_every_required_record_passes() -> None:
    records = [
        {"kind": "spf", "required": True, "status": "verified"},
        {"kind": "dkim", "required": True, "status": "verified"},
        # DMARC is recommended, not required — its absence must not block sending.
        {"kind": "dmarc", "required": False, "status": "missing"},
    ]
    assert domain.fold_domain_status(records) == "verified"


def test_one_failed_required_record_keeps_the_domain_pending() -> None:
    records = [
        {"kind": "spf", "required": True, "status": "verified"},
        {"kind": "dkim", "required": True, "status": "missing"},
    ]
    assert domain.fold_domain_status(records) == "pending"


def test_an_empty_record_set_is_not_vacuously_verified() -> None:
    """`all()` over nothing is True. A domain with no connection yet, or whose records have not
    been checked yet, must not inherit that — it has proven nothing."""
    assert domain.fold_domain_status([]) == "pending"


# --------------------------------------------------------------------- SPF evaluation


def test_no_spf_record_at_all_is_missing_with_a_suggestion() -> None:
    result = domain.evaluate_spf([], required_include="_spf.google.com")
    assert result.status == "missing"
    assert result.suggested_value == "v=spf1 include:_spf.google.com ~all"


def test_one_correct_spf_record_verifies() -> None:
    result = domain.evaluate_spf(
        ["v=spf1 include:_spf.google.com ~all"], required_include="_spf.google.com"
    )
    assert result.status == "verified"


def test_the_include_must_be_a_whole_mechanism_token() -> None:
    """`include:_spf.google.com.evil.com` must not read as containing
    `include:_spf.google.com` — a substring match here would let a lookalike include pass."""
    result = domain.evaluate_spf(
        ["v=spf1 include:_spf.google.com.evil.com ~all"], required_include="_spf.google.com"
    )
    assert result.status == "mismatch"


def test_a_single_wrong_spf_record_is_edited_not_replaced() -> None:
    result = domain.evaluate_spf(["v=spf1 mx ~all"], required_include="_spf.google.com")
    assert result.status == "mismatch"
    assert result.message.startswith("Edit your existing SPF record")
    assert result.suggested_value == "v=spf1 mx include:_spf.google.com ~all"


def test_multiple_spf_records_are_refused_and_merged() -> None:
    result = domain.evaluate_spf(
        ["v=spf1 mx ~all", "v=spf1 include:_spf.google.com ~all"],
        required_include="_spf.google.com",
    )
    assert result.status == "multiple"
    assert "2 SPF records" in result.message
    assert "only one" in result.message
    # Both records' mechanisms survive, deduplicated, with a single trailing `~all`.
    assert result.suggested_value.count("~all") == 1
    assert "mx" in result.suggested_value
    assert "include:_spf.google.com" in result.suggested_value


def test_suggested_value_is_always_present() -> None:
    """Every branch returns something to publish or edit — there is no state where the customer
    is told a record is wrong with nothing to fix it with."""
    missing = domain.evaluate_spf([], required_include="_spf.google.com")
    mismatch = domain.evaluate_spf(["v=spf1 ~all"], required_include="_spf.google.com")
    multiple = domain.evaluate_spf(
        ["v=spf1 ~all", "v=spf1 a ~all"], required_include="_spf.google.com"
    )
    verified = domain.evaluate_spf(
        ["v=spf1 include:_spf.google.com ~all"], required_include="_spf.google.com"
    )
    for result in (missing, mismatch, multiple, verified):
        assert result.suggested_value


def test_a_record_with_too_many_lookups_warns_but_still_verifies() -> None:
    includes = " ".join(f"include:p{i}.example.com" for i in range(11))
    record = f"v=spf1 {includes} include:_spf.google.com ~all"
    result = domain.evaluate_spf([record], required_include="_spf.google.com")
    assert result.status == "verified"
    assert "DNS lookups" in result.message


# --------------------------------------------------------------------- DKIM evaluation


def test_google_dkim_verifies_on_a_v_dkim1_txt() -> None:
    assert domain.evaluate_dkim_txt(["v=DKIM1; k=rsa; p=AAA"]) == "verified"


def test_google_dkim_is_a_mismatch_when_something_else_is_published() -> None:
    assert domain.evaluate_dkim_txt(["not a dkim record"]) == "mismatch"


def test_google_dkim_is_missing_when_nothing_is_published() -> None:
    assert domain.evaluate_dkim_txt([]) == "missing"


def test_smtp_dkim_verifies_on_either_a_txt_or_a_cname() -> None:
    assert domain.evaluate_dkim_smtp(["v=DKIM1; k=rsa; p=AAA"], []) == "verified"
    assert domain.evaluate_dkim_smtp([], ["dkim.provider.example."]) == "verified"


def test_smtp_dkim_is_missing_with_neither() -> None:
    assert domain.evaluate_dkim_smtp([], []) == "missing"


def test_smtp_dkim_is_a_mismatch_when_a_txt_is_published_but_wrong() -> None:
    assert domain.evaluate_dkim_smtp(["not dkim"], []) == "mismatch"


# --------------------------------------------------------------------- DMARC evaluation


def test_dmarc_verifies_on_any_dmarc_record() -> None:
    assert domain.evaluate_dmarc(["v=DMARC1; p=none"]) is True


def test_dmarc_is_false_with_nothing_published() -> None:
    assert domain.evaluate_dmarc([]) is False


def test_dmarc_never_appears_in_foldings_required_list() -> None:
    """Recommended, not required — asserted here at the boundary that actually enforces it."""
    records = domain.expected_records(
        "company.com", connection_kind="google", dkim_selector="google"
    )
    dmarc = next(r for r in records if r["kind"] == "dmarc")
    assert dmarc["required"] is False
