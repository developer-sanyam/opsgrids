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


# --------------------------------------------------------------------- DNS record set


def test_dns_records_cover_the_full_set() -> None:
    records = domain.dns_records_for("company.com", "eng1", "track.company.com")
    kinds = {r["kind"] for r in records}
    assert kinds == {"spf", "dkim", "dmarc", "return_path", "tracking"}


def test_dkim_record_is_scoped_to_its_selector() -> None:
    records = domain.dns_records_for("company.com", "eng1", "track.company.com")
    dkim = next(r for r in records if r["kind"] == "dkim")
    assert dkim["host"] == "eng1._domainkey.company.com"


def test_dkim_value_is_left_for_the_generated_key() -> None:
    """`dns_records_for` is pure, so it cannot mint a keypair. The caller that creates the domain
    fills this in; a record set that shipped with a plausible-looking key would never verify."""
    records = domain.dns_records_for("company.com", "eng1", "track.company.com")
    dkim = next(r for r in records if r["kind"] == "dkim")
    assert dkim["value"] == ""


def test_a_fresh_record_set_claims_nothing_is_verified() -> None:
    records = domain.dns_records_for("company.com", "eng1", "track.company.com")
    assert not any(r["verified"] for r in records)


def test_dmarc_is_recommended_rather_than_required() -> None:
    records = domain.dns_records_for("company.com", "eng1", "track.company.com")
    by_kind = {r["kind"]: r for r in records}
    assert by_kind["dmarc"]["required"] is False
    assert all(by_kind[k]["required"] is True for k in ("spf", "dkim", "return_path", "tracking"))


# --------------------------------------------------------------------- status folding


def test_domain_is_verified_only_when_every_required_record_passes() -> None:
    records = [
        {"kind": "spf", "required": True, "verified": True},
        {"kind": "dkim", "required": True, "verified": True},
        {"kind": "return_path", "required": True, "verified": True},
        {"kind": "tracking", "required": True, "verified": True},
        # DMARC is recommended, not required — its absence must not block sending.
        {"kind": "dmarc", "required": False, "verified": False},
    ]
    assert domain.fold_domain_status(records) == "verified"


def test_one_failed_required_record_keeps_the_domain_pending() -> None:
    records = [
        {"kind": "spf", "required": True, "verified": True},
        {"kind": "dkim", "required": True, "verified": False},
    ]
    assert domain.fold_domain_status(records) == "pending"


def test_an_empty_record_set_is_not_vacuously_verified() -> None:
    """`all()` over nothing is True. A domain whose records have not been generated yet must not
    inherit that — it has proven nothing."""
    assert domain.fold_domain_status([]) == "pending"


def test_a_freshly_generated_record_set_folds_to_pending() -> None:
    records = domain.dns_records_for("company.com", "eng1", "track.company.com")
    assert domain.fold_domain_status(records) == "pending"
