"""Incident lifecycle, asset edit/retire and the service-request JML flows — the gaps the CRUD
audit found missing: update, delete and status transitions for every entity.

Same shape as `test_authz.py`: negative assertions carry the weight. `IncidentWriteOrg` /
`RequestWriteOrg` / `AssetWriteOrg` have no "own" reading — an ITSM_USER holding `*.write` at OWN
must be refused the privileged half of every one of these endpoints (assign, priority, status,
approve, fulfil, delete), which is the audit's headline warning.
"""

from __future__ import annotations

import uuid

import pytest

from tests.helpers import as_principal, principal_for

pytestmark = pytest.mark.integration

INCIDENT_WRITE_OWN = "itsm.service.incident.write:OWN"
INCIDENT_WRITE_ORG = "itsm.service.incident.write:ORG"
INCIDENT_READ_ORG = "itsm.service.incident.read:ORG"
REQUEST_WRITE_OWN = "itsm.service.request.write:OWN"
REQUEST_WRITE_ORG = "itsm.service.request.write:ORG"
REQUEST_READ_ORG = "itsm.service.request.read:ORG"
ASSET_WRITE_OWN = "itsm.cmdb.asset.write:OWN"
ASSET_WRITE_ORG = "itsm.cmdb.asset.write:ORG"


# --------------------------------------------------------------------- incidents: PATCH


class TestUpdateIncident:
    async def test_an_own_caller_may_edit_their_own_summary(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_OWN], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4821", json={"summary": "Bengaluru WAN link flapping"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["summary"] == "Bengaluru WAN link flapping"
        assert body["worklog"][-1]["body"] == "Summary updated."
        assert body["worklog"][-1]["author"] == "System"

    async def test_an_own_caller_cannot_reprioritise_their_own_incident(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_OWN], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4821", json={"priority": "P2"}
            )
        assert response.status_code == 403

    async def test_an_own_caller_cannot_reassign_their_own_incident(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_OWN], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4821", json={"assignee": "Farah Naidu"}
            )
        assert response.status_code == 403

    async def test_an_org_caller_may_reprioritise_and_the_sla_clock_is_restamped(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4821", json={"priority": "P4", "assignee": "Zoya Khan"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["priority"] == "P4"
        assert body["assignee"] == "Zoya Khan"
        assert body["sla_minutes_remaining"] == 24 * 60
        bodies = {w["body"] for w in body["worklog"]}
        assert "Priority P1 → P4." in bodies
        assert "Assigned to Zoya Khan." in bodies

    async def test_clearing_the_affected_asset_with_null(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4821", json={"affected_asset_tag": None}
            )
        assert response.status_code == 200
        assert response.json()["affected_asset_tag"] is None

    async def test_an_unknown_asset_tag_is_404(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4821",
                json={"affected_asset_tag": "AST-DOESNOTEXIST"},
            )
        assert response.status_code == 404

    async def test_no_fields_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch("/api/v1/itsm/incidents/INC-4821", json={})
        assert response.status_code == 422

    async def test_an_unknown_field_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4821", json={"status": "closed"}
            )
        assert response.status_code == 422

    async def test_an_own_caller_is_refused_someone_elses_incident(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_OWN], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4812", json={"summary": "Hijacked"}
            )
        assert response.status_code == 403

    async def test_org_b_cannot_reach_org_as_incident_by_patch(self, client, org_id, data) -> None:
        principal = principal_for(uuid.uuid4(), [INCIDENT_WRITE_ORG], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/incidents/INC-4821", json={"summary": "Stolen"}
            )
        assert response.status_code == 404


# --------------------------------------------------------------------- incidents: status


