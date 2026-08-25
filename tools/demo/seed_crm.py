"""Seed the CRM for the demo org, mirroring the SPA fixtures — across BOTH services.

CRM spans two services: customer-service owns the account book (owners, companies,
contacts) and deal-service owns the pipeline (deals, the activity timeline). Every service
exposes a top-level `app`, so one process can import only one of them; run as a script,
this file therefore re-invokes itself once per service (`--child`), each child running
inside that service's directory exactly as the other seeders do.

The rows cross-link across the schema boundary by **deterministic ids**: every fixture key
("own-1", "comp-3", "con-7", "deal-12") maps to `uuid5(NAMESPACE_URL,
"atmora-demo-crm:<org>:<key>")`, so the deal child derives the same company / contact /
owner UUIDs the customer child inserted — a plain UUID reference, never a cross-schema
foreign key (ADR-0000 §10).

Each child mirrors `tests/conftest.py` of its service: it connects as the NOBYPASSRLS app
role and binds the tenant GUC on every transaction (the GUC is transaction-local), so
writes go through the same row-level-security path a real request does, and through the
real `services.*` write paths — owners, companies, contacts, deals and activities all land
with audit rows and outbox events, exactly as the live product produces them. Logging each
activity also advances its deal's `last_activity_at`; the fixture timestamps drive the
staleness clock to the exact values the approved screens show.

The dataset is the SPA's `frontend/product-crm/src/api/fixtures.ts`, one-for-one: the same
6 owners, 15 companies, 18 contacts, 18 deals across all six stages and 20 timeline
activities, so the live screens look like the fixtures the user already approved. All
money is integer minor units (paise) of INR. The demo clock the fixtures are written
against is 2026-08-24.

Idempotent: a row whose deterministic id already exists is skipped, so re-running never
duplicates.

Usage: seed_crm.py <repo_root> <org_id> <admin_user_id> <admin_membership_id>
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import sys
import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from platform_core.auth import ScopeContext


def demo_id(org_id: uuid.UUID, key: str) -> uuid.UUID:
    """The deterministic id for a fixture key — the same on both sides of the schema
    boundary, so the pipeline's plain-UUID references land on the book's rows."""
    return uuid.uuid5(uuid.NAMESPACE_URL, f"atmora-demo-crm:{org_id}:{key}")


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def _date(s: str) -> date:
    return date.fromisoformat(s)


def rupees(n: int) -> int:
    """Rupees -> paise (minor units), matching the fixtures' `rupees()` helper."""
    return n * 100


# ------------------------------------------------------------------ owners (id, name, email, role)
OWNERS: list[tuple[str, str, str, str]] = [
    ("own-1", "Sofia Marchetti", "sofia.marchetti@atmora.example.com", "Regional Sales Head"),
    ("own-2", "Vikram Singh", "vikram.singh@atmora.example.com", "Account Executive"),
    ("own-3", "Grace Adeyemi", "grace.adeyemi@atmora.example.com", "Sales Development Rep"),
    ("own-4", "Kabir Malhotra", "kabir.malhotra@atmora.example.com", "Enterprise AE"),
    ("own-5", "Emily Carter", "emily.carter@atmora.example.com", "Solutions Consultant"),
    ("own-6", "Rahul Deshpande", "rahul.deshpande@atmora.example.com", "Account Manager"),
]

