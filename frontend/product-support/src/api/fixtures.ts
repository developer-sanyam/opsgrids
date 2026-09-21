/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI renders
 * without a live login. Swapped out by the `USE_FIXTURES` flag in `client.ts`.
 *
 * Everything is written around a fixed "now" of 2026-08-24T14:30Z (`FIXTURE_NOW`, read by
 * `lib/clock.ts`) so SLA countdowns and "needs attention" render the same on every load. The
 * mutation helpers below (`__…`) simulate the write endpoints in-memory, mirroring
 * `services/support/app/services.py` closely enough that a screen built and tested against
 * fixtures behaves the same way against the real gateway.
 */

import { ApiError } from "@opsgrids/client";

import { canTransition } from "@/lib/ticketLifecycle";
import type {
  Agent,
  AgentIn,
  AgentPatch,
  Message,
  SlaPolicy,
  SlaPolicyIn,
  Ticket,
  TicketCreateIn,
  TicketUpdateIn,
} from "./types";

/** The fixed "current time" the fixtures are written around. */
export const FIXTURE_NOW = new Date("2026-08-24T14:30:00Z");

export const AGENTS: Agent[] = [
  { id: "ag-01", name: "Priya Nair", email: "priya.nair@opsgrids.example.com", role: "team_lead", team: "Tier 1", open_load: 7, avg_first_response_min: 18, csat_pct: 96, solved_7d: 41, online: true },
  { id: "ag-02", name: "Daniel Okafor", email: "daniel.okafor@opsgrids.example.com", role: "senior_agent", team: "Billing", open_load: 9, avg_first_response_min: 24, csat_pct: 93, solved_7d: 37, online: true },
  { id: "ag-03", name: "Sofia Marchetti", email: "sofia.marchetti@opsgrids.example.com", role: "agent", team: "Tier 1", open_load: 12, avg_first_response_min: 41, csat_pct: 87, solved_7d: 29, online: true },
  { id: "ag-04", name: "Kabir Malhotra", email: "kabir.malhotra@opsgrids.example.com", role: "senior_agent", team: "Technical", open_load: 6, avg_first_response_min: 22, csat_pct: 94, solved_7d: 33, online: true },
  { id: "ag-05", name: "Grace Adeyemi", email: "grace.adeyemi@opsgrids.example.com", role: "agent", team: "Onboarding", open_load: 10, avg_first_response_min: 33, csat_pct: 90, solved_7d: 26, online: false },
  { id: "ag-06", name: "Tobias Berg", email: "tobias.berg@opsgrids.example.com", role: "agent", team: "Technical", open_load: 8, avg_first_response_min: 29, csat_pct: 91, solved_7d: 31, online: true },
  { id: "ag-07", name: "Meera Krishnan", email: "meera.krishnan@opsgrids.example.com", role: "agent", team: "Billing", open_load: 5, avg_first_response_min: 26, csat_pct: 92, solved_7d: 22, online: false },
  { id: "ag-08", name: "Lena Novak", email: "lena.novak@opsgrids.example.com", role: "senior_agent", team: "Tier 1", open_load: 11, avg_first_response_min: 19, csat_pct: 95, solved_7d: 39, online: true },
];

