/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI renders
 * without a live login. Swapped out by the `USE_FIXTURES` flag in `client.ts`.
 *
 * One coherent delivery studio, "Atmora Studio": twelve people, fourteen client engagements, a
 * task board, the current week's timesheets and today's standups. Money is integer minor units
 * (US-cents) throughout.
 */

import { PEOPLE } from "@/data/people";
import type {
  Project,
  StandupEntry,
  Task,
  TimesheetCell,
  TimesheetRow,
  TimesheetStatus,
} from "./types";

/** The day the app treats as "today" for the week strip and standups. */
export const TODAY = "2026-08-24";

const USD = "USD";

/* ------------------------------------ projects ------------------------------------ */

export const PROJECTS: Project[] = [
  { id: "PB-01", code: "PB-01", name: "Helios Web Platform", client: "Helios Retail", status: "on_track", progress_pct: 62, budget_minor: 24_000_000, spent_minor: 15_000_000, currency: USD, owner_id: "p07", start_date: "2026-05-04", due_date: "2026-10-15", billable: true, open_tasks: 14 },
  { id: "PB-02", code: "PB-02", name: "Aurora Mobile App", client: "Aurora Health", status: "at_risk", progress_pct: 45, budget_minor: 18_000_000, spent_minor: 11_000_000, currency: USD, owner_id: "p02", start_date: "2026-06-01", due_date: "2026-09-30", billable: true, open_tasks: 21 },
  { id: "PB-03", code: "PB-03", name: "Meridian Data Warehouse", client: "Meridian Bank", status: "blocked", progress_pct: 30, budget_minor: 32_000_000, spent_minor: 14_000_000, currency: USD, owner_id: "p11", start_date: "2026-04-20", due_date: "2026-11-20", billable: true, open_tasks: 9 },
  { id: "PB-04", code: "PB-04", name: "Nimbus Design System", client: "Atmora Studio", status: "on_track", progress_pct: 78, budget_minor: 9_000_000, spent_minor: 6_400_000, currency: USD, owner_id: "p03", start_date: "2026-05-18", due_date: "2026-09-12", billable: false, open_tasks: 6 },
  { id: "PB-05", code: "PB-05", name: "Coastal Commerce Replatform", client: "Coastal Group", status: "on_track", progress_pct: 54, budget_minor: 41_000_000, spent_minor: 20_500_000, currency: USD, owner_id: "p04", start_date: "2026-03-30", due_date: "2026-12-05", billable: true, open_tasks: 27 },
  { id: "PB-06", code: "PB-06", name: "Vertex Analytics Portal", client: "Vertex Labs", status: "at_risk", progress_pct: 40, budget_minor: 15_000_000, spent_minor: 9_600_000, currency: USD, owner_id: "p07", start_date: "2026-05-11", due_date: "2026-08-20", billable: true, open_tasks: 17 },
  { id: "PB-07", code: "PB-07", name: "Lumen Marketing Site", client: "Lumen Media", status: "done", progress_pct: 100, budget_minor: 4_800_000, spent_minor: 4_650_000, currency: USD, owner_id: "p05", start_date: "2026-04-06", due_date: "2026-08-01", billable: true, open_tasks: 0 },
  { id: "PB-08", code: "PB-08", name: "Orion Payments Integration", client: "Orion Pay", status: "on_track", progress_pct: 68, budget_minor: 22_000_000, spent_minor: 13_800_000, currency: USD, owner_id: "p06", start_date: "2026-05-25", due_date: "2026-10-30", billable: true, open_tasks: 11 },
  { id: "PB-09", code: "PB-09", name: "Stellar CRM Migration", client: "Stellar Foods", status: "blocked", progress_pct: 22, budget_minor: 26_000_000, spent_minor: 8_000_000, currency: USD, owner_id: "p02", start_date: "2026-06-15", due_date: "2026-11-14", billable: true, open_tasks: 8 },
  { id: "PB-10", code: "PB-10", name: "Terra Field Ops App", client: "Terra Energy", status: "on_track", progress_pct: 58, budget_minor: 19_500_000, spent_minor: 10_100_000, currency: USD, owner_id: "p09", start_date: "2026-05-02", due_date: "2026-10-08", billable: true, open_tasks: 13 },
  { id: "PB-11", code: "PB-11", name: "Cobalt Insights Dashboard", client: "Cobalt Retail", status: "at_risk", progress_pct: 48, budget_minor: 13_000_000, spent_minor: 14_100_000, currency: USD, owner_id: "p11", start_date: "2026-06-08", due_date: "2026-09-18", billable: true, open_tasks: 10 },
  { id: "PB-12", code: "PB-12", name: "Pine UX Research Sprint", client: "Pine Living", status: "done", progress_pct: 100, budget_minor: 3_600_000, spent_minor: 3_520_000, currency: USD, owner_id: "p10", start_date: "2026-06-29", due_date: "2026-07-25", billable: true, open_tasks: 0 },
  { id: "PB-13", code: "PB-13", name: "Atlas Checkout Redesign", client: "Atlas Sports", status: "on_track", progress_pct: 71, budget_minor: 17_500_000, spent_minor: 11_900_000, currency: USD, owner_id: "p03", start_date: "2026-04-27", due_date: "2026-09-27", billable: true, open_tasks: 12 },
  { id: "PB-14", code: "PB-14", name: "Zephyr Internal Tooling", client: "Atmora Studio", status: "on_track", progress_pct: 35, budget_minor: 6_000_000, spent_minor: 1_900_000, currency: USD, owner_id: "p02", start_date: "2026-07-06", due_date: "2026-12-18", billable: false, open_tasks: 7 },
];

