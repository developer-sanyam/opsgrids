/**
 * Realistic fixture data for OpsGrids Revenue, shaped exactly like the planned API responses so the
 * whole UI renders without a live backend. Swapped in by the `USE_FIXTURES` flag in `client.ts`.
 *
 * All money is integer **minor units** (paise) of the row's currency — `rupees()` converts a
 * readable rupee figure into the stored minor-unit integer. "Today" across the app is 2026-08-24.
 */

import type {
  Activity,
  Company,
  Contact,
  Deal,
  Lead,
  LeadStatus,
  Owner,
} from "./types";

/** Rupees -> paise (minor units). ₹1 = 100 paise. */
const rupees = (n: number): number => n * 100;

/* -------------------------------- owners -------------------------------- */

export const OWNERS: Owner[] = [
  { id: "own-1", full_name: "Sofia Marchetti", email: "sofia.marchetti@opsgrids.example.com", role: "Regional Sales Head", is_active: true },
  { id: "own-2", full_name: "Vikram Singh", email: "vikram.singh@opsgrids.example.com", role: "Account Executive", is_active: true },
  { id: "own-3", full_name: "Grace Adeyemi", email: "grace.adeyemi@opsgrids.example.com", role: "Sales Development Rep", is_active: true },
  { id: "own-4", full_name: "Kabir Malhotra", email: "kabir.malhotra@opsgrids.example.com", role: "Enterprise AE", is_active: true },
  { id: "own-5", full_name: "Emily Carter", email: "emily.carter@opsgrids.example.com", role: "Solutions Consultant", is_active: true },
  { id: "own-6", full_name: "Rahul Deshpande", email: "rahul.deshpande@opsgrids.example.com", role: "Account Manager", is_active: false },
];

/* ------------------------------- companies ------------------------------- */