class TestIncidentStatus:
    async def test_a_legal_move_appends_a_status_worklog(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents/INC-4821/status", json={"status": "on_hold"}
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "on_hold"
        assert body["worklog"][-1]["kind"] == "status"

    async def test_an_illegal_move_is_409(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents/INC-4821/status", json={"status": "closed"}
            )
        assert response.status_code == 409

    async def test_resolving_without_a_note_is_422(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents/INC-4821/status", json={"status": "resolved"}
            )
        assert response.status_code == 422

    async def test_resolving_with_a_note_keeps_a_breach_and_writes_a_resolution_entry(
        self, client, org_id, data
    ) -> None:
        """INC-4821 seeds `sla_state="breached"` — resolving must not launder that to "met"."""
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents/INC-4821/status",
                json={"status": "resolved", "note": "Replaced the failed transceiver."},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "resolved"
        assert body["sla_state"] == "breached"
        assert body["sla_minutes_remaining"] is None
        assert body["worklog"][-1]["kind"] == "resolution"

    async def test_reopening_a_resolved_incident(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_b"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents/INC-4812/status", json={"status": "in_progress"}
            )
        assert response.status_code == 200
        assert response.json()["status"] == "in_progress"

    async def test_closed_is_terminal(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_b"])
        with as_principal(principal):
            closed = await client.post(
                "/api/v1/itsm/incidents/INC-4812/status", json={"status": "closed"}
            )
            reopened = await client.post(
                "/api/v1/itsm/incidents/INC-4812/status", json={"status": "in_progress"}
            )
        assert closed.status_code == 200
        assert closed.json()["status"] == "closed"
        assert reopened.status_code == 409

    async def test_an_own_caller_cannot_change_status_on_their_own_incident(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_OWN], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents/INC-4821/status", json={"status": "on_hold"}
            )
        assert response.status_code == 403

    async def test_missing_the_permission_entirely_is_403(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.post(
                "/api/v1/itsm/incidents/INC-4821/status", json={"status": "on_hold"}
            )
        assert response.status_code == 403


# --------------------------------------------------------------------- incidents: delete


class TestDeleteIncident:
    async def test_an_org_caller_may_withdraw_an_incident(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_ORG], subject_ref=data["subject_b"])
        with as_principal(principal):
            deleted = await client.delete("/api/v1/itsm/incidents/INC-4812")
        assert deleted.status_code == 204
        with as_principal(
            principal_for(org_id, [INCIDENT_READ_ORG], subject_ref=data["subject_b"])
        ):
            after = await client.get("/api/v1/itsm/incidents/INC-4812")
        assert after.status_code == 404

    async def test_an_own_caller_cannot_delete_their_own_incident(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, [INCIDENT_WRITE_OWN], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete("/api/v1/itsm/incidents/INC-4821")
        assert response.status_code == 403


# --------------------------------------------------------------------- assets: PATCH / DELETE


class TestUpdateAsset:
    async def test_edited_fields_persist(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [ASSET_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch(
                "/api/v1/itsm/assets/AST-1001",
                json={"status": "in_repair", "cost_currency": "inr"},
            )
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "in_repair"
        assert body["cost_currency"] == "INR"
        assert body["tag"] == "AST-1001"

    async def test_the_tag_is_immutable(self, client, org_id, data) -> None:
        principal = principal_for(org_id, [ASSET_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.patch("/api/v1/itsm/assets/AST-1001", json={"tag": "AST-9999"})
        assert response.status_code == 422

    async def test_an_own_owner_may_edit_their_own_asset(self, client, org_id, data) -> None:
        subject = data["subject_a"]
        with as_principal(principal_for(org_id, [ASSET_WRITE_OWN], subject_ref=subject)):
            created = await client.post(
                "/api/v1/itsm/assets",
                json={"tag": "AST-2001", "name": "Loaner laptop", "type": "laptop"},
            )
            assert created.status_code == 201
            updated = await client.patch(
                "/api/v1/itsm/assets/AST-2001", json={"name": "Loaner laptop (returned)"}
            )
        assert updated.status_code == 200
        assert updated.json()["name"] == "Loaner laptop (returned)"

    async def test_org_b_cannot_reach_org_as_asset(self, client, org_id, data) -> None:
        principal = principal_for(uuid.uuid4(), [ASSET_WRITE_ORG], subject_ref=uuid.uuid4())
        with as_principal(principal):
            response = await client.patch("/api/v1/itsm/assets/AST-1001", json={"name": "Stolen"})
        assert response.status_code == 404


class TestDeleteAsset:
    async def test_refused_while_an_incident_is_open_against_it(self, client, org_id, data) -> None:
        """AST-1006 (the switch) has INC-4821 open against it in the seed."""
        principal = principal_for(org_id, [ASSET_WRITE_ORG], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete("/api/v1/itsm/assets/AST-1006")
        assert response.status_code == 409

    async def test_deleting_an_asset_with_no_open_incidents(self, client, org_id, data) -> None:
        principal = principal_for(
            org_id, [ASSET_WRITE_ORG, "itsm.cmdb.asset.read:ORG"], subject_ref=data["subject_a"]
        )
        with as_principal(principal):
            created = await client.post(
                "/api/v1/itsm/assets",
                json={"tag": "AST-3001", "name": "Spare monitor", "type": "monitor"},
            )
            assert created.status_code == 201
            deleted = await client.delete("/api/v1/itsm/assets/AST-3001")
            after = await client.get("/api/v1/itsm/assets/AST-3001")
        assert deleted.status_code == 204
        assert after.status_code == 404

    async def test_an_own_scope_owner_cannot_delete_even_their_own_asset(
        self, client, org_id, data
    ) -> None:
        principal = principal_for(org_id, [ASSET_WRITE_OWN], subject_ref=data["subject_a"])
        with as_principal(principal):
            response = await client.delete("/api/v1/itsm/assets/AST-1001")
        assert response.status_code == 403


# --------------------------------------------------------------------- service requests


class TestServiceRequestFlow:
    async def test_raise_read_approve_and_fulfil(self, client, org_id, data) -> None:
        subject = data["subject_a"]
        with as_principal(principal_for(org_id, [REQUEST_WRITE_OWN], subject_ref=subject)):
            created = await client.post(
                "/api/v1/itsm/service-requests",
                json={
                    "catalog_item_id": "CAT-01",
                    "subject": "Nikhil Bose (joins 01 Oct)",
                    "requester": "Nikhil Bose",
                },
            )
        assert created.status_code == 201
        body = created.json()
        assert body["status"] == "approval"
        assert body["id"].startswith("REQ-")
        number = body["id"]

        with as_principal(principal_for(org_id, [REQUEST_WRITE_OWN], subject_ref=subject)):
            self_approve = await client.post(
                f"/api/v1/itsm/service-requests/{number}/decision", json={"decision": "approve"}
            )
        assert self_approve.status_code == 403

        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_ORG], subject_ref=data["subject_b"])
        ):
            approved = await client.post(
                f"/api/v1/itsm/service-requests/{number}/decision", json={"decision": "approve"}
            )
            fulfilled = await client.post(f"/api/v1/itsm/service-requests/{number}/fulfil")
        assert approved.status_code == 200
        assert approved.json()["status"] == "in_progress"
        assert fulfilled.status_code == 200
        assert fulfilled.json()["status"] == "fulfilled"
        assert fulfilled.json()["assignee"] is None

    async def test_rejecting_requires_a_note_and_stores_it(self, client, org_id, data) -> None:
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_ORG], subject_ref=data["subject_b"])
        ):
            no_note = await client.post(
                "/api/v1/itsm/service-requests/REQ-2051/decision", json={"decision": "reject"}
            )
            rejected = await client.post(
                "/api/v1/itsm/service-requests/REQ-2051/decision",
                json={"decision": "reject", "note": "Role was cancelled before start date."},
            )
        assert no_note.status_code == 422
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert rejected.json()["decision_note"] == "Role was cancelled before start date."

    async def test_deciding_a_request_not_awaiting_approval_is_409(
        self, client, org_id, data
    ) -> None:
        """REQ-2048 seeds as `fulfilled`."""
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_ORG], subject_ref=data["subject_b"])
        ):
            response = await client.post(
                "/api/v1/itsm/service-requests/REQ-2048/decision", json={"decision": "approve"}
            )
        assert response.status_code == 409

    async def test_fulfilling_before_approval_is_409(self, client, org_id, data) -> None:
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_ORG], subject_ref=data["subject_b"])
        ):
            response = await client.post("/api/v1/itsm/service-requests/REQ-2051/fulfil")
        assert response.status_code == 409

    async def test_patching_the_assignee_requires_org_scope(self, client, org_id, data) -> None:
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_OWN], subject_ref=data["subject_a"])
        ):
            own = await client.patch(
                "/api/v1/itsm/service-requests/REQ-2051", json={"assignee": "Devansh Rao"}
            )
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_ORG], subject_ref=data["subject_b"])
        ):
            org = await client.patch(
                "/api/v1/itsm/service-requests/REQ-2051", json={"assignee": "Devansh Rao"}
            )
        assert own.status_code == 403
        assert org.status_code == 200
        assert org.json()["assignee"] == "Devansh Rao"

    async def test_an_own_requester_may_read_their_own_request_but_not_anothers(
        self, client, org_id, data
    ) -> None:
        with as_principal(
            principal_for(org_id, ["itsm.service.request.read:OWN"], subject_ref=data["subject_a"])
        ):
            own = await client.get("/api/v1/itsm/service-requests/REQ-2051")
            other = await client.get("/api/v1/itsm/service-requests/REQ-2048")
        assert own.status_code == 200
        assert other.status_code == 403

    async def test_cancelling_a_request_requires_org_scope(self, client, org_id, data) -> None:
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_OWN], subject_ref=data["subject_a"])
        ):
            own = await client.delete("/api/v1/itsm/service-requests/REQ-2051")
        assert own.status_code == 403

        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_ORG], subject_ref=data["subject_b"])
        ):
            deleted = await client.delete("/api/v1/itsm/service-requests/REQ-2051")
        assert deleted.status_code == 204

    async def test_raising_against_an_unknown_catalog_item_is_404(
        self, client, org_id, data
    ) -> None:
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_OWN], subject_ref=data["subject_a"])
        ):
            response = await client.post(
                "/api/v1/itsm/service-requests",
                json={"catalog_item_id": "CAT-NOPE", "subject": "x", "requester": "y"},
            )
        assert response.status_code == 404


