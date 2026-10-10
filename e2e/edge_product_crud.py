"""The eleven products' real write endpoints, end to end, as the organisation OWNER.

Each product gets its own throwaway organisation (`edge_lib.bootstrap_owner`): sign up, set the
password from the reset link read off the box over ssh, log in, start the product's 14-day trial
and wait for the subscription event to entitle it and install its roles (`core` skips the
trial — its surfaces carry no `product` entitlement key at the gateway). The owner then holds
that product's admin role at ORG (`<PRODUCT>_ADMIN = <PRODUCT>.all("ORG")` in every catalogue),
so every write below is expected to succeed on permission grounds; a 403 anywhere is a real
defect, not a fixture to route around.

For each product this walks a real create -> read -> update -> delete (or the product's actual
lifecycle where delete does not exist), asserting both the status code and that a read-back
reflects each write. Not part of the pytest suite: it creates real organisations on the
production box. Do **not** run it against the edge until the new write endpoints are deployed —
`edge_product_smoke.py`'s marketplace/entitlement dance still has to succeed first.

    uv run python e2e/edge_product_crud.py --product operations
    uv run python e2e/edge_product_crud.py --all

`--all` runs every product's flow one after another, never in parallel, for the same reason
`edge_product_smoke.py` does: the gateway rate-limits a caller per minute. A CRUD flow makes many
more calls per product than the smoke script's single read, so a full `--all` run is slower and
leans harder on that limit — see the report for how close it runs in practice.
"""

from __future__ import annotations

# read best unwrapped.
import argparse
import sys
import time
import uuid
from collections.abc import Callable
from datetime import date, timedelta

import httpx
from edge_lib import API, PatientClient, StepLog, bootstrap_owner

#: A GSTIN known to pass `domain.parse_gstin`'s checksum (`tools/demo/seed_finance.py`'s
#: `SELLER_GSTIN`) — org-scoped uniqueness means every throwaway org can register the same one.
FINANCE_GSTIN = "27AABCA9012D1Z5"

#: The bullet glyph `platform_core.pii.mask` uses for every masked field — the one thing every
#: "is this actually masked" assertion below checks for, rather than guessing a format.
BULLET = "•"


class Abort(Exception):
    """Raised when a step this product's later steps depend on failed outright. Caught once,
    per product, in `run_product` — the step itself is already logged as FAIL by the time this
    is raised; this only stops the dependent steps that would otherwise fail confusingly."""


def _require(log: StepLog, name: str, ok: bool, detail: str = "") -> None:
    """Log a step; abort this product's remaining steps if it failed."""
    log.step(name, ok, detail)
    if not ok:
        raise Abort(name)


# ---------------------------------------------------------------------- operations