export const COMPANIES: Company[] = [
  { id: "comp-1", name: "Northwind Logistics", domain: "northwind.co", industry: "Logistics", size_band: "501–1000", city: "Mumbai", country: "India", status: "customer", owner_id: "own-1", annual_revenue_minor: rupees(8_400_000_000), currency: "INR", created_at: "2024-02-11T09:00:00Z" },
  { id: "comp-2", name: "Meridian Steelworks", domain: "meridiansteel.com", industry: "Manufacturing", size_band: "1000+", city: "Jamshedpur", country: "India", status: "customer", owner_id: "own-4", annual_revenue_minor: rupees(21_000_000_000), currency: "INR", created_at: "2023-11-03T09:00:00Z" },
  { id: "comp-3", name: "Cobalt Financial", domain: "cobaltfin.com", industry: "Financial Services", size_band: "201–500", city: "Bengaluru", country: "India", status: "prospect", owner_id: "own-2", annual_revenue_minor: rupees(5_600_000_000), currency: "INR", created_at: "2025-01-22T09:00:00Z" },
  { id: "comp-4", name: "Vertex Robotics", domain: "vertexrobotics.io", industry: "Industrial Automation", size_band: "51–200", city: "Pune", country: "India", status: "prospect", owner_id: "own-5", annual_revenue_minor: rupees(1_800_000_000), currency: "INR", created_at: "2025-04-08T09:00:00Z" },
  { id: "comp-5", name: "Harbor & Co Retail", domain: "harborandco.com", industry: "Retail", size_band: "201–500", city: "Delhi", country: "India", status: "customer", owner_id: "own-6", annual_revenue_minor: rupees(4_300_000_000), currency: "INR", created_at: "2024-06-19T09:00:00Z" },
  { id: "comp-6", name: "Aurora Health Systems", domain: "aurorahealth.in", industry: "Healthcare", size_band: "501–1000", city: "Hyderabad", country: "India", status: "prospect", owner_id: "own-1", annual_revenue_minor: rupees(6_900_000_000), currency: "INR", created_at: "2024-09-30T09:00:00Z" },
  { id: "comp-7", name: "Ironclad Manufacturing", domain: "ironcladmfg.com", industry: "Manufacturing", size_band: "201–500", city: "Coimbatore", country: "India", status: "lead", owner_id: "own-3", annual_revenue_minor: rupees(3_100_000_000), currency: "INR", created_at: "2025-06-14T09:00:00Z" },
  { id: "comp-8", name: "Solstice Energy", domain: "solsticeenergy.com", industry: "Energy", size_band: "1000+", city: "Ahmedabad", country: "India", status: "customer", owner_id: "own-4", annual_revenue_minor: rupees(17_500_000_000), currency: "INR", created_at: "2023-08-27T09:00:00Z" },
  { id: "comp-9", name: "Beacon Media Group", domain: "beaconmedia.co", industry: "Media", size_band: "51–200", city: "Mumbai", country: "India", status: "lead", owner_id: "own-3", annual_revenue_minor: rupees(950_000_000), currency: "INR", created_at: "2025-05-02T09:00:00Z" },
  { id: "comp-10", name: "Cascade Foods", domain: "cascadefoods.in", industry: "FMCG", size_band: "501–1000", city: "Nagpur", country: "India", status: "prospect", owner_id: "own-2", annual_revenue_minor: rupees(5_200_000_000), currency: "INR", created_at: "2024-12-05T09:00:00Z" },
  { id: "comp-11", name: "Quantic Software", domain: "quantic.dev", industry: "Software", size_band: "51–200", city: "Bengaluru", country: "India", status: "customer", owner_id: "own-5", annual_revenue_minor: rupees(1_400_000_000), currency: "INR", created_at: "2024-03-18T09:00:00Z" },
  { id: "comp-12", name: "Pinnacle Realty", domain: "pinnaclerealty.in", industry: "Real Estate", size_band: "201–500", city: "Gurugram", country: "India", status: "churned", owner_id: "own-6", annual_revenue_minor: rupees(3_800_000_000), currency: "INR", created_at: "2023-10-11T09:00:00Z" },
  { id: "comp-13", name: "Delta Freight Lines", domain: "deltafreight.com", industry: "Logistics", size_band: "501–1000", city: "Chennai", country: "India", status: "prospect", owner_id: "own-1", annual_revenue_minor: rupees(6_100_000_000), currency: "INR", created_at: "2025-02-27T09:00:00Z" },
  { id: "comp-14", name: "Umbra Analytics", domain: "umbra.ai", industry: "Software", size_band: "11–50", city: "Pune", country: "India", status: "lead", owner_id: "own-3", annual_revenue_minor: rupees(480_000_000), currency: "INR", created_at: "2025-07-01T09:00:00Z" },
  { id: "comp-15", name: "Riverstone Textiles", domain: "riverstone.co", industry: "Textiles", size_band: "201–500", city: "Surat", country: "India", status: "prospect", owner_id: "own-2", annual_revenue_minor: rupees(2_700_000_000), currency: "INR", created_at: "2024-08-09T09:00:00Z" },
];

/* -------------------------------- contacts ------------------------------- */