# --------------------------------------------------------------------- catalog administration


class TestCatalogAdministration:
    async def test_create_update_and_retire(self, client, org_id, data) -> None:
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_ORG], subject_ref=data["subject_b"])
        ):
            created = await client.post(
                "/api/v1/itsm/catalog",
                json={"id": "CAT-09", "name": "New monitor", "category": "hardware"},
            )
            assert created.status_code == 201
            duplicate = await client.post(
                "/api/v1/itsm/catalog",
                json={"id": "CAT-09", "name": "New monitor", "category": "hardware"},
            )
            updated = await client.patch("/api/v1/itsm/catalog/CAT-09", json={"sla_days": 5})
            deleted = await client.delete("/api/v1/itsm/catalog/CAT-09")
        assert duplicate.status_code == 409
        assert updated.status_code == 200
        assert updated.json()["sla_days"] == 5
        assert deleted.status_code == 204

    async def test_an_own_scope_caller_cannot_administer_the_catalog(
        self, client, org_id, data
    ) -> None:
        with as_principal(
            principal_for(org_id, [REQUEST_WRITE_OWN], subject_ref=data["subject_a"])
        ):
            response = await client.post(
                "/api/v1/itsm/catalog",
                json={"id": "CAT-99", "name": "Denied", "category": "hardware"},
            )
        assert response.status_code == 403

    async def test_a_retired_item_still_names_itself_on_existing_requests(
        self, client, org_id, data
    ) -> None:
        """REQ-2048 points at CAT-04; retiring the catalog item must not break the request row."""
        with as_principal(
            principal_for(
                org_id,
                [REQUEST_WRITE_ORG, REQUEST_READ_ORG],
                subject_ref=data["subject_b"],
            )
        ):
            retired = await client.delete("/api/v1/itsm/catalog/CAT-04")
            requests = await client.get("/api/v1/itsm/service-requests")
        assert retired.status_code == 204
        row = next(r for r in requests.json()["items"] if r["id"] == "REQ-2048")
        assert row["catalog_item_name"] == "CAT-04"