# --------------------------------------------- companies
# (id, name, domain, industry, size_band, city, country, status, owner, revenue_rupees, created_at)
COMPANIES: list[tuple[Any, ...]] = [
    (
        "comp-1",
        "Northwind Logistics",
        "northwind.co",
        "Logistics",
        "501–1000",
        "Mumbai",
        "India",
        "customer",
        "own-1",
        8_400_000_000,
        "2024-02-11T09:00:00Z",
    ),
    (
        "comp-2",
        "Meridian Steelworks",
        "meridiansteel.com",
        "Manufacturing",
        "1000+",
        "Jamshedpur",
        "India",
        "customer",
        "own-4",
        21_000_000_000,
        "2023-11-03T09:00:00Z",
    ),
    (
        "comp-3",
        "Cobalt Financial",
        "cobaltfin.com",
        "Financial Services",
        "201–500",
        "Bengaluru",
        "India",
        "prospect",
        "own-2",
        5_600_000_000,
        "2025-01-22T09:00:00Z",
    ),
    (
        "comp-4",
        "Vertex Robotics",
        "vertexrobotics.io",
        "Industrial Automation",
        "51–200",
        "Pune",
        "India",
        "prospect",
        "own-5",
        1_800_000_000,
        "2025-04-08T09:00:00Z",
    ),
    (
        "comp-5",
        "Harbor & Co Retail",
        "harborandco.com",
        "Retail",
        "201–500",
        "Delhi",
        "India",
        "customer",
        "own-6",
        4_300_000_000,
        "2024-06-19T09:00:00Z",
    ),
    (
        "comp-6",
        "Aurora Health Systems",
        "aurorahealth.in",
        "Healthcare",
        "501–1000",
        "Hyderabad",
        "India",
        "prospect",
        "own-1",
        6_900_000_000,
        "2024-09-30T09:00:00Z",
    ),
    (
        "comp-7",
        "Ironclad Manufacturing",
        "ironcladmfg.com",
        "Manufacturing",
        "201–500",
        "Coimbatore",
        "India",
        "lead",
        "own-3",
        3_100_000_000,
        "2025-06-14T09:00:00Z",
    ),
    (
        "comp-8",
        "Solstice Energy",
        "solsticeenergy.com",
        "Energy",
        "1000+",
        "Ahmedabad",
        "India",
        "customer",
        "own-4",
        17_500_000_000,
        "2023-08-27T09:00:00Z",
    ),
    (
        "comp-9",
        "Beacon Media Group",
        "beaconmedia.co",
        "Media",
        "51–200",
        "Mumbai",
        "India",
        "lead",
        "own-3",
        950_000_000,
        "2025-05-02T09:00:00Z",
    ),
    (
        "comp-10",
        "Cascade Foods",
        "cascadefoods.in",
        "FMCG",
        "501–1000",
        "Nagpur",
        "India",
        "prospect",
        "own-2",
        5_200_000_000,
        "2024-12-05T09:00:00Z",
    ),
    (
        "comp-11",
        "Quantic Software",
        "quantic.dev",
        "Software",
        "51–200",
        "Bengaluru",
        "India",
        "customer",
        "own-5",
        1_400_000_000,
        "2024-03-18T09:00:00Z",
    ),
    (
        "comp-12",
        "Pinnacle Realty",
        "pinnaclerealty.in",
        "Real Estate",
        "201–500",
        "Gurugram",
        "India",
        "churned",
        "own-6",
        3_800_000_000,
        "2023-10-11T09:00:00Z",
    ),
    (
        "comp-13",
        "Delta Freight Lines",
        "deltafreight.com",
        "Logistics",
        "501–1000",
        "Chennai",
        "India",
        "prospect",
        "own-1",
        6_100_000_000,
        "2025-02-27T09:00:00Z",
    ),
    (
        "comp-14",
        "Umbra Analytics",
        "umbra.ai",
        "Software",
        "11–50",
        "Pune",
        "India",
        "lead",
        "own-3",
        480_000_000,
        "2025-07-01T09:00:00Z",
    ),
    (
        "comp-15",
        "Riverstone Textiles",
        "riverstone.co",
        "Textiles",
        "201–500",
        "Surat",
        "India",
        "prospect",
        "own-2",
        2_700_000_000,
        "2024-08-09T09:00:00Z",
    ),
]