export const TICKETS: Ticket[] = [
  {
    id: "TK-4001",
    subject: "Checkout returns 500 for all EU customers",
    requester: { name: "Helena Vogel", email: "helena.vogel@northwind.example", company: "Northwind Traders", kind: "customer" },
    priority: "urgent",
    status: "open",
    channel: "email",
    assignee_id: "ag-04",
    tags: ["payments", "outage", "eu"],
    created_at: "2026-08-24T11:02:00Z",
    updated_at: "2026-08-24T13:40:00Z",
    first_response_at: "2026-08-24T11:19:00Z",
    sla_due: "2026-08-24T12:15:00Z",
    sla_breached: true,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Helena Vogel", at: "2026-08-24T11:02:00Z", body: "Every EU customer hits a 500 at the payment step since about 10:40 UTC. This is blocking all revenue in the region — please treat as urgent." },
      { id: "m2", author_type: "agent", author_name: "Kabir Malhotra", at: "2026-08-24T11:19:00Z", body: "Thanks Helena — I can reproduce with a German test card. Escalating to engineering now and watching the payment gateway logs. I'll update you within 30 minutes." },
      { id: "m3", author_type: "agent", author_name: "Kabir Malhotra", at: "2026-08-24T13:40:00Z", body: "Engineering traced it to a failed currency-service deploy. A rollback is in progress; I'll confirm the moment checkout recovers.", internal: false },
    ],
  },
  {
    id: "TK-4002",
    subject: "Invoice #INV-20831 shows the wrong tax rate",
    requester: { name: "Marco Ricci", email: "marco.ricci@meridianfoods.example", company: "Meridian Foods", kind: "customer" },
    priority: "high",
    status: "open",
    channel: "email",
    assignee_id: "ag-02",
    tags: ["billing", "tax"],
    created_at: "2026-08-24T09:30:00Z",
    updated_at: "2026-08-24T12:05:00Z",
    first_response_at: "2026-08-24T09:58:00Z",
    sla_due: "2026-08-24T14:58:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Marco Ricci", at: "2026-08-24T09:30:00Z", body: "The August invoice applies 20% VAT but our account is registered as reverse-charge. Can you correct and reissue?" },
      { id: "m2", author_type: "agent", author_name: "Daniel Okafor", at: "2026-08-24T09:58:00Z", body: "You're right, Marco — your tax exemption wasn't applied on this cycle. I'm raising a corrected invoice and it will supersede the original. Nothing to pay in the meantime." },
    ],
  },
  {
    id: "TK-4003",
    subject: "Cannot log in — SSO loop after password reset",
    requester: { name: "Aisha Bello", email: "aisha.bello@voltamobility.example", company: "Volta Mobility", kind: "customer" },
    priority: "urgent",
    status: "new",
    channel: "chat",
    assignee_id: null,
    tags: ["auth", "sso"],
    created_at: "2026-08-24T14:05:00Z",
    updated_at: "2026-08-24T14:05:00Z",
    first_response_at: null,
    sla_due: "2026-08-24T15:05:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Aisha Bello", at: "2026-08-24T14:05:00Z", body: "After resetting my password I'm stuck in an endless SSO redirect and can't get into the admin console. Three of my colleagues report the same." },
    ],
  },
  {
    id: "TK-4004",
    subject: "Export to CSV truncates at 10,000 rows",
    requester: { name: "Ravi Menon", email: "ravi.menon@cobaltlabs.example", company: "Cobalt Labs", kind: "customer" },
    priority: "high",
    status: "pending",
    channel: "web",
    assignee_id: "ag-03",
    tags: ["reports", "export"],
    created_at: "2026-08-23T16:12:00Z",
    updated_at: "2026-08-24T10:20:00Z",
    first_response_at: "2026-08-23T17:00:00Z",
    sla_due: "2026-08-24T11:40:00Z",
    sla_breached: true,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Ravi Menon", at: "2026-08-23T16:12:00Z", body: "Our monthly usage export stops at exactly 10,000 rows. We have ~34,000 and need the full set for the audit this week." },
      { id: "m2", author_type: "agent", author_name: "Sofia Marchetti", at: "2026-08-23T17:00:00Z", body: "Thanks for flagging — there's a hard cap on the synchronous export. I've asked engineering about the async job that lifts it and will come back to you." },
      { id: "m3", author_type: "agent", author_name: "Sofia Marchetti", at: "2026-08-24T10:20:00Z", body: "Waiting on the engineering estimate for the async exporter. Marking pending on their reply.", internal: true },
    ],
  },
  {
    id: "TK-4005",
    subject: "Webhook deliveries delayed by ~15 minutes",
    requester: { name: "Tomás Herrera", email: "tomas.herrera@harborfreight.example", company: "Harbor Freight Co", kind: "customer" },
    priority: "normal",
    status: "open",
    channel: "api",
    assignee_id: "ag-06",
    tags: ["webhooks", "latency"],
    created_at: "2026-08-24T12:40:00Z",
    updated_at: "2026-08-24T13:10:00Z",
    first_response_at: "2026-08-24T13:02:00Z",
    sla_due: "2026-08-24T15:20:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Tomás Herrera", at: "2026-08-24T12:40:00Z", body: "Our order.created webhooks are arriving 12–18 minutes late since this morning. It's throwing off our fulfilment queue." },
      { id: "m2", author_type: "agent", author_name: "Tobias Berg", at: "2026-08-24T13:02:00Z", body: "Appreciate the detail, Tomás. We're seeing elevated queue depth on the delivery workers and are scaling them up. I'll confirm once latency is back under a minute." },
    ],
  },
  {
    id: "TK-4006",
    subject: "How do I add a second billing contact?",
    requester: { name: "Nadia Farouk", email: "nadia.farouk@lumenhealth.example", company: "Lumen Health", kind: "customer" },
    priority: "normal",
    status: "open",
    channel: "email",
    assignee_id: "ag-01",
    tags: ["billing", "how-to"],
    created_at: "2026-08-24T08:15:00Z",
    updated_at: "2026-08-24T09:00:00Z",
    first_response_at: "2026-08-24T08:44:00Z",
    sla_due: "2026-08-24T18:00:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Nadia Farouk", at: "2026-08-24T08:15:00Z", body: "We'd like invoices to also go to our finance mailbox. Where do I add a second billing contact?" },
      { id: "m2", author_type: "agent", author_name: "Priya Nair", at: "2026-08-24T08:44:00Z", body: "Happy to help! Settings → Billing → Contacts → Add contact, then tick \"receives invoices\". I've linked a short guide — let me know if the option isn't visible on your plan." },
    ],
  },
  {
    id: "TK-4007",
    subject: "Feature request: dark mode for the dashboard",
    requester: { name: "Oliver Grant", email: "oliver.grant@sierraretail.example", company: "Sierra Retail", kind: "customer" },
    priority: "low",
    status: "open",
    channel: "web",
    assignee_id: "ag-05",
    tags: ["feature-request", "ui"],
    created_at: "2026-08-22T14:20:00Z",
    updated_at: "2026-08-23T11:00:00Z",
    first_response_at: "2026-08-22T15:10:00Z",
    sla_due: "2026-08-25T10:00:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Oliver Grant", at: "2026-08-22T14:20:00Z", body: "Our ops team works evenings — a dark theme would be much easier on the eyes. Any plans for one?" },
      { id: "m2", author_type: "agent", author_name: "Grace Adeyemi", at: "2026-08-22T15:10:00Z", body: "Thanks Oliver — I've logged this on the product board and linked your account so you'll be notified. It's a common ask, so it's genuinely on the radar." },
    ],
  },
  {
    id: "TK-4008",
    subject: "API rate limit hit during nightly sync",
    requester: { name: "Priyanka Shah", email: "priyanka.shah@pallaslogistics.example", company: "Pallas Logistics", kind: "customer" },
    priority: "high",
    status: "new",
    channel: "api",
    assignee_id: null,
    tags: ["api", "rate-limit"],
    created_at: "2026-08-24T13:55:00Z",
    updated_at: "2026-08-24T13:55:00Z",
    first_response_at: null,
    sla_due: "2026-08-24T17:15:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Priyanka Shah", at: "2026-08-24T13:55:00Z", body: "Our 02:00 sync started hitting 429s halfway through. Can we get a higher burst limit, or guidance on batching?" },
    ],
  },
  {
    id: "TK-4009",
    subject: "Refund not reflected on customer statement",
    requester: { name: "Emeka Nwosu", email: "emeka.nwosu@onyxstudios.example", company: "Onyx Studios", kind: "customer" },
    priority: "normal",
    status: "pending",
    channel: "email",
    assignee_id: "ag-08",
    tags: ["billing", "refund"],
    created_at: "2026-08-23T10:05:00Z",
    updated_at: "2026-08-24T09:15:00Z",
    first_response_at: "2026-08-23T10:40:00Z",
    sla_due: "2026-08-24T19:30:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Emeka Nwosu", at: "2026-08-23T10:05:00Z", body: "You confirmed a refund on Friday but it hasn't shown on our statement yet. Can you check?" },
      { id: "m2", author_type: "agent", author_name: "Lena Novak", at: "2026-08-23T10:40:00Z", body: "The refund was issued our side on Friday; banks can take 5–7 business days to post it. I've attached the transaction reference so your bank can trace it if needed." },
    ],
  },
  {
    id: "TK-4010",
    subject: "Update company address on account",
    requester: { name: "Sara Lindqvist", email: "sara.lindqvist@meridianfoods.example", company: "Meridian Foods", kind: "customer" },
    priority: "low",
    status: "open",
    channel: "web",
    assignee_id: "ag-07",
    tags: ["account", "how-to"],
    created_at: "2026-08-24T07:50:00Z",
    updated_at: "2026-08-24T08:30:00Z",
    first_response_at: "2026-08-24T08:22:00Z",
    sla_due: "2026-08-25T14:00:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Sara Lindqvist", at: "2026-08-24T07:50:00Z", body: "We've moved offices — how do I update the registered address so it appears on future invoices?" },
      { id: "m2", author_type: "agent", author_name: "Meera Krishnan", at: "2026-08-24T08:22:00Z", body: "Congrats on the move! An account owner can edit it under Settings → Organisation → Legal details. It applies from the next invoice; let me know if you'd like the current one reissued." },
    ],
  },
  {
    id: "TK-4011",
    subject: "Custom report builder crashes on grouped date",
    requester: { name: "Ivan Petrov", email: "ivan.petrov@cobaltlabs.example", company: "Cobalt Labs", kind: "customer" },
    priority: "normal",
    status: "on_hold",
    channel: "web",
    assignee_id: "ag-06",
    tags: ["reports", "bug"],
    created_at: "2026-08-21T13:00:00Z",
    updated_at: "2026-08-23T16:45:00Z",
    first_response_at: "2026-08-21T13:35:00Z",
    sla_due: null,
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Ivan Petrov", at: "2026-08-21T13:00:00Z", body: "Grouping by week in the report builder throws an error and clears my selections." },
      { id: "m2", author_type: "agent", author_name: "Tobias Berg", at: "2026-08-21T13:35:00Z", body: "Reproduced — there's a fix queued for the next release. I'll hold this ticket and let you know the moment it ships." },
      { id: "m3", author_type: "system", author_name: "System", at: "2026-08-23T16:45:00Z", body: "Placed on hold pending release 2026.9.0." },
    ],
  },
  {
    id: "TK-4012",
    subject: "Single sign-on with Okta — metadata mismatch",
    requester: { name: "Grace Wanjiru", email: "grace.wanjiru@lumenhealth.example", company: "Lumen Health", kind: "customer" },
    priority: "high",
    status: "on_hold",
    channel: "email",
    assignee_id: "ag-04",
    tags: ["auth", "sso", "setup"],
    created_at: "2026-08-20T09:40:00Z",
    updated_at: "2026-08-22T14:10:00Z",
    first_response_at: "2026-08-20T10:05:00Z",
    sla_due: null,
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Grace Wanjiru", at: "2026-08-20T09:40:00Z", body: "Setting up Okta SSO — the ACS URL in your metadata doesn't match what your docs show. Can you confirm the right value?" },
      { id: "m2", author_type: "agent", author_name: "Kabir Malhotra", at: "2026-08-20T10:05:00Z", body: "Good catch — the docs are behind. I've sent the current metadata XML. Let me know once your IdP admin has imported it and we'll test a login together." },
      { id: "m3", author_type: "agent", author_name: "Kabir Malhotra", at: "2026-08-22T14:10:00Z", body: "Holding for the customer's IdP admin to schedule the test window.", internal: true },
    ],
  },
  {
    id: "TK-4013",
    subject: "Data region migration to eu-central completed?",
    requester: { name: "Felix Braun", email: "felix.braun@voltamobility.example", company: "Volta Mobility", kind: "customer" },
    priority: "urgent",
    status: "solved",
    channel: "email",
    assignee_id: "ag-01",
    tags: ["data-residency", "migration"],
    created_at: "2026-08-22T08:00:00Z",
    updated_at: "2026-08-22T15:30:00Z",
    first_response_at: "2026-08-22T08:12:00Z",
    sla_due: null,
    sla_breached: false,
    satisfaction: "good",
    messages: [
      { id: "m1", author_type: "customer", author_name: "Felix Braun", at: "2026-08-22T08:00:00Z", body: "Compliance needs written confirmation that our data now resides in eu-central. Can you confirm today?" },
      { id: "m2", author_type: "agent", author_name: "Priya Nair", at: "2026-08-22T08:12:00Z", body: "Absolutely — the migration finished on the 19th. I've attached the signed data-residency attestation covering eu-central." },
      { id: "m3", author_type: "customer", author_name: "Felix Braun", at: "2026-08-22T15:20:00Z", body: "Perfect, that's exactly what compliance needed. Thank you!" },
    ],
  },
  {
    id: "TK-4014",
    subject: "Seat count increase for Q3",
    requester: { name: "Hannah Berg", email: "hannah.berg@sierraretail.example", company: "Sierra Retail", kind: "customer" },
    priority: "normal",
    status: "solved",
    channel: "chat",
    assignee_id: "ag-08",
    tags: ["billing", "seats"],
    created_at: "2026-08-21T11:10:00Z",
    updated_at: "2026-08-21T12:00:00Z",
    first_response_at: "2026-08-21T11:22:00Z",
    sla_due: null,
    sla_breached: false,
    satisfaction: "good",
    messages: [
      { id: "m1", author_type: "customer", author_name: "Hannah Berg", at: "2026-08-21T11:10:00Z", body: "We need to add 15 seats effective the 1st. Can you handle the proration?" },
      { id: "m2", author_type: "agent", author_name: "Lena Novak", at: "2026-08-21T11:22:00Z", body: "Done — 15 seats added, prorated for the remainder of the cycle. A confirmation is on its way to your billing contact." },
    ],
  },
  {
    id: "TK-4015",
    subject: "Onboarding call never happened",
    requester: { name: "Diego Alvarez", email: "diego.alvarez@pallaslogistics.example", company: "Pallas Logistics", kind: "customer" },
    priority: "high",
    status: "solved",
    channel: "email",
    assignee_id: "ag-03",
    tags: ["onboarding", "escalation"],
    created_at: "2026-08-19T09:00:00Z",
    updated_at: "2026-08-20T17:45:00Z",
    first_response_at: "2026-08-19T15:30:00Z",
    sla_due: null,
    sla_breached: true,
    satisfaction: "bad",
    messages: [
      { id: "m1", author_type: "customer", author_name: "Diego Alvarez", at: "2026-08-19T09:00:00Z", body: "Nobody joined our scheduled onboarding call this morning. We blocked an hour for it. Not a great first impression." },
      { id: "m2", author_type: "agent", author_name: "Sofia Marchetti", at: "2026-08-19T15:30:00Z", body: "I'm sorry, Diego — that's on us; the invite didn't sync to the specialist's calendar. I've rebooked for tomorrow at 10:00 and looped in the onboarding lead." },
      { id: "m3", author_type: "customer", author_name: "Diego Alvarez", at: "2026-08-20T17:45:00Z", body: "The call happened, thanks. Still, the miss cost us a day." },
    ],
  },
  {
    id: "TK-4016",
    subject: "Where can I download past invoices?",
    requester: { name: "Yuki Tanaka", email: "yuki.tanaka@onyxstudios.example", company: "Onyx Studios", kind: "customer" },
    priority: "low",
    status: "solved",
    channel: "web",
    assignee_id: "ag-05",
    tags: ["billing", "how-to"],
    created_at: "2026-08-20T13:20:00Z",
    updated_at: "2026-08-20T13:50:00Z",
    first_response_at: "2026-08-20T13:38:00Z",
    sla_due: null,
    sla_breached: false,
    satisfaction: "good",
    messages: [
      { id: "m1", author_type: "customer", author_name: "Yuki Tanaka", at: "2026-08-20T13:20:00Z", body: "Our accountant needs the last 12 invoices. Where do I download them?" },
      { id: "m2", author_type: "agent", author_name: "Grace Adeyemi", at: "2026-08-20T13:38:00Z", body: "Settings → Billing → Invoices lets you download each as a PDF, or \"Export all\" for a single ZIP. That should cover the full 12 months." },
    ],
  },
  {
    id: "TK-4017",
    subject: "Staff laptop can't reach the internal admin console",
    requester: { name: "Aditya Rao", email: "aditya.rao@opsgrids.example.com", company: "OpsGrids (internal)", kind: "staff" },
    priority: "normal",
    status: "solved",
    channel: "web",
    assignee_id: "ag-02",
    tags: ["internal", "access"],
    created_at: "2026-08-21T09:05:00Z",
    updated_at: "2026-08-21T10:15:00Z",
    first_response_at: "2026-08-21T09:20:00Z",
    sla_due: null,
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Aditya Rao", at: "2026-08-21T09:05:00Z", body: "Can't reach admin.internal from the new laptop — times out. VPN is connected." },
      { id: "m2", author_type: "agent", author_name: "Daniel Okafor", at: "2026-08-21T09:20:00Z", body: "Your device wasn't in the allow-list for the internal subnet. Added it — try again after reconnecting the VPN." },
      { id: "m3", author_type: "customer", author_name: "Aditya Rao", at: "2026-08-21T10:15:00Z", body: "Works now. Cheers." },
    ],
  },
  {
    id: "TK-4018",
    subject: "Enable two-factor enforcement for all users",
    requester: { name: "Clara Nunes", email: "clara.nunes@harborfreight.example", company: "Harbor Freight Co", kind: "customer" },
    priority: "high",
    status: "solved",
    channel: "email",
    assignee_id: "ag-04",
    tags: ["security", "2fa"],
    created_at: "2026-08-22T10:30:00Z",
    updated_at: "2026-08-22T12:10:00Z",
    first_response_at: "2026-08-22T10:48:00Z",
    sla_due: null,
    sla_breached: false,
    satisfaction: "good",
    messages: [
      { id: "m1", author_type: "customer", author_name: "Clara Nunes", at: "2026-08-22T10:30:00Z", body: "Security wants 2FA required for every user in our org. Can you switch on enforcement?" },
      { id: "m2", author_type: "agent", author_name: "Kabir Malhotra", at: "2026-08-22T10:48:00Z", body: "Enforcement is now on for your org. Existing users are prompted to enrol at next sign-in; I've included a short admin note you can forward to your team." },
    ],
  },
  {
    id: "TK-4019",
    subject: "Typo on the public status page",
    requester: { name: "Léa Moreau", email: "lea.moreau@voltamobility.example", company: "Volta Mobility", kind: "customer" },
    priority: "normal",
    status: "new",
    channel: "web",
    assignee_id: null,
    tags: ["docs", "minor"],
    created_at: "2026-08-24T13:20:00Z",
    updated_at: "2026-08-24T13:20:00Z",
    first_response_at: null,
    sla_due: "2026-08-24T16:45:00Z",
    sla_breached: false,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Léa Moreau", at: "2026-08-24T13:20:00Z", body: "Small thing — the status page says \"degredaded\" instead of \"degraded\". Thought you'd want to know." },
    ],
  },
  {
    id: "TK-4020",
    subject: "Bulk import failing with unhelpful error",
    requester: { name: "Sam Whitfield", email: "sam.whitfield@sierraretail.example", company: "Sierra Retail", kind: "customer" },
    priority: "urgent",
    status: "open",
    channel: "web",
    assignee_id: "ag-02",
    tags: ["import", "bug"],
    created_at: "2026-08-24T10:15:00Z",
    updated_at: "2026-08-24T13:05:00Z",
    first_response_at: "2026-08-24T10:41:00Z",
    sla_due: "2026-08-24T13:30:00Z",
    sla_breached: true,
    satisfaction: null,
    messages: [
      { id: "m1", author_type: "customer", author_name: "Sam Whitfield", at: "2026-08-24T10:15:00Z", body: "Importing our product catalogue fails with just \"error 3\" and no detail. We're launching Monday and stuck." },
      { id: "m2", author_type: "agent", author_name: "Daniel Okafor", at: "2026-08-24T10:41:00Z", body: "Sorry for the vague error, Sam — \"error 3\" means a duplicate SKU in the file. I'm pulling the offending rows from your last upload and will send the list so you can dedupe." },
      { id: "m3", author_type: "agent", author_name: "Daniel Okafor", at: "2026-08-24T13:05:00Z", body: "Found 42 duplicate SKUs; list attached. I've also filed a bug so the importer names the SKU in the error going forward.", internal: false },
    ],
  },
];

