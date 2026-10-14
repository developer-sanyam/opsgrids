"""Seed the ITSM service desk for the demo org, mirroring the SPA fixtures.

Run **inside `services/itsm`** as a subprocess (every service exposes a top-level `app`, so one
process can import only one of them — the same constraint the other seeders live under).

Mirrors `services/itsm/tests/conftest.py`: connects as the NOBYPASSRLS app role and binds the
tenant GUC on every transaction (the GUC is transaction-local), so writes go through the same
row-level-security path a real request does, and through the real `services.*` write paths — so
assets, catalog items, incidents (with their worklog threads) and requests all land with an audit
row and an outbox event, exactly as the live product produces them.

The dataset is the SPA's `frontend/product-itsm/src/api/fixtures.ts`, one-for-one: the same 18
assets, 10 catalog items, 16 incidents and 15 requests, cross-linked by asset tag and catalog
code, so the live screens look like the fixtures the user already approved.

Idempotent: a tag / code / number that already exists is skipped, so re-running never duplicates.

Usage: seed_itsm.py <itsm_service_dir> <org_id> <admin_user_id> <admin_membership_id>

The demo clock the fixtures are written against is 2026-08-24T18:00Z; SLA due times are derived
from each incident's minutes-remaining relative to that instant.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

from _common import bootstrap, date_from_iso, datetime_from_iso

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from platform_core.auth import ScopeContext

DEMO_NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)


# ------------------------------------------------------------------ assets (tag, name, type, ...)
# (tag, name, type, status, owner, location, serial, purchased_at, warranty_expires_at, cost_minor)
ASSETS: list[tuple[Any, ...]] = [
    (
        "AST-1001",
        'MacBook Pro 14" M3',
        "laptop",
        "in_use",
        "Rohan Mehta",
        "Pune",
        "C02XR3K9MD6T",
        "2024-04-18",
        "2027-04-17",
        21_900_000,
    ),
    (
        "AST-1002",
        "Dell Latitude 7440",
        "laptop",
        "in_use",
        "Priya Nair",
        "Bengaluru",
        "9QF4T2S",
        "2023-11-02",
        "2026-11-01",
        13_450_000,
    ),
    (
        "AST-1003",
        "Lenovo ThinkPad X1 Carbon",
        "laptop",
        "in_stock",
        None,
        "Bengaluru · IT store",
        "PF3RT8K1",
        "2025-06-30",
        "2028-06-29",
        15_200_000,
    ),
    (
        "AST-1004",
        "iPhone 15 Pro",
        "phone",
        "in_use",
        "Sofia Marchetti",
        "Mumbai",
        "F17GQ9L2N4",
        "2024-01-22",
        "2026-09-12",
        13_490_000,
    ),
    (
        "AST-1005",
        "Dell PowerEdge R760 (db-prod-01)",
        "server",
        "in_use",
        "Platform team",
        "Bengaluru · DC-1",
        "SVR76B2X",
        "2023-02-14",
        "2026-09-30",
        148_000_000,
    ),
    (
        "AST-1006",
        "Cisco Catalyst 9300 (core-sw-blr)",
        "network",
        "in_repair",
        "Network team",
        "Bengaluru · DC-1",
        "FCW2340L0GH",
        "2022-08-09",
        "2027-08-08",
        62_500_000,
    ),
    (
        "AST-1007",
        'LG UltraFine 27" 4K',
        "monitor",
        "in_use",
        "Daniel Okafor",
        "Bengaluru",
        "27UP850N",
        "2024-05-11",
        "2027-05-10",
        4_200_000,
    ),
    (
        "AST-1008",
        'MacBook Air 13" M2',
        "laptop",
        "in_repair",
        "Lucas Almeida",
        "Pune",
        "C02GH7X8Q1KL",
        "2025-06-02",
        "2028-06-01",
        11_490_000,
    ),
    (
        "AST-1009",
        "Fortinet FortiGate 100F",
        "network",
        "in_use",
        "Security team",
        "Bengaluru · DC-1",
        "FGT100F9271",
        "2024-03-01",
        "2027-02-28",
        39_800_000,
    ),
    (
        "AST-1010",
        "Adobe Creative Cloud (25 seats)",
        "license",
        "in_use",
        "Design team",
        "SaaS",
        "ADOBE-CC-2026",
        "2026-01-01",
        "2026-12-31",
        105_000_000,
    ),
    (
        "AST-1011",
        "Microsoft 365 E5 (180 seats)",
        "license",
        "in_use",
        "IT team",
        "SaaS",
        "M365-E5-2026",
        "2026-04-01",
        "2027-03-31",
        486_000_000,
    ),
    (
        "AST-1012",
        "HP EliteDesk 800 G6",
        "desktop",
        "retired",
        None,
        "Chennai · disposal",
        "8CG0342XYZ",
        "2020-07-15",
        "2023-07-14",
        6_800_000,
    ),
    (
        "AST-1013",
        'Samsung ViewFinity 32"',
        "monitor",
        "in_stock",
        None,
        "Bengaluru · IT store",
        "LS32B800",
        "2025-09-19",
        "2028-09-18",
        5_600_000,
    ),
    (
        "AST-1014",
        "iPhone 14",
        "phone",
        "in_stock",
        None,
        "Mumbai · IT store",
        "G09TQ2M7P3",
        "2023-10-05",
        "2026-10-04",
        7_190_000,
    ),
    (
        "AST-1015",
        "Dell PowerEdge R750 (app-prod-02)",
        "server",
        "in_use",
        "Platform team",
        "Hyderabad · DC-2",
        "SVR75A9Q",
        "2022-05-20",
        "2025-05-19",
        121_000_000,
    ),
    (
        "AST-1016",
        "Ubiquiti UniFi U6-Pro (office APs ×8)",
        "network",
        "in_use",
        "Network team",
        "Pune · office",
        "U6PRO-BATCH-8",
        "2024-09-30",
        "2026-09-29",
        3_040_000,
    ),
    (
        "AST-1017",
        'MacBook Pro 16" M3 Max',
        "laptop",
        "in_use",
        "Marcus Feng",
        "Bengaluru",
        "C02ZW1Q2MJ9H",
        "2024-08-08",
        "2027-08-07",
        32_900_000,
    ),
    (
        "AST-1018",
        "Atlassian Cloud (Jira + Confluence)",
        "license",
        "in_use",
        "Engineering team",
        "SaaS",
        "ATL-CLOUD-2026",
        "2026-02-01",
        "2027-01-31",
        88_000_000,
    ),
]

# ------------------------------------------------------------------ catalog (code, name, cat, ...)
CATALOG: list[tuple[Any, ...]] = [
    (
        "CAT-01",
        "New joiner onboarding",
        "onboarding",
        "Provision a laptop, create accounts, assign the role's baseline access and enroll MFA for a new hire.",
        3,
        12,
    ),
    (
        "CAT-02",
        "Employee offboarding",
        "offboarding",
        "Revoke all access, reclaim devices, archive the mailbox and transfer file ownership on a leaver's last day.",
        1,
        5,
    ),
    (
        "CAT-03",
        "Role change / internal transfer",
        "access",
        "Re-baseline access for a mover: remove the old team's entitlements and grant the new role's.",
        2,
        7,
    ),
    (
        "CAT-04",
        "VPN access",
        "access",
        "Grant remote-access VPN to a specified network segment, time-boxed for contractors.",
        1,
        18,
    ),
    (
        "CAT-05",
        "Laptop request",
        "hardware",
        "Issue a standard-build laptop from stock, imaged and enrolled in device management.",
        5,
        9,
    ),
    (
        "CAT-06",
        "Additional monitor",
        "hardware",
        "Provide an extra external display from stock.",
        3,
        6,
    ),
    (
        "CAT-07",
        "Adobe Creative Cloud license",
        "software",
        "Assign a Creative Cloud seat from the org's license pool.",
        2,
        8,
    ),
    (
        "CAT-08",
        "JetBrains license",
        "software",
        "Assign a JetBrains All Products Pack seat to a developer.",
        2,
        4,
    ),
    (
        "CAT-09",
        "Shared drive / group access",
        "access",
        "Add a member to a shared drive or security group, with the owner's approval.",
        1,
        15,
    ),
    (
        "CAT-10",
        "Mobile phone provisioning",
        "hardware",
        "Issue a corporate phone with a plan and enroll it in mobile device management.",
        4,
        3,
    ),
]

# ------------------------------------------------------------------ incidents
# Each: dict with fields + worklog list of (author, body, kind, at_iso).
INCIDENTS: list[dict[str, Any]] = [
    {
        "number": "INC-4821",
        "summary": "Bengaluru office internet completely down",
        "description": "The Bengaluru DC-1 core switch stopped forwarding traffic at 15:40. All wired and Wi-Fi connectivity in the Bengaluru office is offline, affecting ~120 staff. Failover to the secondary uplink did not engage.",
        "priority": "P1",
        "status": "in_progress",
        "category": "network",
        "assignee": "Aravind Pillai",
        "requester": "Priya Nair",
        "asset": "AST-1006",
        "sla_state": "breached",
        "sla_minutes_remaining": -38,
        "created_at": "2026-08-24T15:44:00Z",
        "updated_at": "2026-08-24T17:52:00Z",
        "worklog": [
            (
                "Priya Nair",
                "Raised as P1 — entire Bengaluru office has no connectivity.",
                "note",
                "2026-08-24T15:44:00Z",
            ),
            (
                "Service desk",
                "Auto-assigned to the Network team on-call.",
                "assignment",
                "2026-08-24T15:45:00Z",
            ),
            (
                "Aravind Pillai",
                "On the console. core-sw-blr (AST-1006) is not forwarding on the aggregation VLANs; failover link is administratively down. Investigating.",
                "note",
                "2026-08-24T16:10:00Z",
            ),
            ("Service desk", "SLA target of 2h breached at 17:44.", "sla", "2026-08-24T17:44:00Z"),
            (
                "Aravind Pillai",
                "RMA raised with Cisco; swinging traffic to the standby switch now. ETA 30 min.",
                "note",
                "2026-08-24T17:52:00Z",
            ),
        ],
    },
    {
        "number": "INC-4820",
        "summary": "Phishing campaign targeting Finance mailboxes",
        "description": "Multiple Finance staff reported a spoofed 'invoice overdue' email with a credential-harvesting link. Two users reported entering credentials before the link was blocked.",
        "priority": "P1",
        "status": "in_progress",
        "category": "security",
        "assignee": "Zoya Khan",
        "requester": "Rahul Deshpande",
        "asset": None,
        "sla_state": "at_risk",
        "sla_minutes_remaining": 24,
        "created_at": "2026-08-24T16:20:00Z",
        "updated_at": "2026-08-24T17:40:00Z",
        "worklog": [
            (
                "Rahul Deshpande",
                "Several of us got a fake invoice email. Two people may have clicked and signed in.",
                "note",
                "2026-08-24T16:20:00Z",
            ),
            (
                "Zoya Khan",
                "Confirmed phishing. Blocked the sender domain and the payload URL at the FortiGate. Forcing password resets + revoking sessions for the two affected accounts.",
                "note",
                "2026-08-24T16:48:00Z",
            ),
            (
                "Zoya Khan",
                "MFA re-enrollment triggered. Sweeping mailboxes for the message and purging.",
                "note",
                "2026-08-24T17:40:00Z",
            ),
        ],
    },
    {
        "number": "INC-4819",
        "summary": "Sales CRM login failing with 500 error",
        "description": "Sales users cannot sign in to the CRM SPA — the login call returns a 500. Started after the 14:00 gateway deploy.",
        "priority": "P2",
        "status": "in_progress",
        "category": "software",
        "assignee": "Nikhil Bose",
        "requester": "Vikram Singh",
        "asset": None,
        "sla_state": "on_track",
        "sla_minutes_remaining": 186,
        "created_at": "2026-08-24T14:32:00Z",
        "updated_at": "2026-08-24T17:05:00Z",
        "worklog": [
            (
                "Vikram Singh",
                "None of the sales team can log into the CRM. Getting a server error page.",
                "note",
                "2026-08-24T14:32:00Z",
            ),
            (
                "Nikhil Bose",
                "Correlates with the 14:00 gateway rollout. Auth service is healthy; the CRM's token exchange is throwing. Looping in the CRM team.",
                "note",
                "2026-08-24T15:15:00Z",
            ),
            (
                "Nikhil Bose",
                "Rollback of the gateway change staged; testing in preview.",
                "note",
                "2026-08-24T17:05:00Z",
            ),
        ],
    },
    {
        "number": "INC-4818",
        "summary": "MacBook Air won't boot after macOS update",
        "description": "Laptop AST-1008 shows a prohibitory symbol on boot following the overnight macOS update. User cannot start work.",
        "priority": "P2",
        "status": "new",
        "category": "hardware",
        "assignee": "Farah Naidu",
        "requester": "Lucas Almeida",
        "asset": "AST-1008",
        "sla_state": "on_track",
        "sla_minutes_remaining": 142,
        "created_at": "2026-08-24T16:58:00Z",
        "updated_at": "2026-08-24T16:58:00Z",
        "worklog": [
            (
                "Lucas Almeida",
                "My MacBook Air won't start after last night's update — just a crossed-out circle.",
                "note",
                "2026-08-24T16:58:00Z",
            ),
        ],
    },
    {
        "number": "INC-4817",
        "summary": "Outbound email delayed by 30+ minutes",
        "description": "Messages to external recipients are queuing for 30–45 minutes before delivery. Internal mail is unaffected.",
        "priority": "P2",
        "status": "in_progress",
        "category": "email",
        "assignee": "Aravind Pillai",
        "requester": "Emily Carter",
        "asset": None,
        "sla_state": "at_risk",
        "sla_minutes_remaining": 41,
        "created_at": "2026-08-24T15:05:00Z",
        "updated_at": "2026-08-24T17:20:00Z",
        "worklog": [
            (
                "Emily Carter",
                "Clients are saying my replies arrive 30+ minutes late.",
                "note",
                "2026-08-24T15:05:00Z",
            ),
            (
                "Aravind Pillai",
                "Outbound connector is throttling — the sending IP is on a grey-list. Requesting delisting and standing up a secondary connector.",
                "note",
                "2026-08-24T16:30:00Z",
            ),
        ],
    },
    {
        "number": "INC-4816",
        "summary": "VPN access for onboarding contractor",
        "description": "New contractor needs VPN access to the staging network for a two-week engagement.",
        "priority": "P3",
        "status": "resolved",
        "category": "access",
        "assignee": "Devansh Rao",
        "requester": "Tobias Berg",
        "asset": "AST-1009",
        "sla_state": "met",
        "sla_minutes_remaining": None,
        "created_at": "2026-08-23T09:12:00Z",
        "updated_at": "2026-08-23T13:40:00Z",
        "worklog": [
            (
                "Tobias Berg",
                "Please grant staging VPN to our new contractor for 2 weeks.",
                "note",
                "2026-08-23T09:12:00Z",
            ),
            (
                "Devansh Rao",
                "Time-boxed VPN profile issued on the FortiGate, expires 2026-09-06. Sent setup instructions.",
                "resolution",
                "2026-08-23T13:40:00Z",
            ),
        ],
    },
    {
        "number": "INC-4815",
        "summary": "Excel data-analysis add-in crashing on open",
        "description": "Finance users report Excel crashes when loading the internal reporting add-in after the latest Office channel update.",
        "priority": "P3",
        "status": "in_progress",
        "category": "software",
        "assignee": "Nikhil Bose",
        "requester": "Lena Novak",
        "asset": "AST-1011",
        "sla_state": "on_track",
        "sla_minutes_remaining": 320,
        "created_at": "2026-08-24T11:48:00Z",
        "updated_at": "2026-08-24T14:15:00Z",
        "worklog": [
            (
                "Lena Novak",
                "Excel closes itself whenever the reporting add-in tries to load.",
                "note",
                "2026-08-24T11:48:00Z",
            ),
            (
                "Nikhil Bose",
                "Reproduced. Pinning the affected group to the Semi-Annual Office channel while the add-in is rebuilt.",
                "note",
                "2026-08-24T14:15:00Z",
            ),
        ],
    },
    {
        "number": "INC-4814",
        "summary": "Production database latency spikes",
        "description": "db-prod-01 (AST-1005) is showing p99 query latency above 800ms during peak, up from ~120ms. Users see slow page loads across products.",
        "priority": "P2",
        "status": "on_hold",
        "category": "software",
        "assignee": "Aravind Pillai",
        "requester": "Marcus Feng",
        "asset": "AST-1005",
        "sla_state": "at_risk",
        "sla_minutes_remaining": 58,
        "created_at": "2026-08-24T13:02:00Z",
        "updated_at": "2026-08-24T16:44:00Z",
        "worklog": [
            (
                "Marcus Feng",
                "Everything feels sluggish this afternoon — dashboards take 5–6 seconds.",
                "note",
                "2026-08-24T13:02:00Z",
            ),
            (
                "Aravind Pillai",
                "Latency traced to a missing index after a schema migration. Change request raised; holding for the 18:30 maintenance window.",
                "status",
                "2026-08-24T16:44:00Z",
            ),
        ],
    },
    {
        "number": "INC-4813",
        "summary": "External monitor flickering intermittently",
        "description": "Monitor AST-1007 flickers every few minutes when connected over USB-C.",
        "priority": "P4",
        "status": "new",
        "category": "hardware",
        "assignee": "Farah Naidu",
        "requester": "Daniel Okafor",
        "asset": "AST-1007",
        "sla_state": "on_track",
        "sla_minutes_remaining": 1180,
        "created_at": "2026-08-24T10:20:00Z",
        "updated_at": "2026-08-24T10:20:00Z",
        "worklog": [
            (
                "Daniel Okafor",
                "My second monitor keeps flickering over USB-C. HDMI seems fine.",
                "note",
                "2026-08-24T10:20:00Z",
            ),
        ],
    },
    {
        "number": "INC-4812",
        "summary": "DC-1 switch failover verification",
        "description": "Scheduled failover test of the core switch pair to validate redundancy ahead of the quarterly audit.",
        "priority": "P1",
        "status": "resolved",
        "category": "network",
        "assignee": "Aravind Pillai",
        "requester": "Meera Raghavan",
        "asset": "AST-1006",
        "sla_state": "met",
        "sla_minutes_remaining": None,
        "created_at": "2026-08-22T07:30:00Z",
        "updated_at": "2026-08-22T08:55:00Z",
        "worklog": [
            (
                "Meera Raghavan",
                "Run the planned failover test on the DC-1 core pair.",
                "note",
                "2026-08-22T07:30:00Z",
            ),
            (
                "Aravind Pillai",
                "Failover completed in 1.8s, no packet loss beyond convergence. Redundancy validated.",
                "resolution",
                "2026-08-22T08:55:00Z",
            ),
        ],
    },
    {
        "number": "INC-4811",
        "summary": "Distribution list not receiving external mail",
        "description": "The all-ops@ distribution list drops mail from outside the organization.",
        "priority": "P3",
        "status": "in_progress",
        "category": "email",
        "assignee": "Devansh Rao",
        "requester": "Ishita Banerjee",
        "asset": None,
        "sla_state": "on_track",
        "sla_minutes_remaining": 275,
        "created_at": "2026-08-24T09:40:00Z",
        "updated_at": "2026-08-24T12:10:00Z",
        "worklog": [
            (
                "Ishita Banerjee",
                "Vendors say their emails to all-ops@ bounce or vanish.",
                "note",
                "2026-08-24T09:40:00Z",
            ),
            (
                "Devansh Rao",
                '"Allow external senders" was off on the group. Enabling and testing delivery.',
                "note",
                "2026-08-24T12:10:00Z",
            ),
        ],
    },
    {
        "number": "INC-4810",
        "summary": "Slack desktop notifications not firing",
        "description": "A handful of users get no desktop notifications from Slack after the last app update.",
        "priority": "P4",
        "status": "in_progress",
        "category": "software",
        "assignee": "Farah Naidu",
        "requester": "Divya Menon",
        "asset": None,
        "sla_state": "on_track",
        "sla_minutes_remaining": 900,
        "created_at": "2026-08-24T08:15:00Z",
        "updated_at": "2026-08-24T09:30:00Z",
        "worklog": [
            (
                "Divya Menon",
                "No Slack pop-ups since the update, even though notifications are on.",
                "note",
                "2026-08-24T08:15:00Z",
            ),
            (
                "Farah Naidu",
                "Known issue with the OS notification permission being reset by the update. Sent a reset script.",
                "note",
                "2026-08-24T09:30:00Z",
            ),
        ],
    },
    {
        "number": "INC-4809",
        "summary": "Locked out of Jira after SSO password reset",
        "description": "User completed a password reset but Jira now rejects the SSO login with an account-linking error.",
        "priority": "P2",
        "status": "on_hold",
        "category": "access",
        "assignee": "Devansh Rao",
        "requester": "Aditya Ghosh",
        "asset": "AST-1018",
        "sla_state": "breached",
        "sla_minutes_remaining": -95,
        "created_at": "2026-08-24T12:40:00Z",
        "updated_at": "2026-08-24T16:15:00Z",
        "worklog": [
            (
                "Aditya Ghosh",
                "After resetting my password I can't get into Jira — it says my account can't be linked.",
                "note",
                "2026-08-24T12:40:00Z",
            ),
            (
                "Devansh Rao",
                "SSO assertion mapping broke for this user. Escalated to Atlassian support; awaiting their reply.",
                "status",
                "2026-08-24T14:20:00Z",
            ),
            ("Service desk", "SLA target of 4h breached at 16:40.", "sla", "2026-08-24T16:40:00Z"),
        ],
    },
    {
        "number": "INC-4808",
        "summary": "Docking station not charging laptop",
        "description": "Thunderbolt dock stopped delivering power after a firmware update.",
        "priority": "P3",
        "status": "resolved",
        "category": "hardware",
        "assignee": "Farah Naidu",
        "requester": "Harpreet Kaur",
        "asset": None,
        "sla_state": "met",
        "sla_minutes_remaining": None,
        "created_at": "2026-08-21T14:05:00Z",
        "updated_at": "2026-08-21T16:20:00Z",
        "worklog": [
            (
                "Harpreet Kaur",
                "My dock connects the monitors but no longer charges the laptop.",
                "note",
                "2026-08-21T14:05:00Z",
            ),
            (
                "Farah Naidu",
                "Rolled the dock firmware back to the stable build; charging restored. Swapped the USB-C cable as a precaution.",
                "resolution",
                "2026-08-21T16:20:00Z",
            ),
        ],
    },
    {
        "number": "INC-4807",
        "summary": "Guest Wi-Fi captive portal not loading",
        "description": "Visitors on the guest SSID never see the sign-in portal, so they cannot get online.",
        "priority": "P3",
        "status": "in_progress",
        "category": "network",
        "assignee": "Aravind Pillai",
        "requester": "Grace Adeyemi",
        "asset": "AST-1016",
        "sla_state": "on_track",
        "sla_minutes_remaining": 210,
        "created_at": "2026-08-24T10:55:00Z",
        "updated_at": "2026-08-24T13:25:00Z",
        "worklog": [
            (
                "Grace Adeyemi",
                "Our visitors can join Guest Wi-Fi but the login page never appears.",
                "note",
                "2026-08-24T10:55:00Z",
            ),
            (
                "Aravind Pillai",
                "Captive-portal certificate on the UniFi controller expired overnight. Renewing and re-pushing to the APs.",
                "note",
                "2026-08-24T13:25:00Z",
            ),
        ],
    },
    {
        "number": "INC-4806",
        "summary": "Unusual sign-in from an unrecognised location",
        "description": "Identity protection flagged a sign-in for a Sales account from a country the user has never logged in from.",
        "priority": "P2",
        "status": "closed",
        "category": "security",
        "assignee": "Zoya Khan",
        "requester": "Automated alert",
        "asset": None,
        "sla_state": "met",
        "sla_minutes_remaining": None,
        "created_at": "2026-08-20T22:10:00Z",
        "updated_at": "2026-08-21T07:05:00Z",
        "worklog": [
            (
                "Automated alert",
                "Risky sign-in detected: impossible-travel from a new location.",
                "note",
                "2026-08-20T22:10:00Z",
            ),
            (
                "Zoya Khan",
                "Contacted the user — a genuine trip, VPN was off. Confirmed identity, added the location to the trusted set. No compromise.",
                "resolution",
                "2026-08-21T07:05:00Z",
            ),
        ],
    },
]

# ------------------------------------------------------------------ requests
# (number, catalog_code, category, requester, subject, status, assignee, created_at, updated_at)
REQUESTS: list[tuple[Any, ...]] = [
    (
        "REQ-2051",
        "CAT-01",
        "onboarding",
        "Sneha Kulkarni",
        "Vaidehi Kulkarni (joins 01 Sep)",
        "approval",
        "Meera Raghavan",
        "2026-08-24T09:05:00Z",
        "2026-08-24T09:05:00Z",
    ),
    (
        "REQ-2050",
        "CAT-02",
        "offboarding",
        "Sneha Kulkarni",
        "Kabir Malhotra (last day 29 Aug)",
        "in_progress",
        "Devansh Rao",
        "2026-08-23T15:20:00Z",
        "2026-08-24T11:30:00Z",
    ),
    (
        "REQ-2049",
        "CAT-03",
        "access",
        "Tobias Berg",
        "Grace Adeyemi → Enterprise Sales",
        "approval",
        "Meera Raghavan",
        "2026-08-24T10:40:00Z",
        "2026-08-24T10:40:00Z",
    ),
    (
        "REQ-2048",
        "CAT-04",
        "access",
        "Aditya Ghosh",
        "Aditya Ghosh — finance reporting VPN",
        "fulfilled",
        None,
        "2026-08-22T13:10:00Z",
        "2026-08-22T16:05:00Z",
    ),
    (
        "REQ-2047",
        "CAT-05",
        "hardware",
        "Lena Novak",
        "Lena Novak — replacement laptop",
        "in_progress",
        "Farah Naidu",
        "2026-08-23T10:15:00Z",
        "2026-08-24T09:50:00Z",
    ),
    (
        "REQ-2046",
        "CAT-09",
        "access",
        "Ishita Banerjee",
        "Add to Ops-Reports shared drive",
        "fulfilled",
        None,
        "2026-08-24T08:35:00Z",
        "2026-08-24T10:20:00Z",
    ),
    (
        "REQ-2045",
        "CAT-07",
        "software",
        "Priya Nair",
        "CC seat for a new designer",
        "in_progress",
        "Nikhil Bose",
        "2026-08-24T11:00:00Z",
        "2026-08-24T12:45:00Z",
    ),
    (
        "REQ-2044",
        "CAT-06",
        "hardware",
        "Daniel Okafor",
        "Second display for hybrid desk",
        "approval",
        "Farah Naidu",
        "2026-08-24T13:30:00Z",
        "2026-08-24T13:30:00Z",
    ),
    (
        "REQ-2043",
        "CAT-08",
        "software",
        "Rohan Mehta",
        "All Products Pack for a backend hire",
        "fulfilled",
        None,
        "2026-08-21T09:25:00Z",
        "2026-08-21T15:40:00Z",
    ),
    (
        "REQ-2042",
        "CAT-10",
        "hardware",
        "Sofia Marchetti",
        "Corporate phone for a field AE",
        "in_progress",
        "Devansh Rao",
        "2026-08-23T14:50:00Z",
        "2026-08-24T08:10:00Z",
    ),
    (
        "REQ-2041",
        "CAT-01",
        "onboarding",
        "Sneha Kulkarni",
        "Arnav Desai (joined 22 Aug)",
        "in_progress",
        "Meera Raghavan",
        "2026-08-20T09:00:00Z",
        "2026-08-24T10:05:00Z",
    ),
    (
        "REQ-2040",
        "CAT-04",
        "access",
        "Tobias Berg",
        "Staging VPN for QA contractor",
        "fulfilled",
        None,
        "2026-08-23T09:12:00Z",
        "2026-08-23T13:40:00Z",
    ),
    (
        "REQ-2039",
        "CAT-09",
        "access",
        "Rahul Deshpande",
        "Finance-Close group membership",
        "approval",
        "Meera Raghavan",
        "2026-08-24T14:00:00Z",
        "2026-08-24T14:00:00Z",
    ),
    (
        "REQ-2038",
        "CAT-02",
        "offboarding",
        "Sneha Kulkarni",
        "Harpreet Kaur (transfer-out)",
        "fulfilled",
        None,
        "2026-08-19T16:30:00Z",
        "2026-08-20T11:15:00Z",
    ),
    (
        "REQ-2037",
        "CAT-05",
        "hardware",
        "Marcus Feng",
        "High-spec laptop for a new lead",
        "in_progress",
        "Farah Naidu",
        "2026-08-22T11:45:00Z",
        "2026-08-24T09:20:00Z",
    ),
]


async def seed(session: AsyncSession, ctx: ScopeContext) -> dict[str, int]:
    """Insert the full ITSM demo dataset through the real service write paths. Idempotent.

    Importable and side-effect-free at module load, so tests can drive it directly. Runs inside a
    transaction the caller opens.
    """
    from app import selectors, services  # type: ignore[import-not-found]

    counts = {"assets": 0, "catalog": 0, "incidents": 0, "requests": 0, "skipped": 0}

    for tag, name, atype, status, owner, location, serial, purchased, warranty, cost in ASSETS:
        if await selectors.get_asset(session, ctx.organization_id, tag):
            counts["skipped"] += 1
            continue
        await services.create_asset(
            session,
            ctx,
            tag=tag,
            name=name,
            asset_type=atype,
            status=status,
            owner=owner,
            location=location,
            serial=serial,
            purchased_at=date_from_iso(purchased),
            warranty_expires_at=date_from_iso(warranty),
            cost_minor=cost,
            cost_currency="INR",
        )
        counts["assets"] += 1

    for code, name, category, description, sla_days, requests_30d in CATALOG:
        if await selectors.get_catalog_item(session, ctx.organization_id, code):
            counts["skipped"] += 1
            continue
        await services.create_catalog_item(
            session,
            ctx,
            code=code,
            name=name,
            category=category,
            description=description,
            sla_days=sla_days,
            requests_30d=requests_30d,
        )
        counts["catalog"] += 1

    for spec in INCIDENTS:
        if await selectors.get_incident(session, ctx.organization_id, spec["number"]):
            counts["skipped"] += 1
            continue
        affected_asset_id = None
        if spec["asset"]:
            asset = await selectors.get_asset(session, ctx.organization_id, spec["asset"])
            affected_asset_id = asset.id if asset else None
        minutes = spec["sla_minutes_remaining"]
        due = None if minutes is None else DEMO_NOW + timedelta(minutes=minutes)
        await services.create_incident(
            session,
            ctx,
            number=spec["number"],
            summary=spec["summary"],
            description=spec["description"],
            priority=spec["priority"],
            status=spec["status"],
            category=spec["category"],
            requester=spec["requester"],
            assignee=spec["assignee"],
            affected_asset_id=affected_asset_id,
            sla_state=spec["sla_state"],
            sla_due_at=due,
            sla_breached=spec["sla_state"] == "breached",
            sla_minutes_remaining=minutes,
            created_at=datetime_from_iso(spec["created_at"]),
            updated_at=datetime_from_iso(spec["updated_at"]),
            worklog=[
                {"author": a, "body": b, "kind": k, "at": datetime_from_iso(at)}
                for (a, b, k, at) in spec["worklog"]
            ],
        )
        counts["incidents"] += 1

    for number, code, category, requester, subject, status, assignee, created, updated in REQUESTS:
        if await selectors.get_request(session, ctx.organization_id, number):
            counts["skipped"] += 1
            continue
        await services.create_request(
            session,
            ctx,
            number=number,
            catalog_item_code=code,
            category=category,
            requester=requester,
            subject=subject,
            status=status,
            assignee=assignee,
            created_at=datetime_from_iso(created),
            updated_at=datetime_from_iso(updated),
        )
        counts["requests"] += 1

    return counts


# ------------------------------------------------------------------ standalone entry point


async def _main(
    org_id: uuid.UUID, admin_user_id: uuid.UUID, admin_membership_id: uuid.UUID
) -> None:
    from sqlalchemy import event
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import settings  # type: ignore[import-not-found]
    from app.models import SCHEMA  # type: ignore[import-not-found]
    from platform_core.auth import Principal, Scope, ScopeContext
    from platform_core.db import TENANT_GUC

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
            perms=["itsm.service.incident.write:ORG"],
            entitlements=["itsm"],
        ),
        scope=Scope.ORG,
        permission="itsm.service.incident.write",
    )

    async with maker() as session:

        @event.listens_for(session.sync_session, "after_begin")
        def _bind(_s, _t, connection) -> None:  # type: ignore[no-untyped-def]
            connection.exec_driver_sql(f"SELECT set_config('{TENANT_GUC}', '{org_id}', true)")

        async with session.begin():
            counts = await seed(session, ctx)

    await engine.dispose()
    print(json.dumps(counts))


if __name__ == "__main__":
    _org, _user, _membership = bootstrap()
    asyncio.run(_main(_org, _user, _membership))