/* -------------------------------------- tasks -------------------------------------- */

export const TASKS: Task[] = [
  // Backlog
  { id: "t231", key: "PB-231", title: "Wire up saved-filter presets", status: "backlog", assignee_id: "p05", project_id: "PB-01", estimate_hours: 8, priority: "medium", tags: ["frontend"], due_date: null },
  { id: "t244", key: "PB-244", title: "Draft data-retention policy", status: "backlog", assignee_id: "p11", project_id: "PB-03", estimate_hours: 5, priority: "high", tags: ["data", "compliance"], due_date: "2026-08-27" },
  { id: "t258", key: "PB-258", title: "Spike: offline sync strategy", status: "backlog", assignee_id: "p04", project_id: "PB-10", estimate_hours: 13, priority: "medium", tags: ["research", "mobile"], due_date: null },
  { id: "t262", key: "PB-262", title: "Design empty states for portal", status: "backlog", assignee_id: "p03", project_id: "PB-06", estimate_hours: 6, priority: "low", tags: ["design"], due_date: null },
  { id: "t270", key: "PB-270", title: "Set up load-testing harness", status: "backlog", assignee_id: "p09", project_id: "PB-08", estimate_hours: 8, priority: "medium", tags: ["devops"], due_date: "2026-08-28" },
  { id: "t275", key: "PB-275", title: "Audit colour contrast across app", status: "backlog", assignee_id: "p10", project_id: "PB-13", estimate_hours: 4, priority: "low", tags: ["a11y", "design"], due_date: null },
  // In progress
  { id: "t198", key: "PB-198", title: "Checkout payment error handling", status: "in_progress", assignee_id: "p06", project_id: "PB-08", estimate_hours: 10, priority: "high", tags: ["backend"], due_date: "2026-08-26" },
  { id: "t210", key: "PB-210", title: "Responsive nav for tablet", status: "in_progress", assignee_id: "p05", project_id: "PB-01", estimate_hours: 6, priority: "medium", tags: ["frontend"], due_date: "2026-08-25" },
  { id: "t215", key: "PB-215", title: "ETL pipeline for ledger sync", status: "in_progress", assignee_id: "p11", project_id: "PB-03", estimate_hours: 16, priority: "high", tags: ["data"], due_date: "2026-08-29" },
  { id: "t221", key: "PB-221", title: "Field-ops GPS check-in", status: "in_progress", assignee_id: "p04", project_id: "PB-10", estimate_hours: 12, priority: "medium", tags: ["mobile"], due_date: "2026-08-27" },
  { id: "t226", key: "PB-226", title: "Onboarding flow copy pass", status: "in_progress", assignee_id: "p07", project_id: "PB-13", estimate_hours: 3, priority: "low", tags: ["content"], due_date: null },
  { id: "t229", key: "PB-229", title: "Migrate auth to JWKS verify", status: "in_progress", assignee_id: "p06", project_id: "PB-05", estimate_hours: 8, priority: "high", tags: ["backend", "security"], due_date: "2026-08-28" },
  // Review
  { id: "t180", key: "PB-180", title: "Dashboard KPI tiles", status: "review", assignee_id: "p05", project_id: "PB-06", estimate_hours: 6, priority: "medium", tags: ["frontend"], due_date: "2026-08-24" },
  { id: "t184", key: "PB-184", title: "Invoice PDF export", status: "review", assignee_id: "p04", project_id: "PB-08", estimate_hours: 5, priority: "medium", tags: ["backend"], due_date: null },
  { id: "t190", key: "PB-190", title: "Design tokens for dark theme", status: "review", assignee_id: "p03", project_id: "PB-04", estimate_hours: 4, priority: "low", tags: ["design"], due_date: null },
  { id: "t193", key: "PB-193", title: "Search relevance tuning", status: "review", assignee_id: "p11", project_id: "PB-05", estimate_hours: 8, priority: "medium", tags: ["data"], due_date: "2026-08-25" },
  { id: "t196", key: "PB-196", title: "Cypress smoke suite", status: "review", assignee_id: "p08", project_id: "PB-13", estimate_hours: 6, priority: "medium", tags: ["qa"], due_date: null },
  // Done
  { id: "t150", key: "PB-150", title: "Project scaffolding & CI", status: "done", assignee_id: "p02", project_id: "PB-05", estimate_hours: 5, priority: "medium", tags: ["setup"], due_date: null },
  { id: "t162", key: "PB-162", title: "Brand guidelines handoff", status: "done", assignee_id: "p10", project_id: "PB-12", estimate_hours: 4, priority: "low", tags: ["design"], due_date: null },
  { id: "t171", key: "PB-171", title: "Landing page build", status: "done", assignee_id: "p05", project_id: "PB-07", estimate_hours: 8, priority: "medium", tags: ["frontend"], due_date: null },
  { id: "t175", key: "PB-175", title: "API contract review", status: "done", assignee_id: "p02", project_id: "PB-08", estimate_hours: 3, priority: "medium", tags: ["backend"], due_date: null },
  { id: "t178", key: "PB-178", title: "Accessibility audit v1", status: "done", assignee_id: "p10", project_id: "PB-13", estimate_hours: 6, priority: "low", tags: ["a11y"], due_date: null },
];