export let SLA_POLICY: SlaPolicy | null = {
  name: "Standard Support SLA",
  business_hours: "Mon–Fri, 08:00–20:00 IST",
  coverage: "Urgent tickets are covered 24×7; all other priorities follow business hours.",
  targets: [
    { priority: "urgent", first_response_min: 30, resolution_min: 240 },
    { priority: "high", first_response_min: 60, resolution_min: 480 },
    { priority: "normal", first_response_min: 240, resolution_min: 1440 },
    { priority: "low", first_response_min: 480, resolution_min: 2880 },
  ],
};

/* --------------------------------------------------------------------- mutation helpers
 * Each mirrors one write in `services/support/app/services.py` closely enough that a screen
 * behaves identically against fixtures and against the real gateway: same 404s, same 409 on an
 * illegal status move, same server-allocated ids. Synchronous — `client.ts` wraps every call in
 * `settle()` so fixture mode still exercises the loading state.
 */

function notFound(message: string): never {
  throw new ApiError(404, message, { title: "Not Found", status: 404, detail: message });
}

export function __getFixtureTicket(number: string): Ticket {
  const ticket = TICKETS.find((t) => t.id === number);
  if (!ticket) notFound("That ticket is not part of this organization");
  return ticket;
}

function __getFixtureAgent(ref: string): Agent | undefined {
  return AGENTS.find((a) => a.id === ref);
}

