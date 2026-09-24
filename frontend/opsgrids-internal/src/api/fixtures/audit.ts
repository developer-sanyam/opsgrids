/**
 * The operator audit log fixture — `AuditOut`, exactly what `GET /admin/audit` returns: an actor
 * id (not a name), an `entity`/`entity_id` reference, a free-text reason and a timestamp. No
 * organization name, no human summary and no success/failure flag — a row is only ever written
 * *after* the write it records already succeeded, so there is no "failed" row to show.
 */

import type { AuditListOut, AuditOut } from "@/api/types";

const PRIYA = "3f1a5b2c-0000-4000-8000-000000000001";
const RAHUL = "3f1a5b2c-0000-4000-8000-000000000002";
const DEVANSH = "3f1a5b2c-0000-4000-8000-000000000003";

export const AUDIT_LOG: AuditOut[] = [
  {
    id: "aud_0031",
    organization_id: "org_e5b440",
    actor_id: PRIYA,
    acting_as: "operator:owner",
    action: "organization.read",
    entity: "organization",
    entity_id: "org_e5b440",
    reason: null,
    at: "2026-08-20T13:48:00Z",
  },
  {
    id: "aud_0029",
    organization_id: "org_905ffb",
    actor_id: RAHUL,
    acting_as: "operator:billing_ops",
    action: "suspend",
    entity: "subscription",
    entity_id: "sub_905ffb_1",
    reason: "Non-payment after dunning window closed",
    at: "2026-08-12T10:05:00Z",
  },
  {
    id: "aud_0028",
    organization_id: "org_07c662",
    actor_id: RAHUL,
    acting_as: "operator:billing_ops",
    action: "comp_grant",
    entity: "credit",
    entity_id: "le_blueoak_grant_01",
    reason: "Trial-linked comp access while evaluating CRM",
    at: "2026-08-06T09:40:00Z",
  },
  {
    id: "aud_0027",
    organization_id: "org_5c1bb7",
    actor_id: RAHUL,
    acting_as: "operator:billing_ops",
    action: "suspend",
    entity: "subscription",
    entity_id: "sub_5c1bb7_1",
    reason: "Payment mandate revoked by customer's bank",
    at: "2026-08-04T16:38:00Z",
  },
  {
    id: "aud_0026",
    organization_id: "org_4b0aa6",
    actor_id: PRIYA,
    acting_as: "operator:owner",
    action: "suspend",
    entity: "subscription",
    entity_id: "sub_4b0aa6_1",
    reason: "Non-payment after dunning window closed",
    at: "2026-07-30T10:04:00Z",
  },
  {
    id: "aud_0025",
    organization_id: "org_7e3dd9",
    actor_id: PRIYA,
    acting_as: "operator:owner",
    action: "comp_grant",
    entity: "credit",
    entity_id: "le_lotus_grant_01",
    reason: "Migration goodwill — 3 months complimentary Scale",
    at: "2026-07-29T11:12:00Z",
  },
  {
    id: "aud_0022",
    organization_id: "org_a1f09c",
    actor_id: PRIYA,
    acting_as: "operator:owner",
    action: "comp_grant",
    entity: "credit",
    entity_id: "le_northwind_grant_01",
    reason: "SLA credit for Q1 settlement-desk downtime",
    at: "2026-06-02T10:18:00Z",
  },
  {
    id: "aud_0020",
    organization_id: "org_a1f09c",
    actor_id: DEVANSH,
    acting_as: "operator:owner",
    action: "pricing.set",
    entity: "plan_version",
    entity_id: "pv_trading_bespoke_northwind_v1",
    reason: "Negotiated enterprise rate — 3-year commit",
    at: "2025-12-09T12:15:00Z",
  },
];

export function buildAuditListOut(): AuditListOut {
  return { audit: AUDIT_LOG };
}