# --------------------------------------------- contacts
# (id, name, title, email, phone_masked, company, status, owner, last_activity_at, created_at)
CONTACTS: list[tuple[Any, ...]] = [
    (
        "con-1",
        "Rajesh Khanna",
        "VP Operations",
        "rajesh.khanna@northwind.co",
        "+91 98••• ••210",
        "comp-1",
        "customer",
        "own-1",
        "2026-08-22T11:20:00Z",
        "2024-02-12T09:00:00Z",
    ),
    (
        "con-2",
        "Anita Desai",
        "Warehouse Director",
        "anita.desai@northwind.co",
        "+91 99••• ••044",
        "comp-1",
        "customer",
        "own-1",
        "2026-08-04T15:05:00Z",
        "2024-04-01T09:00:00Z",
    ),
    (
        "con-3",
        "Suresh Iyer",
        "Chief Information Officer",
        "suresh.iyer@meridiansteel.com",
        "+91 98••• ••771",
        "comp-2",
        "customer",
        "own-4",
        "2026-08-18T10:40:00Z",
        "2023-11-05T09:00:00Z",
    ),
    (
        "con-4",
        "Neha Kapoor",
        "Head of Risk",
        "neha.kapoor@cobaltfin.com",
        "+91 90••• ••318",
        "comp-3",
        "qualified",
        "own-2",
        "2026-08-20T09:15:00Z",
        "2025-01-24T09:00:00Z",
    ),
    (
        "con-5",
        "Arjun Rao",
        "Director of Engineering",
        "arjun.rao@vertexrobotics.io",
        "+91 97••• ••562",
        "comp-4",
        "lead",
        "own-5",
        "2026-08-05T13:30:00Z",
        "2025-04-10T09:00:00Z",
    ),
    (
        "con-6",
        "Meredith Shah",
        "Retail Operations Lead",
        "meredith.shah@harborandco.com",
        "+91 98••• ••129",
        "comp-5",
        "customer",
        "own-6",
        "2026-08-12T16:00:00Z",
        "2024-06-21T09:00:00Z",
    ),
    (
        "con-7",
        "Kavya Menon",
        "Chief Medical Officer",
        "kavya.menon@aurorahealth.in",
        "+91 99••• ••487",
        "comp-6",
        "qualified",
        "own-1",
        "2026-08-23T08:50:00Z",
        "2024-10-02T09:00:00Z",
    ),
    (
        "con-8",
        "Prakash Bhatt",
        "Plant Manager",
        "prakash.bhatt@ironcladmfg.com",
        "+91 96••• ••903",
        "comp-7",
        "lead",
        "own-3",
        "2026-07-30T12:10:00Z",
        "2025-06-16T09:00:00Z",
    ),
    (
        "con-9",
        "Ritu Agarwal",
        "Head of Grid Operations",
        "ritu.agarwal@solsticeenergy.com",
        "+91 98••• ••655",
        "comp-8",
        "customer",
        "own-4",
        "2026-08-23T14:25:00Z",
        "2023-08-29T09:00:00Z",
    ),
    (
        "con-10",
        "Sameer Joshi",
        "Ad Ops Manager",
        "sameer.joshi@beaconmedia.co",
        "+91 90••• ••240",
        "comp-9",
        "lead",
        "own-3",
        "2026-08-19T10:05:00Z",
        "2025-05-04T09:00:00Z",
    ),
    (
        "con-11",
        "Deepak Verma",
        "National Sales Head",
        "deepak.verma@cascadefoods.in",
        "+91 97••• ••817",
        "comp-10",
        "qualified",
        "own-2",
        "2026-08-06T11:45:00Z",
        "2024-12-07T09:00:00Z",
    ),
    (
        "con-12",
        "Lisa Fernandes",
        "VP Product",
        "lisa.fernandes@quantic.dev",
        "+91 98••• ••390",
        "comp-11",
        "customer",
        "own-5",
        "2026-08-24T07:35:00Z",
        "2024-03-20T09:00:00Z",
    ),
    (
        "con-13",
        "Manish Gupta",
        "Sales Director",
        "manish.gupta@pinnaclerealty.in",
        "+91 99••• ••512",
        "comp-12",
        "dormant",
        "own-6",
        "2026-07-28T09:00:00Z",
        "2023-10-13T09:00:00Z",
    ),
    (
        "con-14",
        "Farhan Ali",
        "Logistics Head",
        "farhan.ali@deltafreight.com",
        "+91 96••• ••178",
        "comp-13",
        "qualified",
        "own-1",
        "2026-08-17T15:20:00Z",
        "2025-03-01T09:00:00Z",
    ),
    (
        "con-15",
        "Tara Krishnan",
        "Founder & CEO",
        "tara.krishnan@umbra.ai",
        "+91 90••• ••006",
        "comp-14",
        "lead",
        "own-3",
        "2026-08-11T10:00:00Z",
        "2025-07-03T09:00:00Z",
    ),
    (
        "con-16",
        "Gopal Shah",
        "Operations Head",
        "gopal.shah@riverstone.co",
        "+91 98••• ••441",
        "comp-15",
        "qualified",
        "own-2",
        "2026-08-15T12:30:00Z",
        "2024-08-11T09:00:00Z",
    ),
    (
        "con-17",
        "Vivek Nambiar",
        "IT Director",
        "vivek.nambiar@aurorahealth.in",
        "+91 97••• ••328",
        "comp-6",
        "qualified",
        "own-1",
        "2026-08-21T09:40:00Z",
        "2025-01-15T09:00:00Z",
    ),
    (
        "con-18",
        "Priyanka Sethi",
        "Procurement Lead",
        "priyanka.sethi@solsticeenergy.com",
        "+91 99••• ••760",
        "comp-8",
        "customer",
        "own-4",
        "2026-08-10T13:15:00Z",
        "2024-05-19T09:00:00Z",
    ),
]