/* ------------------------------------ week dates ----------------------------------- */

function isoOf(d: Date): string {
  return d.toISOString().slice(0, 10);
}

/** Monday of the week containing `iso`. */
function mondayOf(iso: string): string {
  const base = new Date(`${iso}T00:00:00Z`);
  const dow = (base.getUTCDay() + 6) % 7; // 0 = Monday
  base.setUTCDate(base.getUTCDate() - dow);
  return isoOf(base);
}

export const WEEK_START = mondayOf(TODAY);

/** The seven ISO dates of the current week, Monday first. */
export const WEEK_DATES: string[] = Array.from({ length: 7 }, (_, i) => {
  const d = new Date(`${WEEK_START}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + i);
  return isoOf(d);
});

/* ------------------------------------ timesheets ----------------------------------- */

/** A stable pseudo-random in [0,1) so generated hours vary but never change between renders. */
function seeded(n: number): number {
  const x = Math.sin(n) * 10000;
  return x - Math.floor(x);
}

function roundHalf(n: number): number {
  return Math.round(n * 2) / 2;
}

const BILLABLE_RATIO = [0.6, 0.85, 0.82, 0.9, 0.92, 0.88, 0.8, 0.86, 0.84, 0.78, 0.91, 0.7];
const TIMESHEET_STATUS: TimesheetStatus[] = [
  "draft", "submitted", "approved", "approved", "submitted", "approved",
  "submitted", "approved", "draft", "approved", "submitted", "approved",
];

function buildCells(personIndex: number, capacity: number): TimesheetCell[] {
  const dailyTarget = capacity / 5;
  const ratio = BILLABLE_RATIO[personIndex] ?? 0.85;
  return WEEK_DATES.map((date, day) => {
    const isWeekend = day >= 5;
    if (isWeekend) {
      // A little weekend crunch for two people; everyone else is off.
      const crunch = personIndex % 5 === 0 && day === 5;
      const entered = crunch ? roundHalf(2 + seeded(personIndex * 13 + day) * 3) : 0;
      return { date, entered_hours: entered, billable_hours: entered };
    }
    const jitter = seeded(personIndex * 7 + day) * 2 - 1; // −1..+1
    const entered = Math.max(0, roundHalf(dailyTarget + jitter));
    const billable = Math.min(entered, roundHalf(entered * ratio));
    return { date, entered_hours: entered, billable_hours: billable };
  });
}

export const TIMESHEETS: TimesheetRow[] = PEOPLE.map((p, i) => ({
  person_id: p.id,
  week_start: WEEK_START,
  status: TIMESHEET_STATUS[i] ?? "draft",
  cells: buildCells(i, p.capacity_hours),
}));

/* ------------------------------------- standups ------------------------------------ */

export const STANDUPS: StandupEntry[] = [
  { id: "s01", person_id: "p02", date: TODAY, yesterday: "Unblocked the Stellar CRM data mapping and paired with Kwame on the auth migration.", today: "Break down the CRM migration epic and re-scope PB-09 with the client.", blockers: "Waiting on Stellar's sandbox credentials — escalated to their IT team." },
  { id: "s02", person_id: "p03", date: TODAY, yesterday: "Shipped dark-theme design tokens for review and refined the Atlas checkout flow.", today: "Empty-state designs for the Vertex portal and a contrast pass with Hannah.", blockers: null },
  { id: "s03", person_id: "p04", date: TODAY, yesterday: "Built the field-ops GPS check-in happy path; wrote the invoice PDF export.", today: "Handle offline edge cases on the check-in and open the PDF export for review.", blockers: null },
  { id: "s04", person_id: "p05", date: TODAY, yesterday: "Finished the responsive tablet nav and started saved-filter presets.", today: "Polish KPI tiles in review and pick up the presets ticket.", blockers: null },
  { id: "s05", person_id: "p06", date: TODAY, yesterday: "Wired checkout payment error states; reviewed Diego's API contract.", today: "Finish JWKS verification for Coastal auth and cover it with tests.", blockers: "Orion's staging gateway is flaky — retrying against the sandbox." },
  { id: "s06", person_id: "p08", date: TODAY, yesterday: "Extended the Cypress smoke suite to cover checkout and login.", today: "Triage two flaky specs and add visual snapshots for the KPI tiles.", blockers: null },
  { id: "s07", person_id: "p09", date: TODAY, yesterday: "Stood up the load-testing harness skeleton and tuned CI cache.", today: "Baseline the Orion payments load test and document the thresholds.", blockers: null },
  { id: "s08", person_id: "p10", date: TODAY, yesterday: "Ran the colour-contrast audit and logged eleven findings.", today: "Group the findings by severity and hand the top ones to design.", blockers: null },
  { id: "s09", person_id: "p11", date: TODAY, yesterday: "Continued the ledger ETL pipeline; profiled the slow warehouse query.", today: "Get the ETL incremental load working end-to-end for Meridian.", blockers: "Meridian warehouse is read-locked during their close — blocked until Thursday." },
];