export const CONTACTS: Contact[] = [
  { id: "con-1", full_name: "Rajesh Khanna", title: "VP Operations", email: "rajesh.khanna@northwind.co", phone: "+91 98••• ••210", company_id: "comp-1", status: "customer", owner_id: "own-1", last_activity_at: "2026-08-22T11:20:00Z", created_at: "2024-02-12T09:00:00Z" },
  { id: "con-2", full_name: "Anita Desai", title: "Warehouse Director", email: "anita.desai@northwind.co", phone: "+91 99••• ••044", company_id: "comp-1", status: "customer", owner_id: "own-1", last_activity_at: "2026-08-04T15:05:00Z", created_at: "2024-04-01T09:00:00Z" },
  { id: "con-3", full_name: "Suresh Iyer", title: "Chief Information Officer", email: "suresh.iyer@meridiansteel.com", phone: "+91 98••• ••771", company_id: "comp-2", status: "customer", owner_id: "own-4", last_activity_at: "2026-08-18T10:40:00Z", created_at: "2023-11-05T09:00:00Z" },
  { id: "con-4", full_name: "Neha Kapoor", title: "Head of Risk", email: "neha.kapoor@cobaltfin.com", phone: "+91 90••• ••318", company_id: "comp-3", status: "qualified", owner_id: "own-2", last_activity_at: "2026-08-20T09:15:00Z", created_at: "2025-01-24T09:00:00Z" },
  { id: "con-5", full_name: "Arjun Rao", title: "Director of Engineering", email: "arjun.rao@vertexrobotics.io", phone: "+91 97••• ••562", company_id: "comp-4", status: "lead", owner_id: "own-5", last_activity_at: "2026-08-05T13:30:00Z", created_at: "2025-04-10T09:00:00Z" },
  { id: "con-6", full_name: "Meredith Shah", title: "Retail Operations Lead", email: "meredith.shah@harborandco.com", phone: "+91 98••• ••129", company_id: "comp-5", status: "customer", owner_id: "own-6", last_activity_at: "2026-08-12T16:00:00Z", created_at: "2024-06-21T09:00:00Z" },
  { id: "con-7", full_name: "Kavya Menon", title: "Chief Medical Officer", email: "kavya.menon@aurorahealth.in", phone: "+91 99••• ••487", company_id: "comp-6", status: "qualified", owner_id: "own-1", last_activity_at: "2026-08-23T08:50:00Z", created_at: "2024-10-02T09:00:00Z" },
  { id: "con-8", full_name: "Prakash Bhatt", title: "Plant Manager", email: "prakash.bhatt@ironcladmfg.com", phone: "+91 96••• ••903", company_id: "comp-7", status: "lead", owner_id: "own-3", last_activity_at: "2026-07-30T12:10:00Z", created_at: "2025-06-16T09:00:00Z" },
  { id: "con-9", full_name: "Ritu Agarwal", title: "Head of Grid Operations", email: "ritu.agarwal@solsticeenergy.com", phone: "+91 98••• ••655", company_id: "comp-8", status: "customer", owner_id: "own-4", last_activity_at: "2026-08-23T14:25:00Z", created_at: "2023-08-29T09:00:00Z" },
  { id: "con-10", full_name: "Sameer Joshi", title: "Ad Ops Manager", email: "sameer.joshi@beaconmedia.co", phone: "+91 90••• ••240", company_id: "comp-9", status: "lead", owner_id: "own-3", last_activity_at: "2026-08-19T10:05:00Z", created_at: "2025-05-04T09:00:00Z" },
  { id: "con-11", full_name: "Deepak Verma", title: "National Sales Head", email: "deepak.verma@cascadefoods.in", phone: "+91 97••• ••817", company_id: "comp-10", status: "qualified", owner_id: "own-2", last_activity_at: "2026-08-06T11:45:00Z", created_at: "2024-12-07T09:00:00Z" },
  { id: "con-12", full_name: "Lisa Fernandes", title: "VP Product", email: "lisa.fernandes@quantic.dev", phone: "+91 98••• ••390", company_id: "comp-11", status: "customer", owner_id: "own-5", last_activity_at: "2026-08-24T07:35:00Z", created_at: "2024-03-20T09:00:00Z" },
  { id: "con-13", full_name: "Manish Gupta", title: "Sales Director", email: "manish.gupta@pinnaclerealty.in", phone: "+91 99••• ••512", company_id: "comp-12", status: "dormant", owner_id: "own-6", last_activity_at: "2026-07-28T09:00:00Z", created_at: "2023-10-13T09:00:00Z" },
  { id: "con-14", full_name: "Farhan Ali", title: "Logistics Head", email: "farhan.ali@deltafreight.com", phone: "+91 96••• ••178", company_id: "comp-13", status: "qualified", owner_id: "own-1", last_activity_at: "2026-08-17T15:20:00Z", created_at: "2025-03-01T09:00:00Z" },
  { id: "con-15", full_name: "Tara Krishnan", title: "Founder & CEO", email: "tara.krishnan@umbra.ai", phone: "+91 90••• ••006", company_id: "comp-14", status: "lead", owner_id: "own-3", last_activity_at: "2026-08-11T10:00:00Z", created_at: "2025-07-03T09:00:00Z" },
  { id: "con-16", full_name: "Gopal Shah", title: "Operations Head", email: "gopal.shah@riverstone.co", phone: "+91 98••• ••441", company_id: "comp-15", status: "qualified", owner_id: "own-2", last_activity_at: "2026-08-15T12:30:00Z", created_at: "2024-08-11T09:00:00Z" },
  { id: "con-17", full_name: "Vivek Nambiar", title: "IT Director", email: "vivek.nambiar@aurorahealth.in", phone: "+91 97••• ••328", company_id: "comp-6", status: "qualified", owner_id: "own-1", last_activity_at: "2026-08-21T09:40:00Z", created_at: "2025-01-15T09:00:00Z" },
  { id: "con-18", full_name: "Priyanka Sethi", title: "Procurement Lead", email: "priyanka.sethi@solsticeenergy.com", phone: "+91 99••• ••760", company_id: "comp-8", status: "customer", owner_id: "own-4", last_activity_at: "2026-08-10T13:15:00Z", created_at: "2024-05-19T09:00:00Z" },
];