# --------------------------------------------- deals
# (id, name, company, contact, value_rupees, stage, owner, probability, close_date,
#  created_at, last_activity_at, source)
DEALS: list[tuple[Any, ...]] = [
    (
        "deal-1",
        "Fleet telematics rollout",
        "comp-1",
        "con-1",
        32_000_000,
        "negotiation",
        "own-1",
        70,
        "2026-08-29",
        "2026-05-14T09:00:00Z",
        "2026-08-22T11:20:00Z",
        "Referral",
    ),
    (
        "deal-2",
        "Plant-wide ERP integration",
        "comp-2",
        "con-3",
        45_000_000,
        "proposal",
        "own-4",
        55,
        "2026-09-30",
        "2026-06-02T09:00:00Z",
        "2026-08-18T10:40:00Z",
        "Outbound",
    ),
    (
        "deal-3",
        "Risk analytics platform",
        "comp-3",
        "con-4",
        18_000_000,
        "qualified",
        "own-2",
        40,
        "2026-10-15",
        "2026-07-09T09:00:00Z",
        "2026-08-20T09:15:00Z",
        "Inbound",
    ),
    (
        "deal-4",
        "Automation pilot",
        "comp-4",
        "con-5",
        6_500_000,
        "lead",
        "own-5",
        20,
        "2026-11-10",
        "2026-07-28T09:00:00Z",
        "2026-08-05T13:30:00Z",
        "Event",
    ),
    (
        "deal-5",
        "Omnichannel CRM",
        "comp-5",
        "con-6",
        21_000_000,
        "closed_won",
        "own-6",
        100,
        "2026-08-12",
        "2026-04-18T09:00:00Z",
        "2026-08-12T16:00:00Z",
        "Referral",
    ),
    (
        "deal-6",
        "Patient engagement suite",
        "comp-6",
        "con-7",
        27_500_000,
        "proposal",
        "own-1",
        60,
        "2026-09-05",
        "2026-06-11T09:00:00Z",
        "2026-08-23T08:50:00Z",
        "Outbound",
    ),
    (
        "deal-7",
        "Shop-floor visibility",
        "comp-7",
        "con-8",
        4_800_000,
        "lead",
        "own-3",
        15,
        "2026-10-28",
        "2026-07-15T09:00:00Z",
        "2026-07-30T12:10:00Z",
        "Cold call",
    ),
    (
        "deal-8",
        "Grid asset monitoring",
        "comp-8",
        "con-9",
        39_000_000,
        "negotiation",
        "own-4",
        75,
        "2026-08-27",
        "2026-05-30T09:00:00Z",
        "2026-08-23T14:25:00Z",
        "Partner",
    ),
    (
        "deal-9",
        "Ad ops workspace",
        "comp-9",
        "con-10",
        3_200_000,
        "qualified",
        "own-3",
        35,
        "2026-09-20",
        "2026-07-21T09:00:00Z",
        "2026-08-19T10:05:00Z",
        "Inbound",
    ),
    (
        "deal-10",
        "Distributor portal",
        "comp-10",
        "con-11",
        14_500_000,
        "proposal",
        "own-2",
        50,
        "2026-08-20",
        "2026-06-08T09:00:00Z",
        "2026-08-06T11:45:00Z",
        "Outbound",
    ),
    (
        "deal-11",
        "Renewal + expansion",
        "comp-11",
        "con-12",
        8_800_000,
        "negotiation",
        "own-5",
        80,
        "2026-09-02",
        "2026-06-25T09:00:00Z",
        "2026-08-24T07:35:00Z",
        "Renewal",
    ),
    (
        "deal-12",
        "Sales enablement",
        "comp-12",
        "con-13",
        7_200_000,
        "closed_lost",
        "own-6",
        0,
        "2026-07-28",
        "2026-04-02T09:00:00Z",
        "2026-07-28T09:00:00Z",
        "Outbound",
    ),
    (
        "deal-13",
        "Route optimization",
        "comp-13",
        "con-14",
        16_000_000,
        "qualified",
        "own-1",
        45,
        "2026-10-08",
        "2026-07-04T09:00:00Z",
        "2026-08-17T15:20:00Z",
        "Referral",
    ),
    (
        "deal-14",
        "Analytics starter",
        "comp-14",
        "con-15",
        2_400_000,
        "lead",
        "own-3",
        15,
        "2026-11-18",
        "2026-07-30T09:00:00Z",
        "2026-08-11T10:00:00Z",
        "Event",
    ),
    (
        "deal-15",
        "Order management",
        "comp-15",
        "con-16",
        11_500_000,
        "proposal",
        "own-2",
        50,
        "2026-09-12",
        "2026-06-19T09:00:00Z",
        "2026-08-15T12:30:00Z",
        "Inbound",
    ),
    (
        "deal-16",
        "Warehouse add-on",
        "comp-1",
        "con-2",
        9_500_000,
        "closed_won",
        "own-1",
        100,
        "2026-08-04",
        "2026-05-01T09:00:00Z",
        "2026-08-04T15:05:00Z",
        "Expansion",
    ),
    (
        "deal-17",
        "Predictive maintenance",
        "comp-2",
        "con-3",
        13_500_000,
        "lead",
        "own-4",
        20,
        "2026-11-05",
        "2026-07-12T09:00:00Z",
        "2026-07-26T10:40:00Z",
        "Outbound",
    ),
    (
        "deal-18",
        "Telehealth module",
        "comp-6",
        "con-17",
        20_500_000,
        "negotiation",
        "own-1",
        65,
        "2026-09-08",
        "2026-06-16T09:00:00Z",
        "2026-08-23T09:40:00Z",
        "Expansion",
    ),
]