def flow_operations(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]
    today = date.today()

    r = c.post(
        "/api/v1/operations/warehouses",
        headers=h,
        json={"code": f"WH-{suffix}", "name": f"Warehouse {suffix}"},
    )
    _require(log, "create warehouse", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    warehouse = r.json()

    r = c.post(
        "/api/v1/operations/suppliers",
        headers=h,
        json={"code": f"SUP-{suffix}", "name": f"Supplier {suffix}", "category": "raw_materials"},
    )
    _require(log, "create supplier", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    supplier = r.json()

    sku = f"SKU-{suffix}"
    r = c.post(
        "/api/v1/operations/inventory",
        headers=h,
        json={
            "sku": sku,
            "name": f"Widget {suffix}",
            "category": "raw_materials",
            "warehouse": warehouse["name"],
            "uom": "unit",
            "opening_on_hand": 20,
        },
    )
    _require(
        log,
        "create inventory item with opening_on_hand=20",
        r.status_code == 201 and r.json().get("on_hand") == 20,
        f"{r.status_code} {r.text[:160]}",
    )
    item = r.json()

    r = c.post(
        f"/api/v1/operations/inventory/{item['id']}/adjustments",
        headers=h,
        json={"quantity_delta": 5, "reason": "found"},
    )
    _require(
        log,
        "POST .../adjustments delta +5",
        r.status_code == 201 and r.json().get("on_hand") == 25,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        f"/api/v1/operations/inventory/{item['id']}/adjustments",
        headers=h,
        json={"counted_on_hand": 30},
    )
    _require(
        log,
        "POST .../adjustments count -> 30",
        r.status_code == 201 and r.json().get("on_hand") == 30,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.get(f"/api/v1/operations/inventory/{item['id']}/movements", headers=h)
    kinds = {m["kind"] for m in r.json().get("items", [])} if r.status_code == 200 else set()
    _require(
        log,
        "GET .../movements lists opening + adjustment + count",
        r.status_code == 200 and {"opening", "adjustment", "count"} <= kinds,
        f"{r.status_code} kinds={sorted(kinds)}",
    )

    unmatched_sku = f"NO-MATCH-{suffix}"
    r = c.post(
        "/api/v1/operations/purchase-orders",
        headers=h,
        json={
            "supplier_id": supplier["id"],
            "warehouse": warehouse["name"],
            "expected_date": (today + timedelta(days=7)).isoformat(),
            "lines": [
                {
                    "sku": sku,
                    "description": "Restock",
                    "quantity": 10,
                    "uom": "unit",
                    "unit_price_minor": 500,
                },
                {
                    "sku": unmatched_sku,
                    "description": "Freight (no matching SKU)",
                    "quantity": 1,
                    "uom": "unit",
                    "unit_price_minor": 200,
                },
            ],
        },
    )
    _require(
        log,
        "raise PO: one line matches the item's SKU, one matches nothing",
        r.status_code == 201 and r.json().get("status") == "draft",
        f"{r.status_code} {r.text[:200]}",
    )
    po = r.json()

    r = c.post(f"/api/v1/operations/purchase-orders/{po['id']}/approve", headers=h)
    _require(
        log,
        "approve",
        r.status_code == 200 and r.json().get("status") == "approved",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(f"/api/v1/operations/purchase-orders/{po['id']}/receive", headers=h)
    unmatched = r.json().get("unmatched_skus", []) if r.status_code == 200 else []
    _require(
        log,
        "receive: response names the unmatched SKU",
        r.status_code == 200 and unmatched == [unmatched_sku],
        f"{r.status_code} unmatched={unmatched}",
    )

    r = c.get(f"/api/v1/operations/inventory/{item['id']}", headers=h)
    on_hand = r.json().get("on_hand") if r.status_code == 200 else None
    _require(
        log,
        "on_hand rose by the matched quantity (30 -> 40)",
        r.status_code == 200 and on_hand == 40,
        f"{r.status_code} on_hand={on_hand}",
    )

    r = c.post(f"/api/v1/operations/purchase-orders/{po['id']}/close", headers=h)
    _require(
        log,
        "close",
        r.status_code == 200 and r.json().get("status") == "closed",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.patch(f"/api/v1/operations/inventory/{item['id']}", headers=h, json={"on_hand": 5})
    _require(
        log,
        "PATCH with on_hand in the body -> 422",
        r.status_code == 422,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.delete(f"/api/v1/operations/inventory/{item['id']}", headers=h)
    _require(
        log,
        "delete the item with stock -> 409",
        r.status_code == 409,
        f"{r.status_code} {r.text[:160]}",
    )


# ---------------------------------------------------------------------- hrms


def flow_hrms(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]

    r = c.post(
        "/api/v1/employees",
        headers=h,
        json={"employee_no": f"E-{suffix}", "full_name": f"Employee {suffix}"},
    )
    _require(
        log,
        "create employee (POST /employees)",
        r.status_code == 201 and r.json().get("status") == "probation",
        f"{r.status_code} {r.text[:160]}",
    )
    employee = r.json()

    r = c.get(f"/api/v1/employees/{employee['id']}", headers=h)
    _require(log, "read employee", r.status_code == 200, f"{r.status_code} {r.text[:160]}")

    r = c.patch(f"/api/v1/employees/{employee['id']}", headers=h, json={"designation": "Engineer"})
    _require(
        log,
        "PATCH employee",
        r.status_code == 200 and r.json().get("designation") == "Engineer",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        f"/api/v1/employees/{employee['id']}/transition",
        headers=h,
        json={"to": "active", "reason": "onboarded"},
    )
    _require(
        log,
        "transition along a legal path (probation -> active)",
        r.status_code == 200 and r.json().get("status") == "active",
        f"{r.status_code} {r.text[:160]}",
    )

    shift_code = f"SHIFT-{suffix}"
    r = c.post(
        "/api/v1/shifts",
        headers=h,
        json={
            "code": shift_code,
            "name": "Day Shift",
            "start_time": "09:00:00",
            "end_time": "18:00:00",
        },
    )
    _require(log, "create shift", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    shift = r.json()

    r = c.patch(f"/api/v1/shifts/{shift['id']}", headers=h, json={"name": "Day Shift (renamed)"})
    _require(
        log,
        "PATCH shift",
        r.status_code == 200 and r.json().get("name") == "Day Shift (renamed)",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.delete(f"/api/v1/shifts/{shift['id']}", headers=h)
    _require(log, "DELETE shift", r.status_code == 204, f"{r.status_code}")

    r = c.post(
        "/api/v1/shifts",
        headers=h,
        json={
            "code": shift_code,
            "name": "Day Shift Mk2",
            "start_time": "10:00:00",
            "end_time": "19:00:00",
        },
    )
    _require(
        log,
        "recreate with the same code succeeds",
        r.status_code == 201,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        "/api/v1/geofences",
        headers=h,
        json={
            "location_code": f"LOC-{suffix}",
            "name": "HQ Fence",
            "center_lat": 12.9716,
            "center_lng": 77.5946,
            "radius_m": 200,
        },
    )
    _require(
        log,
        "create geofence via the newly routed /api/v1/geofences",
        r.status_code == 201,
        f"{r.status_code} {r.text[:160]}",
    )
    fence = r.json()

    r = c.delete(f"/api/v1/geofences/{fence['id']}", headers=h)
    _require(log, "delete geofence", r.status_code == 204, f"{r.status_code}")


# ---------------------------------------------------------------------- crm


def flow_crm(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]
    today = date.today()

    r = c.post(
        "/api/v1/customers/owners",
        headers=h,
        json={"full_name": "Rep Owner", "email": f"rep-{suffix}@example.com"},
    )
    _require(
        log,
        "create an owner (POST /customers/owners)",
        r.status_code == 201,
        f"{r.status_code} {r.text[:160]}",
    )
    owner = r.json()

    r = c.post(
        "/api/v1/customers", headers=h, json={"name": f"Acme {suffix}", "owner_id": owner["id"]}
    )
    _require(log, "create company", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    company = r.json()

    raw_phone = "+919876543210"
    r = c.post(
        "/api/v1/contacts",
        headers=h,
        json={
            "full_name": "Jane Buyer",
            "email": f"jane-{suffix}@example.com",
            "company_id": company["id"],
            "owner_id": owner["id"],
            "raw_phone": raw_phone,
        },
    )
    _require(
        log,
        "create contact with raw_phone",
        r.status_code == 201,
        f"{r.status_code} {r.text[:160]}",
    )
    contact = r.json()
    _require(
        log,
        "read-back phone is MASKED",
        BULLET in contact.get("phone", "") and contact.get("phone") != raw_phone,
        f"phone={contact.get('phone')!r}",
    )

    r = c.post(
        "/api/v1/contacts",
        headers=h,
        json={
            "full_name": "Spoof",
            "email": f"spoof-{suffix}@example.com",
            "company_id": company["id"],
            "owner_id": owner["id"],
            "phone": raw_phone,
        },
    )
    _require(
        log,
        "sending phone (not raw_phone) in the body -> 422",
        r.status_code == 422,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        "/api/v1/deals",
        headers=h,
        json={
            "name": f"Deal {suffix}",
            "company_id": company["id"],
            "owner_id": owner["id"],
            "value_minor": 100_000,
            "close_date": (today + timedelta(days=30)).isoformat(),
            "primary_contact_id": contact["id"],
        },
    )
    _require(
        log,
        "create deal",
        r.status_code == 201 and r.json().get("stage") == "lead",
        f"{r.status_code} {r.text[:160]}",
    )
    deal = r.json()

    r = c.post(f"/api/v1/deals/{deal['id']}/stage", headers=h, json={"stage": "qualified"})
    _require(
        log,
        "POST /deals/{id}/stage",
        r.status_code == 200 and r.json().get("stage") == "qualified",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.get(f"/api/v1/customers/{contact['id']}/360", headers=h)
    card = r.json() if r.status_code == 200 else {}
    _require(
        log,
        "the 360 shows the deal and its CRM section",
        r.status_code == 200
        and "crm" not in card.get("unavailable", [])
        and card.get("crm") == {"stage": "qualified", "open_opportunities": 1, "hot": False},
        f"{r.status_code} crm={card.get('crm')!r} unavailable={card.get('unavailable')!r}",
    )

    r = c.post(
        "/api/v1/deals/activities",
        headers=h,
        json={"type": "note", "subject": "Called customer", "deal_id": deal["id"]},
    )
    _require(log, "log an activity", r.status_code == 201, f"{r.status_code} {r.text[:160]}")

    r = c.delete(f"/api/v1/deals/{deal['id']}", headers=h)
    _require(log, "delete the deal", r.status_code == 204, f"{r.status_code}")

    r = c.delete(f"/api/v1/customers/{company['id']}", headers=h)
    _require(
        log,
        "deleting the company while the contact exists -> 409",
        r.status_code == 409,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.delete(f"/api/v1/contacts/{contact['id']}", headers=h)
    _require(log, "delete contact", r.status_code == 204, f"{r.status_code}")

    r = c.delete(f"/api/v1/customers/{company['id']}", headers=h)
    _require(log, "delete company", r.status_code == 204, f"{r.status_code}")


# ---------------------------------------------------------------------- pulseboard


def flow_pulseboard(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]

    r = c.post(
        "/api/v1/projects/people", headers=h, json={"ref": f"p-{suffix}", "name": "Ada Lovelace"}
    )
    _require(log, "create roster person", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    person = r.json()

    project_code = f"PB-{suffix}"
    r = c.post(
        "/api/v1/projects", headers=h, json={"code": project_code, "name": f"Project {suffix}"}
    )
    _require(log, "create project", r.status_code == 201, f"{r.status_code} {r.text[:160]}")

    task_key = f"TASK-{suffix}"
    r = c.post(
        "/api/v1/projects/tasks",
        headers=h,
        json={
            "key": task_key,
            "project_code": project_code,
            "title": "Do the thing",
            "assignee_ref": person["id"],
        },
    )
    _require(
        log,
        "create task",
        r.status_code == 201 and r.json().get("status") == "backlog",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.patch(f"/api/v1/projects/tasks/{task_key}", headers=h, json={"status": "in_progress"})
    _require(
        log,
        "PATCH /projects/tasks/{key} status move",
        r.status_code == 200 and r.json().get("status") == "in_progress",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.get("/api/v1/projects/tasks", headers=h, params={"project": project_code})
    items = r.json().get("items", []) if r.status_code == 200 else []
    moved = next((t for t in items if t["key"] == task_key), None)
    _require(
        log,
        "read back the moved task",
        r.status_code == 200 and moved is not None and moved.get("status") == "in_progress",
        f"{r.status_code} moved={moved}",
    )

    r = c.delete(f"/api/v1/projects/tasks/{task_key}", headers=h)
    _require(log, "delete task", r.status_code == 204, f"{r.status_code}")

    r = c.delete(f"/api/v1/projects/{project_code}", headers=h)
    _require(log, "delete project", r.status_code == 204, f"{r.status_code}")


# ---------------------------------------------------------------------- projects-workspace


def flow_projects(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]
    today = date.today()
    base = "/api/v1/projects-workspace"

    r = c.post(f"{base}/members", headers=h, json={"ref": f"m-{suffix}", "name": "Priya Delivery"})
    _require(log, "create member", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    member = r.json()

    project_code = f"PRJ-{suffix}"
    r = c.post(
        f"{base}/projects",
        headers=h,
        json={
            "code": project_code,
            "name": f"Workspace project {suffix}",
            "owner_ref": member["id"],
            "start_date": today.isoformat(),
            "target_date": (today + timedelta(days=60)).isoformat(),
        },
    )
    _require(log, "create project", r.status_code == 201, f"{r.status_code} {r.text[:200]}")

    # `MilestoneIn.due_date` is a required field on the wire today (`app/schemas.py`), not the
    # optional field the brief describes — a create with no due date is a 422, not a legitimate
    # milestone. Supplying one here and only asserting the *list* still answers 200 is the
    # closest honest reading; see the report for the full note.
    r = c.post(
        f"{base}/milestones",
        headers=h,
        json={
            "project_code": project_code,
            "title": "Kickoff",
            "due_date": (today + timedelta(days=14)).isoformat(),
        },
    )
    _require(log, "create milestone", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    milestone = r.json()

    r = c.get(f"{base}/milestones", headers=h)
    _require(log, "the milestone list still answers 200", r.status_code == 200, f"{r.status_code}")

    r = c.post(
        f"{base}/tasks",
        headers=h,
        json={
            "project_code": project_code,
            "title": "Set up the repo",
            "assignee_ref": member["id"],
        },
    )
    _require(log, "create task", r.status_code == 201, f"{r.status_code} {r.text[:200]}")
    task = r.json()

    r = c.patch(f"{base}/tasks/{task['id']}", headers=h, json={"stage": "in_progress"})
    _require(
        log,
        "PATCH task stage",
        r.status_code == 200 and r.json().get("stage") == "in_progress",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.delete(f"{base}/projects/{project_code}", headers=h)
    _require(log, "delete project", r.status_code == 204, f"{r.status_code}")

    r = c.get(f"{base}/milestones/{milestone['id']}", headers=h)
    _require(log, "its milestone is gone", r.status_code == 404, f"{r.status_code}")

    r = c.get(f"{base}/tasks", headers=h, params={"project_code": project_code})
    remaining = r.json().get("items") if r.status_code == 200 else None
    _require(
        log,
        "its task is gone",
        r.status_code == 200 and remaining == [],
        f"{r.status_code} items={remaining}",
    )


# ---------------------------------------------------------------------- support


def flow_support(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]

    r = c.post(
        "/api/v1/support/agents",
        headers=h,
        json={"name": "Agent Smith", "email": f"agent-{suffix}@example.com"},
    )
    _require(log, "create agent", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    agent = r.json()

    r = c.post(
        "/api/v1/support/tickets",
        headers=h,
        json={
            "subject": "Cannot log in",
            "requester": {
                "name": "Buyer",
                "email": f"buyer-{suffix}@example.com",
                "company": "Acme",
                "kind": "customer",
            },
            "body": "Please help, I cannot sign in.",
        },
    )
    _require(
        log,
        "create ticket",
        r.status_code == 201 and r.json().get("status") == "new",
        f"{r.status_code} {r.text[:160]}",
    )
    number = r.json()["id"]

    r = c.post(
        f"/api/v1/support/tickets/{number}/assign", headers=h, json={"assignee_id": agent["id"]}
    )
    _require(
        log,
        "assign",
        r.status_code == 200 and r.json().get("assignee_id") == agent["id"],
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        f"/api/v1/support/tickets/{number}/reply",
        headers=h,
        json={"body": "Looking into it", "author_name": "Agent Smith"},
    )
    _require(log, "reply (no author_type)", r.status_code == 201, f"{r.status_code} {r.text[:160]}")

    r = c.post(
        f"/api/v1/support/tickets/{number}/reply",
        headers=h,
        json={"body": "x", "author_name": "Agent Smith", "author_type": "agent"},
    )
    _require(
        log,
        "sending author_type in the body -> 422",
        r.status_code == 422,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(f"/api/v1/support/tickets/{number}/status", headers=h, json={"status": "open"})
    _require(
        log,
        "status new -> open",
        r.status_code == 200 and r.json().get("status") == "open",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(f"/api/v1/support/tickets/{number}/status", headers=h, json={"status": "solved"})
    _require(
        log,
        "status open -> solved",
        r.status_code == 200 and r.json().get("status") == "solved",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(f"/api/v1/support/tickets/{number}/status", headers=h, json={"status": "pending"})
    _require(
        log,
        "an illegal move (solved -> pending) -> 409",
        r.status_code == 409,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.put(
        "/api/v1/support/sla-policy",
        headers=h,
        json={
            "name": "Standard",
            "targets": [
                {"priority": "urgent", "first_response_min": 15, "resolution_min": 240},
                {"priority": "high", "first_response_min": 30, "resolution_min": 480},
                {"priority": "normal", "first_response_min": 60, "resolution_min": 1440},
                {"priority": "low", "first_response_min": 120, "resolution_min": 2880},
            ],
        },
    )
    _require(
        log, "PUT /support/sla-policy", r.status_code == 200, f"{r.status_code} {r.text[:200]}"
    )

    r = c.get("/api/v1/support/sla-policy", headers=h)
    _require(
        log,
        "GET /support/sla-policy reflects it",
        r.status_code == 200 and r.json().get("name") == "Standard",
        f"{r.status_code} {r.text[:160]}",
    )


# ---------------------------------------------------------------------- marketing


def flow_marketing(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]
    today = date.today()

    r = c.post(
        "/api/v1/marketing/audiences",
        headers=h,
        json={"name": f"Audience {suffix}", "size": 1000, "created_at": today.isoformat()},
    )
    _require(log, "audience", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    audience = r.json()

    r = c.post(
        "/api/v1/marketing/campaigns",
        headers=h,
        json={
            "name": f"Campaign {suffix}",
            "channel": "email",
            "audience_id": audience["id"],
            "start_date": today.isoformat(),
        },
    )
    _require(
        log,
        "campaign",
        r.status_code == 201 and r.json().get("status") == "draft",
        f"{r.status_code} {r.text[:160]}",
    )
    campaign = r.json()

    r = c.patch(f"/api/v1/marketing/campaigns/{campaign['id']}", headers=h, json={"status": "live"})
    _require(
        log,
        "PATCH status draft -> live",
        r.status_code == 200 and r.json().get("status") == "live",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.patch(
        f"/api/v1/marketing/campaigns/{campaign['id']}", headers=h, json={"status": "paused"}
    )
    _require(
        log,
        "live -> paused",
        r.status_code == 200 and r.json().get("status") == "paused",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.patch(
        f"/api/v1/marketing/campaigns/{campaign['id']}", headers=h, json={"status": "ended"}
    )
    _require(
        log,
        "paused -> ended",
        r.status_code == 200 and r.json().get("status") == "ended",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.patch(f"/api/v1/marketing/campaigns/{campaign['id']}", headers=h, json={"status": "live"})
    _require(log, "ended -> live -> 409", r.status_code == 409, f"{r.status_code} {r.text[:160]}")

    r = c.delete(f"/api/v1/marketing/audiences/{audience['id']}", headers=h)
    _require(
        log,
        "deleting the audience while the campaign exists -> 409",
        r.status_code == 409,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.delete(f"/api/v1/marketing/campaigns/{campaign['id']}", headers=h)
    _require(log, "delete campaign", r.status_code == 204, f"{r.status_code}")

    r = c.delete(f"/api/v1/marketing/audiences/{audience['id']}", headers=h)
    _require(log, "delete audience", r.status_code == 204, f"{r.status_code}")


# ---------------------------------------------------------------------- finance


def flow_finance(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]
    today = date.today()

    r = c.put(
        "/api/v1/finance/gst/registration",
        headers=h,
        json={"gstin": FINANCE_GSTIN, "legal_name": f"Crud Test {suffix} Pvt Ltd"},
    )
    _require(
        log,
        "GST registration first (not strictly required to invoice, but checked and done)",
        r.status_code == 200,
        f"{r.status_code} {r.text[:200]}",
    )

    r = c.post("/api/v1/finance/customers", headers=h, json={"name": f"Buyer {suffix}"})
    _require(log, "create customer", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    customer = r.json()

    invoice_body = {
        "customer_id": customer["id"],
        "issued_date": today.isoformat(),
        "due_date": (today + timedelta(days=30)).isoformat(),
        "tax_rate_pct": 18,
        "lines": [{"description": "Consulting", "quantity": 2, "unit_price_minor": 50_000}],
    }
    r = c.post("/api/v1/finance/invoices", headers=h, json=invoice_body)
    _require(
        log,
        "issue invoice; server allocates the number",
        r.status_code == 201 and str(r.json().get("number", "")).startswith("INV"),
        f"{r.status_code} {r.text[:200]}",
    )
    invoice = r.json()
    total_minor = invoice["total_minor"]

    r = c.post("/api/v1/finance/invoices", headers=h, json={**invoice_body, "status": "paid"})
    _require(
        log,
        "sending status in the body -> 422",
        r.status_code == 422,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        "/api/v1/finance/invoices", headers=h, json={**invoice_body, "number": "INV-CUSTOM-1"}
    )
    _require(
        log,
        "sending number in the body -> 422",
        r.status_code == 422,
        f"{r.status_code} {r.text[:160]}",
    )

    half = total_minor // 2
    rest = total_minor - half
    r = c.post(
        "/api/v1/finance/payments",
        headers=h,
        json={
            "reference": f"PAY-{suffix}-1",
            "customer_id": customer["id"],
            "received_date": today.isoformat(),
            "method": "bank_transfer",
            "allocations": [{"invoice_id": invoice["id"], "amount_minor": half}],
        },
    )
    _require(
        log, "record a partial payment", r.status_code == 201, f"{r.status_code} {r.text[:200]}"
    )

    r = c.get(f"/api/v1/finance/invoices/{invoice['id']}", headers=h)
    body = r.json() if r.status_code == 200 else {}
    _require(
        log,
        "invoice is not paid yet",
        r.status_code == 200
        and body.get("status") != "paid"
        and body.get("amount_paid_minor") == half,
        f"{r.status_code} status={body.get('status')} paid={body.get('amount_paid_minor')}",
    )

    r = c.post(
        "/api/v1/finance/payments",
        headers=h,
        json={
            "reference": f"PAY-{suffix}-2",
            "customer_id": customer["id"],
            "received_date": today.isoformat(),
            "method": "bank_transfer",
            "allocations": [{"invoice_id": invoice["id"], "amount_minor": rest}],
        },
    )
    _require(log, "pay the rest", r.status_code == 201, f"{r.status_code} {r.text[:200]}")

    r = c.get(f"/api/v1/finance/invoices/{invoice['id']}", headers=h)
    body = r.json() if r.status_code == 200 else {}
    _require(
        log,
        "invoice is paid",
        r.status_code == 200
        and body.get("status") == "paid"
        and body.get("amount_paid_minor") == total_minor,
        f"{r.status_code} status={body.get('status')} paid={body.get('amount_paid_minor')}",
    )

    r = c.post(
        "/api/v1/finance/invoices",
        headers=h,
        json={
            "customer_id": customer["id"],
            "issued_date": today.isoformat(),
            "due_date": (today + timedelta(days=30)).isoformat(),
            "tax_rate_pct": 18,
            "document_type": "credit_note",
            "lines": [{"description": "Refund", "quantity": -1, "unit_price_minor": 50_000}],
        },
    )
    _require(
        log,
        "credit note without corrects_invoice_id -> 422",
        r.status_code == 422,
        f"{r.status_code} {r.text[:200]}",
    )

    r = c.delete(f"/api/v1/finance/customers/{customer['id']}", headers=h)
    _require(
        log,
        "archive the customer -> 409 (documents reference it)",
        r.status_code == 409,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.patch(f"/api/v1/finance/invoices/{invoice['id']}", headers=h, json={"notes": "nope"})
    _require(log, "PATCH invoice -> 404/405", r.status_code in (404, 405), f"{r.status_code}")

    r = c.delete(f"/api/v1/finance/invoices/{invoice['id']}", headers=h)
    _require(log, "DELETE invoice -> 404/405", r.status_code in (404, 405), f"{r.status_code}")


# ---------------------------------------------------------------------- itsm


def flow_itsm(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]

    catalog_id = f"CAT-{suffix}"
    r = c.post(
        "/api/v1/itsm/catalog",
        headers=h,
        json={"id": catalog_id, "name": "New laptop", "category": "hardware"},
    )
    _require(log, "catalog item", r.status_code == 201, f"{r.status_code} {r.text[:160]}")

    r = c.post(
        "/api/v1/itsm/service-requests",
        headers=h,
        json={
            "catalog_item_id": catalog_id,
            "subject": "New hire laptop",
            "requester": "Crud Test Owner",
        },
    )
    _require(
        log,
        "service request",
        r.status_code == 201 and r.json().get("status") == "approval",
        f"{r.status_code} {r.text[:160]}",
    )
    req1 = r.json()

    r = c.post(
        f"/api/v1/itsm/service-requests/{req1['id']}/decision",
        headers=h,
        json={"decision": "approve"},
    )
    _require(
        log,
        "decision approve",
        r.status_code == 200 and r.json().get("status") == "in_progress",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(f"/api/v1/itsm/service-requests/{req1['id']}/fulfil", headers=h)
    _require(
        log,
        "fulfil",
        r.status_code == 200 and r.json().get("status") == "fulfilled",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        "/api/v1/itsm/service-requests",
        headers=h,
        json={
            "catalog_item_id": catalog_id,
            "subject": "Second request",
            "requester": "Crud Test Owner",
        },
    )
    _require(log, "a second request", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    req2 = r.json()

    r = c.post(
        f"/api/v1/itsm/service-requests/{req2['id']}/decision",
        headers=h,
        json={"decision": "reject"},
    )
    _require(
        log, "reject without note -> 422", r.status_code == 422, f"{r.status_code} {r.text[:160]}"
    )

    r = c.post(
        f"/api/v1/itsm/service-requests/{req2['id']}/decision",
        headers=h,
        json={"decision": "reject", "note": "Not eligible"},
    )
    body = r.json() if r.status_code == 200 else {}
    _require(
        log,
        "reject with note -> rejected, decision_note readable",
        r.status_code == 200
        and body.get("status") == "rejected"
        and body.get("decision_note") == "Not eligible",
        f"{r.status_code} {body}",
    )

    r = c.post(
        "/api/v1/itsm/incidents",
        headers=h,
        json={"summary": "Printer on fire", "requester": "Crud Test Owner"},
    )
    _require(
        log,
        "incident",
        r.status_code == 201 and r.json().get("status") == "new",
        f"{r.status_code} {r.text[:160]}",
    )
    incident = r.json()

    r = c.post(
        f"/api/v1/itsm/incidents/{incident['id']}/worklog",
        headers=h,
        json={"author": "Crud Test Owner", "body": "Investigating"},
    )
    _require(
        log,
        "worklog note",
        r.status_code == 200 and len(r.json().get("worklog", [])) >= 1,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        f"/api/v1/itsm/incidents/{incident['id']}/status", headers=h, json={"status": "in_progress"}
    )
    _require(
        log,
        "status new -> in_progress",
        r.status_code == 200 and r.json().get("status") == "in_progress",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        f"/api/v1/itsm/incidents/{incident['id']}/status",
        headers=h,
        json={"status": "resolved", "note": "Replaced the fuser"},
    )
    _require(
        log,
        "status in_progress -> resolved (note required)",
        r.status_code == 200 and r.json().get("status") == "resolved",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        f"/api/v1/itsm/incidents/{incident['id']}/status", headers=h, json={"status": "closed"}
    )
    _require(
        log,
        "status resolved -> closed",
        r.status_code == 200 and r.json().get("status") == "closed",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        f"/api/v1/itsm/incidents/{incident['id']}/status", headers=h, json={"status": "in_progress"}
    )
    _require(
        log, "closed -> anything -> 409", r.status_code == 409, f"{r.status_code} {r.text[:160]}"
    )

    tag = f"AST-{suffix}"
    r = c.post(
        "/api/v1/itsm/assets", headers=h, json={"tag": tag, "name": "ThinkPad", "type": "laptop"}
    )
    _require(log, "asset", r.status_code == 201, f"{r.status_code} {r.text[:160]}")

    r = c.patch(f"/api/v1/itsm/assets/{tag}", headers=h, json={"status": "in_repair"})
    _require(
        log,
        "PATCH asset",
        r.status_code == 200 and r.json().get("status") == "in_repair",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.delete(f"/api/v1/itsm/assets/{tag}", headers=h)
    _require(log, "DELETE asset", r.status_code == 204, f"{r.status_code}")


# ---------------------------------------------------------------------- storefront


def flow_storefront(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]

    r = c.put("/api/v1/storefront/store", headers=h, json={"slug": slug, "name": "Crud Test Store"})
    _require(
        log,
        "PUT /storefront/store (identity)",
        r.status_code == 200,
        f"{r.status_code} {r.text[:200]}",
    )

    r = c.put(
        "/api/v1/storefront/theme",
        headers=h,
        json={"theme_key": "default", "mode": "retail", "tokens": {}, "pages": [], "version": 1},
    )
    _require(
        log,
        "PUT /storefront/theme with version=1 (adopt)",
        r.status_code == 200 and r.json().get("version") == 1,
        f"{r.status_code} {r.text[:200]}",
    )

    r = c.get("/api/v1/storefront/theme", headers=h)
    _require(
        log,
        "GET /storefront/theme",
        r.status_code == 200 and r.json().get("version") == 1,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.put(
        "/api/v1/storefront/theme",
        headers=h,
        json={
            "theme_key": "default",
            "mode": "retail",
            "tokens": {"a": 1},
            "pages": [],
            "version": 1,
        },
    )
    _require(
        log,
        "PUT theme with the current version saves (version -> 2)",
        r.status_code == 200 and r.json().get("version") == 2,
        f"{r.status_code} {r.text[:200]}",
    )

    r = c.put(
        "/api/v1/storefront/theme",
        headers=h,
        json={"theme_key": "default", "mode": "retail", "tokens": {}, "pages": [], "version": 1},
    )
    _require(
        log,
        "a PUT with a stale version -> 409",
        r.status_code == 409,
        f"{r.status_code} {r.text[:200]}",
    )

    category_key = f"cat-{suffix}"
    r = c.post(
        "/api/v1/storefront/categories", headers=h, json={"key": category_key, "label": "Widgets"}
    )
    _require(log, "category", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    category = r.json()

    product_code = f"PROD-{suffix}"
    r = c.post(
        "/api/v1/storefront/products",
        headers=h,
        json={
            "code": product_code,
            "name": "Widget",
            "category_id": category["id"],
            "price_min_minor": 10_000,
            "price_max_minor": 10_000,
            "status": "active",
            "variants": [
                {
                    "label": "Pack of 1",
                    "price_min_minor": 10_000,
                    "price_max_minor": 10_000,
                    "mrp_minor": 5_000,
                },
                {
                    "label": "Pack of 6",
                    "price_min_minor": 55_000,
                    "price_max_minor": 55_000,
                    "mrp_minor": 60_000,
                },
            ],
        },
    )
    _require(
        log,
        "product with two variants, mrp_minor below price -> 422",
        r.status_code == 422,
        f"{r.status_code} {r.text[:200]}",
    )

    r = c.post(
        "/api/v1/storefront/products",
        headers=h,
        json={
            "code": product_code,
            "name": "Widget",
            "category_id": category["id"],
            "price_min_minor": 10_000,
            "price_max_minor": 10_000,
            "status": "active",
            "variants": [
                {
                    "label": "Pack of 1",
                    "price_min_minor": 10_000,
                    "price_max_minor": 10_000,
                    "mrp_minor": 12_000,
                },
                {
                    "label": "Pack of 6",
                    "price_min_minor": 55_000,
                    "price_max_minor": 55_000,
                    "mrp_minor": 60_000,
                },
            ],
        },
    )
    _require(
        log,
        "then valid -> 201",
        r.status_code == 201 and r.json().get("status") == "active",
        f"{r.status_code} {r.text[:200]}",
    )
    product = r.json()

    r = c.get("/api/v1/storefront/products", headers=h, params={"q": "Widget"})
    items = r.json().get("items", []) if r.status_code == 200 else []
    _require(
        log,
        "list with q",
        r.status_code == 200 and any(p["id"] == product["id"] for p in items),
        f"{r.status_code}",
    )

    r = c.patch(f"/api/v1/storefront/products/{product['id']}", headers=h, json={"featured": True})
    _require(
        log,
        "PATCH product",
        r.status_code == 200 and r.json().get("featured") is True,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.delete(f"/api/v1/storefront/categories/{category['id']}", headers=h)
    _require(
        log,
        "deleting the category while the product exists -> 422",
        r.status_code == 422,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post("/api/v1/storefront/store/publish", headers=h)
    _require(
        log,
        "publish the store",
        r.status_code == 200 and r.json().get("status") == "published",
        f"{r.status_code} {r.text[:160]}",
    )

    enquiry_id = None
    with PatientClient(base_url=API, timeout=30) as anon:
        r = anon.post("/api/v1/storefront/public/catalogue", json={"store": slug})
        found = (
            any(p["id"] == product["id"] for p in r.json().get("products", {}).get("items", []))
            if r.status_code == 200
            else False
        )
        _require(
            log,
            "anonymous POST /storefront/public/catalogue (no Authorization) returns the product",
            r.status_code == 200 and found,
            f"{r.status_code} {r.text[:200]}",
        )

        r = anon.post(
            "/api/v1/storefront/public/enquiry",
            json={
                "store": slug,
                "name": "Buyer",
                "company": "Acme",
                "email": f"buyer-{suffix}@example.com",
                "requirement": "Need 10 units",
            },
        )
        _require(
            log,
            "anonymous POST /storefront/public/enquiry -> 201",
            r.status_code == 201,
            f"{r.status_code} {r.text[:200]}",
        )
        enquiry_id = r.json().get("id")

    r = c.get("/api/v1/storefront/enquiries", headers=h)
    items = r.json().get("items", []) if r.status_code == 200 else []
    mine = next((e for e in items if e["id"] == enquiry_id), None)
    _require(
        log,
        "owner lists enquiries, sees the email MASKED",
        r.status_code == 200 and mine is not None and BULLET in mine.get("email_masked", ""),
        f"{r.status_code} {mine}",
    )

    r = c.patch(
        f"/api/v1/storefront/enquiries/{enquiry_id}", headers=h, json={"status": "contacted"}
    )
    _require(
        log,
        "status new -> contacted",
        r.status_code == 200 and r.json().get("status") == "contacted",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.patch(f"/api/v1/storefront/enquiries/{enquiry_id}", headers=h, json={"status": "won"})
    _require(
        log,
        "status contacted -> won",
        r.status_code == 200 and r.json().get("status") == "won",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post("/api/v1/storefront/store/unpublish", headers=h)
    _require(
        log,
        "unpublish",
        r.status_code == 200 and r.json().get("status") == "draft",
        f"{r.status_code} {r.text[:160]}",
    )

    with PatientClient(base_url=API, timeout=30) as anon:
        r = anon.post("/api/v1/storefront/public/catalogue", json={"store": slug})
        _require(
            log,
            "the anonymous catalogue no longer serves it",
            r.status_code == 404,
            f"{r.status_code} {r.text[:160]}",
        )


# ---------------------------------------------------------------------- core (no product)


def flow_core(c: httpx.Client, log: StepLog, h: dict[str, str], slug: str) -> None:
    suffix = uuid.uuid4().hex[:6]

    r = c.get("/api/v1/members/me/grants", headers=h)
    grants = r.json().get("grants", []) if r.status_code == 200 else []
    _require(
        log,
        "GET /members/me/grants returns a non-empty list for the owner",
        r.status_code == 200 and len(grants) > 0,
        f"{r.status_code} grants={len(grants)}",
    )

    r = c.get("/api/v1/permissions", headers=h)
    _require(
        log, "GET /permissions", r.status_code == 200 and len(r.json()) >= 2, f"{r.status_code}"
    )
    perms = sorted(r.json(), key=lambda p: p["code"])[:2]
    grants_payload = [{"permission_code": p["code"], "scope": p["default_scope"]} for p in perms]

    role_code = f"CRUD-ROLE-{suffix}".upper()
    r = c.post(
        "/api/v1/roles",
        headers=h,
        json={"code": role_code, "name": "Crud Test Role", "grants": grants_payload},
    )
    _require(
        log,
        "create a custom role from two permissions",
        r.status_code == 201,
        f"{r.status_code} {r.text[:200]}",
    )
    role = r.json()

    r = c.put(
        f"/api/v1/roles/{role['id']}",
        headers=h,
        json={"code": role_code, "name": "Crud Test Role (renamed)", "grants": grants_payload},
    )
    _require(
        log,
        "rename it",
        r.status_code == 200 and r.json().get("name") == "Crud Test Role (renamed)",
        f"{r.status_code} {r.text[:200]}",
    )

    r = c.delete(f"/api/v1/roles/{role['id']}", headers=h)
    _require(log, "delete it", r.status_code == 204, f"{r.status_code}")

    r = c.post(
        "/api/v1/org-units", headers=h, json={"name": "Engineering", "code": f"ENG-{suffix}"}
    )
    _require(log, "create an org unit", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    unit = r.json()

    r = c.patch(
        f"/api/v1/org-units/{unit['id']}", headers=h, json={"name": "Engineering (renamed)"}
    )
    _require(
        log,
        "PATCH org unit",
        r.status_code == 200 and r.json().get("name") == "Engineering (renamed)",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.patch(f"/api/v1/org-units/{unit['id']}", headers=h, json={"is_active": False})
    _require(
        log,
        "deactivate org unit",
        r.status_code == 200 and r.json().get("is_active") is False,
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post("/api/v1/locations", headers=h, json={"name": "Head Office", "code": f"HQ-{suffix}"})
    _require(log, "create a location", r.status_code == 201, f"{r.status_code} {r.text[:160]}")
    location = r.json()

    r = c.patch(
        f"/api/v1/locations/{location['id']}", headers=h, json={"name": "Head Office (renamed)"}
    )
    _require(
        log,
        "PATCH location",
        r.status_code == 200 and r.json().get("name") == "Head Office (renamed)",
        f"{r.status_code} {r.text[:160]}",
    )

    r = c.post(
        "/api/v1/invitations",
        headers=h,
        json={"email": f"invitee-{suffix}@example.com", "role_codes": []},
    )
    _require(
        log,
        "POST /invitations returns a token once",
        r.status_code == 201 and bool(r.json().get("token")),
        f"{r.status_code} {r.text[:160]}",
    )
    invitation = r.json()

    r = c.delete(f"/api/v1/invitations/{invitation['id']}", headers=h)
    _require(log, "revoke it", r.status_code == 204, f"{r.status_code}")


FLOWS: dict[str, Callable[[httpx.Client, StepLog, dict[str, str], str], None]] = {
    "operations": flow_operations,
    "hrms": flow_hrms,
    "crm": flow_crm,
    "pulseboard": flow_pulseboard,
    "projects": flow_projects,
    "support": flow_support,
    "marketing": flow_marketing,
    "finance": flow_finance,
    "itsm": flow_itsm,
    "storefront": flow_storefront,
    "core": flow_core,
}


def run_product(key: str) -> StepLog:
    log = StepLog()
    run_id = time.strftime("%H%M%S")
    slug = f"crud-{key}-{run_id}-{uuid.uuid4().hex[:4]}"
    email = f"{slug}@example.com"
    password = f"Crud-{uuid.uuid4().hex[:14]}!"
    print(f"\n=== {key} (org {slug}, {email}) ===")

    c = PatientClient(base_url=API, timeout=30)
    try:
        product_key = None if key == "core" else key
        h = bootstrap_owner(
            c,
            log,
            key=product_key,
            slug=slug,
            email=email,
            password=password,
            org_name=f"Crud {key} {slug}",
        )
        if h is not None:
            FLOWS[key](c, log, h, slug)
    except Abort:
        pass
    except httpx.HTTPError as exc:
        log.step("unexpected transport error talking to the gateway", False, str(exc))
    finally:
        c.close()

    passed = log.passed_count
    total = len(log.results)
    print(f"{passed}/{total} steps passed for {key}")
    return log


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--product", choices=sorted(FLOWS), help="run one product's CRUD flow")
    group.add_argument("--all", action="store_true", help="run every product's CRUD flow, in order")
    args = parser.parse_args()

    keys = sorted(FLOWS) if args.all else [args.product]

    logs: dict[str, StepLog] = {}
    for key in keys:
        logs[key] = run_product(key)

    print("\n=== summary ===")
    width = max(len(k) for k in keys)
    failed_products = []
    for key in keys:
        log = logs[key]
        status = "PASS" if log.ok else "FAIL"
        print(f"{status}  {key:<{width}}  {log.passed_count}/{len(log.results)} steps")
        if not log.ok:
            failed_products.append(key)
            for name, _ok, detail in log.failed:
                print(f"       - {name}  {detail}")

    sys.exit(1 if failed_products else 0)


if __name__ == "__main__":
    main()