/* --------------------------------- deals --------------------------------- */

export const DEALS: Deal[] = [
  { id: "deal-1", name: "Fleet telematics rollout", company_id: "comp-1", primary_contact_id: "con-1", value_minor: rupees(32_000_000), currency: "INR", stage: "negotiation", owner_id: "own-1", probability: 70, close_date: "2026-08-29", created_at: "2026-05-14T09:00:00Z", last_activity_at: "2026-08-22T11:20:00Z", source: "Referral" },
  { id: "deal-2", name: "Plant-wide ERP integration", company_id: "comp-2", primary_contact_id: "con-3", value_minor: rupees(45_000_000), currency: "INR", stage: "proposal", owner_id: "own-4", probability: 55, close_date: "2026-09-30", created_at: "2026-06-02T09:00:00Z", last_activity_at: "2026-08-18T10:40:00Z", source: "Outbound" },
  { id: "deal-3", name: "Risk analytics platform", company_id: "comp-3", primary_contact_id: "con-4", value_minor: rupees(18_000_000), currency: "INR", stage: "qualified", owner_id: "own-2", probability: 40, close_date: "2026-10-15", created_at: "2026-07-09T09:00:00Z", last_activity_at: "2026-08-20T09:15:00Z", source: "Inbound" },
  { id: "deal-4", name: "Automation pilot", company_id: "comp-4", primary_contact_id: "con-5", value_minor: rupees(6_500_000), currency: "INR", stage: "lead", owner_id: "own-5", probability: 20, close_date: "2026-11-10", created_at: "2026-07-28T09:00:00Z", last_activity_at: "2026-08-05T13:30:00Z", source: "Event" },
  { id: "deal-5", name: "Omnichannel CRM", company_id: "comp-5", primary_contact_id: "con-6", value_minor: rupees(21_000_000), currency: "INR", stage: "closed_won", owner_id: "own-6", probability: 100, close_date: "2026-08-12", created_at: "2026-04-18T09:00:00Z", last_activity_at: "2026-08-12T16:00:00Z", source: "Referral" },
  { id: "deal-6", name: "Patient engagement suite", company_id: "comp-6", primary_contact_id: "con-7", value_minor: rupees(27_500_000), currency: "INR", stage: "proposal", owner_id: "own-1", probability: 60, close_date: "2026-09-05", created_at: "2026-06-11T09:00:00Z", last_activity_at: "2026-08-23T08:50:00Z", source: "Outbound" },
  { id: "deal-7", name: "Shop-floor visibility", company_id: "comp-7", primary_contact_id: "con-8", value_minor: rupees(4_800_000), currency: "INR", stage: "lead", owner_id: "own-3", probability: 15, close_date: "2026-10-28", created_at: "2026-07-15T09:00:00Z", last_activity_at: "2026-07-30T12:10:00Z", source: "Cold call" },
  { id: "deal-8", name: "Grid asset monitoring", company_id: "comp-8", primary_contact_id: "con-9", value_minor: rupees(39_000_000), currency: "INR", stage: "negotiation", owner_id: "own-4", probability: 75, close_date: "2026-08-27", created_at: "2026-05-30T09:00:00Z", last_activity_at: "2026-08-23T14:25:00Z", source: "Partner" },
  { id: "deal-9", name: "Ad ops workspace", company_id: "comp-9", primary_contact_id: "con-10", value_minor: rupees(3_200_000), currency: "INR", stage: "qualified", owner_id: "own-3", probability: 35, close_date: "2026-09-20", created_at: "2026-07-21T09:00:00Z", last_activity_at: "2026-08-19T10:05:00Z", source: "Inbound" },
  { id: "deal-10", name: "Distributor portal", company_id: "comp-10", primary_contact_id: "con-11", value_minor: rupees(14_500_000), currency: "INR", stage: "proposal", owner_id: "own-2", probability: 50, close_date: "2026-08-20", created_at: "2026-06-08T09:00:00Z", last_activity_at: "2026-08-06T11:45:00Z", source: "Outbound" },
  { id: "deal-11", name: "Renewal + expansion", company_id: "comp-11", primary_contact_id: "con-12", value_minor: rupees(8_800_000), currency: "INR", stage: "negotiation", owner_id: "own-5", probability: 80, close_date: "2026-09-02", created_at: "2026-06-25T09:00:00Z", last_activity_at: "2026-08-24T07:35:00Z", source: "Renewal" },
  { id: "deal-12", name: "Sales enablement", company_id: "comp-12", primary_contact_id: "con-13", value_minor: rupees(7_200_000), currency: "INR", stage: "closed_lost", owner_id: "own-6", probability: 0, close_date: "2026-07-28", created_at: "2026-04-02T09:00:00Z", last_activity_at: "2026-07-28T09:00:00Z", source: "Outbound" },
  { id: "deal-13", name: "Route optimization", company_id: "comp-13", primary_contact_id: "con-14", value_minor: rupees(16_000_000), currency: "INR", stage: "qualified", owner_id: "own-1", probability: 45, close_date: "2026-10-08", created_at: "2026-07-04T09:00:00Z", last_activity_at: "2026-08-17T15:20:00Z", source: "Referral" },
  { id: "deal-14", name: "Analytics starter", company_id: "comp-14", primary_contact_id: "con-15", value_minor: rupees(2_400_000), currency: "INR", stage: "lead", owner_id: "own-3", probability: 15, close_date: "2026-11-18", created_at: "2026-07-30T09:00:00Z", last_activity_at: "2026-08-11T10:00:00Z", source: "Event" },
  { id: "deal-15", name: "Order management", company_id: "comp-15", primary_contact_id: "con-16", value_minor: rupees(11_500_000), currency: "INR", stage: "proposal", owner_id: "own-2", probability: 50, close_date: "2026-09-12", created_at: "2026-06-19T09:00:00Z", last_activity_at: "2026-08-15T12:30:00Z", source: "Inbound" },
  { id: "deal-16", name: "Warehouse add-on", company_id: "comp-1", primary_contact_id: "con-2", value_minor: rupees(9_500_000), currency: "INR", stage: "closed_won", owner_id: "own-1", probability: 100, close_date: "2026-08-04", created_at: "2026-05-01T09:00:00Z", last_activity_at: "2026-08-04T15:05:00Z", source: "Expansion" },
  { id: "deal-17", name: "Predictive maintenance", company_id: "comp-2", primary_contact_id: "con-3", value_minor: rupees(13_500_000), currency: "INR", stage: "lead", owner_id: "own-4", probability: 20, close_date: "2026-11-05", created_at: "2026-07-12T09:00:00Z", last_activity_at: "2026-07-26T10:40:00Z", source: "Outbound" },
  { id: "deal-18", name: "Telehealth module", company_id: "comp-6", primary_contact_id: "con-17", value_minor: rupees(20_500_000), currency: "INR", stage: "negotiation", owner_id: "own-1", probability: 65, close_date: "2026-09-08", created_at: "2026-06-16T09:00:00Z", last_activity_at: "2026-08-23T09:40:00Z", source: "Expansion" },
];