# --------------------------------------------- activities
# (id, type, subject, body, deal, company, contact, owner, occurred_at, direction)
ACTIVITIES: list[tuple[Any, ...]] = [
    (
        "act-1",
        "call",
        "Discovery call on telematics scope",
        "Walked through fleet size and current tracking gaps. Rajesh wants a 30-vehicle pilot first.",
        "deal-1",
        "comp-1",
        "con-1",
        "own-1",
        "2026-08-22T11:20:00Z",
        "outbound",
    ),
    (
        "act-2",
        "email",
        "Sent revised commercial proposal",
        "Shared the tiered pricing with an annual commitment option.",
        "deal-8",
        "comp-8",
        "con-9",
        "own-4",
        "2026-08-23T14:25:00Z",
        "outbound",
    ),
    (
        "act-3",
        "meeting",
        "Solution demo with clinical team",
        "Demoed patient engagement flows to Dr. Menon and two department heads.",
        "deal-6",
        "comp-6",
        "con-7",
        "own-1",
        "2026-08-23T08:50:00Z",
        None,
    ),
    (
        "act-4",
        "email",
        "Renewal terms confirmed",
        "Lisa approved the expansion seats; contract moving to procurement.",
        "deal-11",
        "comp-11",
        "con-12",
        "own-5",
        "2026-08-24T07:35:00Z",
        "inbound",
    ),
    (
        "act-5",
        "call",
        "Qualification call — risk platform",
        "Neha outlined the compliance requirements. Budget approved for Q4.",
        "deal-3",
        "comp-3",
        "con-4",
        "own-2",
        "2026-08-20T09:15:00Z",
        "outbound",
    ),
    (
        "act-6",
        "note",
        "Champion identified at IT",
        "Vivek is advocating internally; loop him into the next telehealth review.",
        "deal-18",
        "comp-6",
        "con-17",
        "own-1",
        "2026-08-21T09:40:00Z",
        None,
    ),
    (
        "act-7",
        "meeting",
        "Onsite ERP workshop",
        "Mapped the integration touchpoints with the plant IT team.",
        "deal-2",
        "comp-2",
        "con-3",
        "own-4",
        "2026-08-18T10:40:00Z",
        None,
    ),
    (
        "act-8",
        "call",
        "Follow-up on distributor portal",
        "Left a voicemail; awaiting Deepak's feedback on the rollout timeline.",
        "deal-10",
        "comp-10",
        "con-11",
        "own-2",
        "2026-08-06T11:45:00Z",
        "outbound",
    ),
    (
        "act-9",
        "email",
        "Case study shared",
        "Sent the Riverstone order-management case study to Gopal.",
        "deal-15",
        "comp-15",
        "con-16",
        "own-2",
        "2026-08-15T12:30:00Z",
        "outbound",
    ),
    (
        "act-10",
        "task",
        "Prepare security questionnaire",
        "Aurora infosec review due before contract; assemble SOC 2 evidence.",
        "deal-6",
        "comp-6",
        None,
        "own-1",
        "2026-08-19T16:00:00Z",
        None,
    ),
    (
        "act-11",
        "meeting",
        "Route optimization scoping",
        "Farhan walked us through the depot network and current planning tools.",
        "deal-13",
        "comp-13",
        "con-14",
        "own-1",
        "2026-08-17T15:20:00Z",
        None,
    ),
    (
        "act-12",
        "call",
        "Intro call — Beacon ad ops",
        "Sameer interested in the workspace; small team, quick decision cycle.",
        "deal-9",
        "comp-9",
        "con-10",
        "own-3",
        "2026-08-19T10:05:00Z",
        "inbound",
    ),
    (
        "act-13",
        "email",
        "Pilot recap and next steps",
        "Summarized the automation pilot outcomes; proposed a phase-2 plan.",
        "deal-4",
        "comp-4",
        "con-5",
        "own-5",
        "2026-08-05T13:30:00Z",
        "outbound",
    ),
    (
        "act-14",
        "note",
        "Deal lost — budget frozen",
        "Pinnacle paused all software spend after a leadership change. Revisit in 2027.",
        "deal-12",
        "comp-12",
        "con-13",
        "own-6",
        "2026-07-28T09:00:00Z",
        None,
    ),
    (
        "act-15",
        "meeting",
        "Kickoff — Harbor & Co go-live",
        "Aligned on rollout milestones with the retail ops team. Contract signed.",
        "deal-5",
        "comp-5",
        "con-6",
        "own-6",
        "2026-08-12T16:00:00Z",
        None,
    ),
    (
        "act-16",
        "call",
        "Discovery — predictive maintenance",
        "Early interest; Suresh wants an ROI model before committing time.",
        "deal-17",
        "comp-2",
        "con-3",
        "own-4",
        "2026-07-26T10:40:00Z",
        "outbound",
    ),
    (
        "act-17",
        "email",
        "Founder intro — Umbra",
        "Tara replied to the event follow-up; scheduling a product walkthrough.",
        "deal-14",
        "comp-14",
        "con-15",
        "own-3",
        "2026-08-11T10:00:00Z",
        "inbound",
    ),
    (
        "act-18",
        "call",
        "Warehouse add-on confirmation",
        "Anita confirmed the add-on scope; order processed.",
        "deal-16",
        "comp-1",
        "con-2",
        "own-1",
        "2026-08-04T15:05:00Z",
        "outbound",
    ),
    (
        "act-19",
        "task",
        "Send MSA redlines to legal",
        "Solstice legal returned redlines on liability caps; route to our counsel.",
        "deal-8",
        "comp-8",
        "con-18",
        "own-4",
        "2026-08-10T13:15:00Z",
        None,
    ),
    (
        "act-20",
        "note",
        "Shop-floor pilot cooling off",
        "No response from Prakash in three weeks; try a different stakeholder.",
        "deal-7",
        "comp-7",
        "con-8",
        "own-3",
        "2026-07-30T12:10:00Z",
        None,
    ),
]