export function __replyToFixtureTicket(
  number: string,
  body: string,
  authorName: string,
  internal: boolean,
): Ticket {
  const ticket = __getFixtureTicket(number);
  const at = FIXTURE_NOW.toISOString();
  const message: Message = {
    id: `m${ticket.messages.length + 1}`,
    author_type: "agent",
    author_name: authorName,
    at,
    body,
    ...(internal ? { internal: true } : {}),
  };
  ticket.messages = [...ticket.messages, message];
  if (!internal && ticket.first_response_at === null) ticket.first_response_at = at;
  if (!internal && ticket.status === "new") {
    ticket.status = "open";
  } else if (ticket.status === "solved") {
    ticket.status = "open";
  }
  ticket.updated_at = at;
  return { ...ticket };
}

export function __assignFixtureTicket(number: string, assigneeId: string | null): Ticket {
  const ticket = __getFixtureTicket(number);
  if (assigneeId !== null && !__getFixtureAgent(assigneeId)) {
    notFound("That agent is not part of this organization");
  }
  ticket.assignee_id = assigneeId;
  ticket.updated_at = FIXTURE_NOW.toISOString();
  return { ...ticket };
}

export function __changeFixtureTicketStatus(number: string, target: Ticket["status"]): Ticket {
  const ticket = __getFixtureTicket(number);
  if (!canTransition(ticket.status, target)) {
    throw new ApiError(409, `A ticket cannot move from '${ticket.status}' to '${target}'`, {
      title: "Conflict",
      status: 409,
      detail: `A ticket cannot move from '${ticket.status}' to '${target}'`,
    });
  }
  ticket.status = target;
  if (target === "solved") ticket.sla_due = null;
  ticket.updated_at = FIXTURE_NOW.toISOString();
  return { ...ticket };
}