/* ------------------------------- activities ------------------------------ */

export const ACTIVITIES: Activity[] = [
  { id: "act-1", type: "call", subject: "Discovery call on telematics scope", body: "Walked through fleet size and current tracking gaps. Rajesh wants a 30-vehicle pilot first.", deal_id: "deal-1", company_id: "comp-1", contact_id: "con-1", owner_id: "own-1", occurred_at: "2026-08-22T11:20:00Z", direction: "outbound" },
  { id: "act-2", type: "email", subject: "Sent revised commercial proposal", body: "Shared the tiered pricing with an annual commitment option.", deal_id: "deal-8", company_id: "comp-8", contact_id: "con-9", owner_id: "own-4", occurred_at: "2026-08-23T14:25:00Z", direction: "outbound" },
  { id: "act-3", type: "meeting", subject: "Solution demo with clinical team", body: "Demoed patient engagement flows to Dr. Menon and two department heads.", deal_id: "deal-6", company_id: "comp-6", contact_id: "con-7", owner_id: "own-1", occurred_at: "2026-08-23T08:50:00Z", direction: null },
  { id: "act-4", type: "email", subject: "Renewal terms confirmed", body: "Lisa approved the expansion seats; contract moving to procurement.", deal_id: "deal-11", company_id: "comp-11", contact_id: "con-12", owner_id: "own-5", occurred_at: "2026-08-24T07:35:00Z", direction: "inbound" },
  { id: "act-5", type: "call", subject: "Qualification call — risk platform", body: "Neha outlined the compliance requirements. Budget approved for Q4.", deal_id: "deal-3", company_id: "comp-3", contact_id: "con-4", owner_id: "own-2", occurred_at: "2026-08-20T09:15:00Z", direction: "outbound" },
  { id: "act-6", type: "note", subject: "Champion identified at IT", body: "Vivek is advocating internally; loop him into the next telehealth review.", deal_id: "deal-18", company_id: "comp-6", contact_id: "con-17", owner_id: "own-1", occurred_at: "2026-08-21T09:40:00Z", direction: null },
  { id: "act-7", type: "meeting", subject: "Onsite ERP workshop", body: "Mapped the integration touchpoints with the plant IT team.", deal_id: "deal-2", company_id: "comp-2", contact_id: "con-3", owner_id: "own-4", occurred_at: "2026-08-18T10:40:00Z", direction: null },
  { id: "act-8", type: "call", subject: "Follow-up on distributor portal", body: "Left a voicemail; awaiting Deepak's feedback on the rollout timeline.", deal_id: "deal-10", company_id: "comp-10", contact_id: "con-11", owner_id: "own-2", occurred_at: "2026-08-06T11:45:00Z", direction: "outbound" },
  { id: "act-9", type: "email", subject: "Case study shared", body: "Sent the Riverstone order-management case study to Gopal.", deal_id: "deal-15", company_id: "comp-15", contact_id: "con-16", owner_id: "own-2", occurred_at: "2026-08-15T12:30:00Z", direction: "outbound" },
  { id: "act-10", type: "task", subject: "Prepare security questionnaire", body: "Aurora infosec review due before contract; assemble SOC 2 evidence.", deal_id: "deal-6", company_id: "comp-6", contact_id: null, owner_id: "own-1", occurred_at: "2026-08-19T16:00:00Z", direction: null },
  { id: "act-11", type: "meeting", subject: "Route optimization scoping", body: "Farhan walked us through the depot network and current planning tools.", deal_id: "deal-13", company_id: "comp-13", contact_id: "con-14", owner_id: "own-1", occurred_at: "2026-08-17T15:20:00Z", direction: null },
  { id: "act-12", type: "call", subject: "Intro call — Beacon ad ops", body: "Sameer interested in the workspace; small team, quick decision cycle.", deal_id: "deal-9", company_id: "comp-9", contact_id: "con-10", owner_id: "own-3", occurred_at: "2026-08-19T10:05:00Z", direction: "inbound" },
  { id: "act-13", type: "email", subject: "Pilot recap and next steps", body: "Summarized the automation pilot outcomes; proposed a phase-2 plan.", deal_id: "deal-4", company_id: "comp-4", contact_id: "con-5", owner_id: "own-5", occurred_at: "2026-08-05T13:30:00Z", direction: "outbound" },
  { id: "act-14", type: "note", subject: "Deal lost — budget frozen", body: "Pinnacle paused all software spend after a leadership change. Revisit in 2027.", deal_id: "deal-12", company_id: "comp-12", contact_id: "con-13", owner_id: "own-6", occurred_at: "2026-07-28T09:00:00Z", direction: null },
  { id: "act-15", type: "meeting", subject: "Kickoff — Harbor & Co go-live", body: "Aligned on rollout milestones with the retail ops team. Contract signed.", deal_id: "deal-5", company_id: "comp-5", contact_id: "con-6", owner_id: "own-6", occurred_at: "2026-08-12T16:00:00Z", direction: null },
  { id: "act-16", type: "call", subject: "Discovery — predictive maintenance", body: "Early interest; Suresh wants an ROI model before committing time.", deal_id: "deal-17", company_id: "comp-2", contact_id: "con-3", owner_id: "own-4", occurred_at: "2026-07-26T10:40:00Z", direction: "outbound" },
  { id: "act-17", type: "email", subject: "Founder intro — Umbra", body: "Tara replied to the event follow-up; scheduling a product walkthrough.", deal_id: "deal-14", company_id: "comp-14", contact_id: "con-15", owner_id: "own-3", occurred_at: "2026-08-11T10:00:00Z", direction: "inbound" },
  { id: "act-18", type: "call", subject: "Warehouse add-on confirmation", body: "Anita confirmed the add-on scope; order processed.", deal_id: "deal-16", company_id: "comp-1", contact_id: "con-2", owner_id: "own-1", occurred_at: "2026-08-04T15:05:00Z", direction: "outbound" },
  { id: "act-19", type: "task", subject: "Send MSA redlines to legal", body: "Solstice legal returned redlines on liability caps; route to our counsel.", deal_id: "deal-8", company_id: "comp-8", contact_id: "con-18", owner_id: "own-4", occurred_at: "2026-08-10T13:15:00Z", direction: null },
  { id: "act-20", type: "note", subject: "Shop-floor pilot cooling off", body: "No response from Prakash in three weeks; try a different stakeholder.", deal_id: "deal-7", company_id: "comp-7", contact_id: "con-8", owner_id: "own-3", occurred_at: "2026-07-30T12:10:00Z", direction: null },
];