# ------------------------------------------------------------------ the two seeders


async def seed_customer(session: AsyncSession, ctx: ScopeContext) -> dict[str, int]:
    """Insert the account-book half (owners, companies, contacts) through customer-service's
    real write paths. Idempotent. Importable and side-effect-free at module load, so the
    service's tests can drive it directly; runs inside a transaction the caller opens —
    and only inside customer-service's process."""
    from app import selectors, services  # type: ignore[import-not-found]

    org = ctx.organization_id
    counts = {"owners": 0, "companies": 0, "contacts": 0, "skipped": 0}

    for key, full_name, email, role in OWNERS:
        if await selectors.get_owner_by_email(session, org, email):
            counts["skipped"] += 1
            continue
        await services.create_owner(
            session, ctx, full_name=full_name, email=email, role=role, owner_id=demo_id(org, key)
        )
        counts["owners"] += 1

    for (
        key,
        name,
        domain,
        industry,
        band,
        city,
        country,
        status,
        owner,
        revenue,
        created,
    ) in COMPANIES:
        if await selectors.get_company(session, org, demo_id(org, key)):
            counts["skipped"] += 1
            continue
        await services.create_company(
            session,
            ctx,
            name=name,
            domain=domain,
            industry=industry,
            size_band=band,
            city=city,
            country=country,
            status=status,
            owner_id=demo_id(org, owner),
            annual_revenue_minor=rupees(revenue),
            currency="INR",
            company_id=demo_id(org, key),
            created_at=_dt(created),
        )
        counts["companies"] += 1

    for key, full_name, title, email, phone, company, status, owner, last_at, created in CONTACTS:
        if await selectors.get_contact(session, org, demo_id(org, key)):
            counts["skipped"] += 1
            continue
        await services.create_contact(
            session,
            ctx,
            full_name=full_name,
            title=title,
            email=email,
            phone=phone,
            company_id=demo_id(org, company),
            status=status,
            owner_id=demo_id(org, owner),
            last_activity_at=_dt(last_at),
            contact_id=demo_id(org, key),
            created_at=_dt(created),
        )
        counts["contacts"] += 1

    return counts