export function __updateFixtureTicket(number: string, payload: TicketUpdateIn): Ticket {
  const ticket = __getFixtureTicket(number);
  if (payload.priority !== undefined) ticket.priority = payload.priority;
  if (payload.subject !== undefined) ticket.subject = payload.subject;
  if (payload.tags !== undefined) ticket.tags = payload.tags;
  ticket.updated_at = FIXTURE_NOW.toISOString();
  return { ...ticket };
}

function nextFixtureTicketNumber(): string {
  const max = TICKETS.reduce((top, t) => {
    const n = Number(t.id.replace("TK-", ""));
    return Number.isFinite(n) && n > top ? n : top;
  }, 0);
  return `TK-${String(max + 1).padStart(4, "0")}`;
}

export function __createFixtureTicket(payload: TicketCreateIn): Ticket {
  if (payload.assignee_id != null && !__getFixtureAgent(payload.assignee_id)) {
    notFound("That agent is not part of this organization");
  }
  const at = FIXTURE_NOW.toISOString();
  const priority = payload.priority ?? "normal";
  const target = SLA_POLICY?.targets.find((t) => t.priority === priority) ?? null;
  const slaDue = target
    ? new Date(FIXTURE_NOW.getTime() + target.first_response_min * 60000).toISOString()
    : null;
  const ticket: Ticket = {
    id: nextFixtureTicketNumber(),
    subject: payload.subject,
    requester: {
      name: payload.requester.name,
      email: payload.requester.email,
      company: payload.requester.company,
      kind: payload.requester.kind ?? "customer",
    },
    priority,
    status: "new",
    channel: payload.channel ?? "web",
    assignee_id: payload.assignee_id ?? null,
    tags: payload.tags ?? [],
    created_at: at,
    updated_at: at,
    first_response_at: null,
    sla_due: slaDue,
    sla_breached: false,
    satisfaction: null,
    messages: [
      {
        id: "m1",
        author_type: "customer",
        author_name: payload.requester.name,
        at,
        body: payload.body,
      },
    ],
  };
  TICKETS.unshift(ticket);
  return { ...ticket };
}