/* ---------------------------------- leads --------------------------------- */

// email/phone are already the masked form here — fixtures render exactly what the live
// service returns, and the raw value never reaches this client either way.
/** The moves the live service's state machine allows, in its order (`LeadOut.next_statuses`).
 *  Fixture mode answers like the server does; the live app never reads this table. */
const FIXTURE_LEAD_MOVES: Record<LeadStatus, LeadStatus[]> = {
  new: ["working", "disqualified"],
  working: ["new", "qualified", "disqualified"],
  qualified: ["working", "disqualified"],
  disqualified: ["new", "working"],
  converted: [],
};

export function nextStatusesFor(status: LeadStatus): LeadStatus[] {
  return [...FIXTURE_LEAD_MOVES[status]];
}

const RAW_LEADS: Omit<Lead, "next_statuses">[] = [
  { id: "lead-1", full_name: "Meera Iyer", company: "Solstice Energy", email: "m•••@s•••", phone: "•••••• 4821", source: "form", status: "new", owner_id: null, notes: "Requested a demo from the pricing page.", converted_company_id: null, converted_contact_id: null, converted_deal_id: null, converted_at: null, created_at: "2026-08-20T09:00:00Z" },
  { id: "lead-2", full_name: "Devansh Rao", company: "Kestrel Freight", email: "d•••@k•••", phone: null, source: "manual", status: "working", owner_id: "own-2", notes: "Referral from Vikram's LinkedIn outreach.", converted_company_id: null, converted_contact_id: null, converted_deal_id: null, converted_at: null, created_at: "2026-08-18T14:30:00Z" },
  { id: "lead-3", full_name: "Ananya Bose", company: "Cobalt Financial", email: "a•••@c•••", phone: "•••••• 7710", source: "form", status: "qualified", owner_id: "own-4", notes: "Confirmed budget for Q4; ready to convert.", converted_company_id: null, converted_contact_id: null, converted_deal_id: null, converted_at: null, created_at: "2026-08-10T11:15:00Z" },
  { id: "lead-4", full_name: "Farhan Sheikh", company: null, email: null, phone: "•••••• 9042", source: "import", status: "disqualified", owner_id: "own-3", notes: "Not a fit — outside our supported region.", converted_company_id: null, converted_contact_id: null, converted_deal_id: null, converted_at: null, created_at: "2026-07-29T08:45:00Z" },
  { id: "lead-5", full_name: "Priyanka Nair", company: "Umbra Studio", email: "p•••@u•••", phone: null, source: "form", status: "converted", owner_id: "own-1", notes: "Converted after the founder walkthrough.", converted_company_id: "comp-14", converted_contact_id: "con-15", converted_deal_id: "deal-14", converted_at: "2026-08-11T10:30:00Z", created_at: "2026-07-15T09:00:00Z" },
];

export const LEADS: Lead[] = RAW_LEADS.map((lead) => ({ ...lead, next_statuses: nextStatusesFor(lead.status) }));