async def seed_deal(session: AsyncSession, ctx: ScopeContext) -> dict[str, int]:
    """Insert the pipeline half (deals, activities) through deal-service's real write
    paths. Idempotent, and only inside deal-service's process. Each deal's
    `last_activity_at` is seeded verbatim from the fixtures — the 20-entry timeline is a
    sample of the touches, not all of them (deal-18's latest touch is off-timeline) — and
    `log_activity`'s monotonic bookkeeping then never winds the clock backwards."""
    from app import selectors, services

    org = ctx.organization_id
    counts = {"deals": 0, "activities": 0, "skipped": 0}

    for (
        key,
        name,
        company,
        contact,
        value,
        stage,
        owner,
        prob,
        close,
        created,
        last_at,
        source,
    ) in DEALS:
        if await selectors.get_deal(session, org, demo_id(org, key)):
            counts["skipped"] += 1
            continue
        await services.create_deal(
            session,
            ctx,
            name=name,
            company_id=demo_id(org, company),
            primary_contact_id=demo_id(org, contact),
            value_minor=rupees(value),
            currency="INR",
            stage=stage,
            owner_id=demo_id(org, owner),
            probability=prob,
            close_date=_date(close),
            source=source,
            # Seeded verbatim: the fixture timeline is a sample of the touches, so the
            # clock cannot be reconstructed from it alone; log_activity only ever advances.
            last_activity_at=_dt(last_at),
            deal_id=demo_id(org, key),
            created_at=_dt(created),
        )
        counts["deals"] += 1

    for key, kind, subject, body, deal, company, contact, owner, occurred, direction in ACTIVITIES:
        activity_id = demo_id(org, key)
        if await _activity_exists(session, org, activity_id):
            counts["skipped"] += 1
            continue
        await services.log_activity(
            session,
            ctx,
            activity_type=kind,
            subject=subject,
            body=body,
            deal_id=demo_id(org, deal),
            company_id=demo_id(org, company),
            contact_id=demo_id(org, contact) if contact else None,
            owner_id=demo_id(org, owner),
            occurred_at=_dt(occurred),
            direction=direction,
            activity_id=activity_id,
        )
        counts["activities"] += 1

    return counts