function nextFixtureAgentRef(): string {
  const max = AGENTS.reduce((top, a) => {
    const n = Number(a.id.replace("ag-", ""));
    return Number.isFinite(n) && n > top ? n : top;
  }, 0);
  return `ag-${String(max + 1).padStart(2, "0")}`;
}

export function __createFixtureAgent(payload: AgentIn): Agent {
  const agent: Agent = {
    id: nextFixtureAgentRef(),
    name: payload.name,
    email: payload.email,
    role: payload.role ?? "agent",
    team: payload.team ?? "Tier 1",
    open_load: 0,
    avg_first_response_min: 0,
    csat_pct: 0,
    solved_7d: 0,
    online: payload.online ?? false,
  };
  AGENTS.push(agent);
  return { ...agent };
}

export function __updateFixtureAgent(ref: string, payload: AgentPatch): Agent {
  const agent = __getFixtureAgent(ref);
  if (!agent) notFound("That agent is not part of this organization");
  if (payload.name !== undefined) agent.name = payload.name;
  if (payload.email !== undefined) agent.email = payload.email;
  if (payload.role !== undefined) agent.role = payload.role;
  if (payload.team !== undefined) agent.team = payload.team;
  if (payload.online !== undefined) agent.online = payload.online;
  return { ...agent };
}

export function __deleteFixtureAgent(ref: string): void {
  const index = AGENTS.findIndex((a) => a.id === ref);
  if (index === -1) notFound("That agent is not part of this organization");
  AGENTS.splice(index, 1);
  for (const ticket of TICKETS) {
    if (ticket.assignee_id === ref) ticket.assignee_id = null;
  }
}

export function __setFixtureSlaPolicy(payload: SlaPolicyIn): SlaPolicy {
  SLA_POLICY = {
    name: payload.name,
    business_hours: payload.business_hours ?? "",
    coverage: payload.coverage ?? "",
    targets: payload.targets.map((t) => ({ ...t })),
  };
  return { ...SLA_POLICY };
}