async def _activity_exists(session: AsyncSession, org: uuid.UUID, activity_id: uuid.UUID) -> bool:
    from sqlalchemy import select

    from app.models import Activity  # type: ignore[import-not-found]

    stmt = select(Activity.id).where(Activity.id == activity_id, Activity.organization_id == org)
    return (await session.execute(stmt)).scalar_one_or_none() is not None


# ------------------------------------------------------------------ standalone entry points


async def _child(
    service: str, org_id: uuid.UUID, admin_user_id: uuid.UUID, admin_membership_id: uuid.UUID
) -> None:
    """Run one service's half. The service directory is already on sys.path."""
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]
    from app.models import SCHEMA
    from platform_core.auth import Principal, Scope, ScopeContext
    from platform_core.db import TENANT_GUC

    permission = "crm.book.company.write" if service == "customer" else "crm.pipeline.deal.write"
    cfg = settings()
    engine = create_async_engine(
        cfg.app_dsn, connect_args={"server_settings": {"search_path": f"{SCHEMA},public"}}
    )
    maker = async_sessionmaker(engine, expire_on_commit=False)

    ctx = ScopeContext(
        principal=Principal(
            sub=admin_user_id,
            organization_id=org_id,
            membership_id=admin_membership_id,
            perms=[f"{permission}:ORG"],
            entitlements=["crm"],
        ),
        scope=Scope.ORG,
        permission=permission,
    )

    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        async with session.begin():
            seeder = seed_customer if service == "customer" else seed_deal
            counts = await seeder(session, ctx)

    await engine.dispose()
    print(json.dumps({service: counts}))


def _run_child() -> None:
    """`--child <service> <service_dir> <org> <user> <membership>` — the per-process half."""
    from pathlib import Path

    service = sys.argv[2]
    service_dir = Path(sys.argv[3]).resolve()
    sys.path.insert(0, str(service_dir))
    asyncio.run(
        _child(service, uuid.UUID(sys.argv[4]), uuid.UUID(sys.argv[5]), uuid.UUID(sys.argv[6]))
    )


def _run_parent() -> None:
    """`<repo_root> <org> <user> <membership>` — one subprocess per service, book first so
    the pipeline's cross-schema references land on rows that exist."""
    from pathlib import Path

    root = Path(sys.argv[1]).resolve()
    org, user, membership = sys.argv[2], sys.argv[3], sys.argv[4]
    for service in ("customer", "deal"):
        service_dir = root / "services" / service
        subprocess.run(  # noqa: S603 - this interpreter, this file, paths we chose
            [sys.executable, __file__, "--child", service, str(service_dir), org, user, membership],
            cwd=service_dir,
            check=True,
        )


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--child":
        _run_child()
    else:
        _run_parent()
