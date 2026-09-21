/**
 * Realistic fixture data, shaped exactly like the real API responses so the whole UI renders
 * without a live login. Swapped out by the `USE_FIXTURES` flag in `client.ts`.
 *
 * A coherent delivery studio: fourteen client projects across six phases, their milestones, and a
 * task board — all pinned to a fixed "today" so relative dates read the same on every run.
 */

import type {
  BurndownPoint,
  BurndownRange,
  MemberOut,
  MilestoneOut,
  Person,
  ProjectOut,
  TaskOut,
} from "./types";

/** The fixed reference "today" the whole product measures relative dates against. */
export const NOW = "2026-08-25";

/* -------------------------------- people -------------------------------- */

const PEOPLE = {
  aarav: { id: "p-01", name: "Aarav Sharma", role: "Delivery Lead" },
  lena: { id: "p-02", name: "Lena Nowak", role: "Product Manager" },
  diego: { id: "p-03", name: "Diego Alvarez", role: "Engineering Lead" },
  priya: { id: "p-04", name: "Priya Nair", role: "Design Lead" },
  kenji: { id: "p-05", name: "Kenji Tanaka", role: "Program Manager" },
  fatima: { id: "p-06", name: "Fatima Al-Sayed", role: "QA Lead" },
  marcus: { id: "p-07", name: "Marcus Feng", role: "Tech Lead" },
  sofia: { id: "p-08", name: "Sofia Marchetti", role: "Account Director" },
  ravi: { id: "p-09", name: "Ravi Deshpande", role: "DevOps Engineer" },
  grace: { id: "p-10", name: "Grace Okoro", role: "Business Analyst" },
} satisfies Record<string, Person>;

export const TEAM: Person[] = Object.values(PEOPLE);

/** The roster, as `GET /members` would return it — the source for every owner/assignee picker
 *  and for the Team screen in fixture mode. */
export const MEMBERS: MemberOut[] = TEAM.map((p) => ({ ...p, directory_id: null }));

/* ------------------------------- projects ------------------------------- */

interface ProjectSeed {
  code: string;
  name: string;
  client: string;
  owner: Person;
  phase: ProjectOut["phase"];
  health: ProjectOut["health"];
  progress: number;
  start: string;
  target: string;
  /** Budget and spend in rupees; converted to paise (minor units) below. */
  budget: number;
  spent: number;
  team: number;
  openTasks: number;
  summary: string;
}

const SEEDS: ProjectSeed[] = [
  {
    code: "PRJ-104", name: "Northwind Trade Portal", client: "Northwind Trading",
    owner: PEOPLE.aarav, phase: "build", health: "on_track", progress: 62,
    start: "2026-04-01", target: "2026-10-15", budget: 12_400_000, spent: 7_320_000,
    team: 9, openTasks: 34,
    summary: "A B2B ordering portal replacing Northwind's spreadsheet-driven purchase flow.",
  },
  {
    code: "PRJ-108", name: "Helios Grid Analytics", client: "Helios Energy",
    owner: PEOPLE.lena, phase: "design", health: "at_risk", progress: 34,
    start: "2026-06-10", target: "2026-12-20", budget: 18_600_000, spent: 5_100_000,
    team: 7, openTasks: 41,
    summary: "Real-time load and outage analytics across Helios's regional grid substations.",
  },
  {
    code: "PRJ-111", name: "Meridian Mobile Banking", client: "Meridian Bank",
    owner: PEOPLE.diego, phase: "build", health: "on_track", progress: 71,
    start: "2026-02-15", target: "2026-09-30", budget: 24_000_000, spent: 16_800_000,
    team: 12, openTasks: 28,
    summary: "A ground-up mobile banking app with biometric auth and instant transfers.",
  },
  {
    code: "PRJ-115", name: "Cobalt Fleet Control", client: "Cobalt Robotics",
    owner: PEOPLE.kenji, phase: "stabilize", health: "on_track", progress: 88,
    start: "2026-01-20", target: "2026-09-05", budget: 15_800_000, spent: 13_900_000,
    team: 8, openTasks: 12,
    summary: "Command-and-control dashboard for Cobalt's autonomous warehouse fleet.",
  },
  {
    code: "PRJ-119", name: "Verdant Supply Tracker", client: "Verdant Foods",
    owner: PEOPLE.grace, phase: "discovery", health: "on_track", progress: 12,
    start: "2026-07-28", target: "2027-02-10", budget: 9_200_000, spent: 940_000,
    team: 5, openTasks: 47,
    summary: "Farm-to-shelf traceability for Verdant's cold-chain produce network.",
  },
  {
    code: "PRJ-122", name: "Atlas Route Optimizer", client: "Atlas Logistics",
    owner: PEOPLE.marcus, phase: "build", health: "delayed", progress: 48,
    start: "2026-05-05", target: "2026-11-12", budget: 16_500_000, spent: 11_200_000,
    team: 9, openTasks: 39,
    summary: "A routing engine cutting last-mile mileage across Atlas's delivery network.",
  },
  {
    code: "PRJ-126", name: "Lumen Patient Portal", client: "Lumen Health",
    owner: PEOPLE.priya, phase: "launch", health: "at_risk", progress: 95,
    start: "2025-11-10", target: "2026-09-01", budget: 21_300_000, spent: 20_100_000,
    team: 10, openTasks: 9,
    summary: "Appointments, records and messaging portal for Lumen's clinic network.",
  },
  {
    code: "PRJ-130", name: "Orion Telemetry Hub", client: "Orion Aerospace",
    owner: PEOPLE.diego, phase: "build", health: "on_track", progress: 55,
    start: "2026-03-18", target: "2026-10-30", budget: 27_500_000, spent: 15_400_000,
    team: 11, openTasks: 33,
    summary: "High-throughput ingestion and replay of flight telemetry streams.",
  },
  {
    code: "PRJ-133", name: "Northwind Vendor Onboarding", client: "Northwind Trading",
    owner: PEOPLE.sofia, phase: "design", health: "on_track", progress: 40,
    start: "2026-06-01", target: "2026-12-05", budget: 8_800_000, spent: 3_100_000,
    team: 6, openTasks: 26,
    summary: "Self-service vendor registration, KYC and catalogue submission.",
  },
  {
    code: "PRJ-137", name: "Helios Field Ops App", client: "Helios Energy",
    owner: PEOPLE.ravi, phase: "stabilize", health: "delayed", progress: 80,
    start: "2026-02-02", target: "2026-09-18", budget: 13_600_000, spent: 12_700_000,
    team: 7, openTasks: 18,
    summary: "Offline-first mobile app for Helios field crews logging inspections.",
  },
  {
    code: "PRJ-140", name: "Meridian Fraud Engine", client: "Meridian Bank",
    owner: PEOPLE.fatima, phase: "build", health: "on_track", progress: 66,
    start: "2026-04-22", target: "2026-11-28", budget: 19_900_000, spent: 12_100_000,
    team: 8, openTasks: 31,
    summary: "A streaming fraud-scoring engine feeding Meridian's transaction pipeline.",
  },
  {
    code: "PRJ-143", name: "Cobalt Analytics Cloud", client: "Cobalt Robotics",
    owner: PEOPLE.lena, phase: "discovery", health: "on_track", progress: 18,
    start: "2026-07-15", target: "2027-01-22", budget: 11_400_000, spent: 1_600_000,
    team: 5, openTasks: 44,
    summary: "A tenant-isolated analytics warehouse for Cobalt's robot fleet telemetry.",
  },
  {
    code: "PRJ-146", name: "Verdant Retail Sync", client: "Verdant Foods",
    owner: PEOPLE.aarav, phase: "launch", health: "on_track", progress: 92,
    start: "2025-12-08", target: "2026-08-29", budget: 10_100_000, spent: 9_400_000,
    team: 6, openTasks: 7,
    summary: "Two-way inventory sync between Verdant's ERP and retail point-of-sale.",
  },
  {
    code: "PRJ-150", name: "Atlas Warehouse IoT", client: "Atlas Logistics",
    owner: PEOPLE.kenji, phase: "live", health: "on_track", progress: 100,
    start: "2025-09-01", target: "2026-06-30", budget: 14_200_000, spent: 13_800_000,
    team: 4, openTasks: 3,
    summary: "Sensor mesh and dashboards monitoring Atlas's automated warehouses. Now in run.",
  },
];

let projectSeq = 0;
const uuid = (prefix: string, n: number): string =>
  `${prefix}-0000-4000-8000-${n.toString().padStart(12, "0")}`;

export const PROJECTS: ProjectOut[] = SEEDS.map((s) => {
  projectSeq += 1;
  return {
    id: uuid("a1a0", projectSeq),
    code: s.code,
    name: s.name,
    client: s.client,
    owner: s.owner,
    phase: s.phase,
    health: s.health,
    progress_pct: s.progress,
    start_date: s.start,
    target_date: s.target,
    budget_minor: s.budget * 100,
    spent_minor: s.spent * 100,
    currency: "INR",
    team_size: s.team,
    open_tasks: s.openTasks,
    milestone_count: 0, // filled in below from MILESTONES
    summary: s.summary,
  };
});

const projectByCode = new Map(PROJECTS.map((p) => [p.code, p]));

/* ------------------------------ milestones ------------------------------ */

interface MilestoneSeed {
  code: string;
  title: string;
  owner: Person;
  due: string;
  status: MilestoneOut["status"];
  progress: number;
}

const MILESTONE_SEEDS: MilestoneSeed[] = [
  { code: "PRJ-111", title: "Beta release to 500 pilot users", owner: PEOPLE.diego, due: "2026-08-28", status: "due_soon", progress: 84 },
  { code: "PRJ-122", title: "Routing algorithm v2 sign-off", owner: PEOPLE.marcus, due: "2026-08-22", status: "slipping", progress: 61 },
  { code: "PRJ-137", title: "Offline sync general availability", owner: PEOPLE.ravi, due: "2026-08-20", status: "slipping", progress: 72 },
  { code: "PRJ-126", title: "Security audit closure", owner: PEOPLE.priya, due: "2026-08-30", status: "at_risk", progress: 55 },
  { code: "PRJ-146", title: "Store rollout — wave 1", owner: PEOPLE.aarav, due: "2026-08-29", status: "due_soon", progress: 78 },
  { code: "PRJ-126", title: "Go-live readiness review", owner: PEOPLE.priya, due: "2026-09-01", status: "due_soon", progress: 62 },
  { code: "PRJ-115", title: "Hardware certification pass", owner: PEOPLE.kenji, due: "2026-09-02", status: "on_track", progress: 90 },
  { code: "PRJ-108", title: "Data model approval", owner: PEOPLE.lena, due: "2026-09-05", status: "at_risk", progress: 30 },
  { code: "PRJ-119", title: "Discovery workshop wrap-up", owner: PEOPLE.grace, due: "2026-09-08", status: "on_track", progress: 40 },
  { code: "PRJ-104", title: "Payment gateway integration", owner: PEOPLE.aarav, due: "2026-09-10", status: "on_track", progress: 48 },
  { code: "PRJ-140", title: "Fraud model training complete", owner: PEOPLE.fatima, due: "2026-09-15", status: "on_track", progress: 58 },
  { code: "PRJ-130", title: "Ingestion pipeline live", owner: PEOPLE.diego, due: "2026-09-20", status: "on_track", progress: 44 },
  { code: "PRJ-133", title: "Vendor pilot kickoff", owner: PEOPLE.sofia, due: "2026-09-25", status: "on_track", progress: 36 },
  { code: "PRJ-111", title: "App store submission", owner: PEOPLE.diego, due: "2026-09-28", status: "on_track", progress: 40 },
  { code: "PRJ-146", title: "ERP cutover", owner: PEOPLE.aarav, due: "2026-08-18", status: "completed", progress: 100 },
  { code: "PRJ-150", title: "Sensor fleet handover to run team", owner: PEOPLE.kenji, due: "2026-06-28", status: "completed", progress: 100 },
];

let milestoneSeq = 0;
export const MILESTONES: MilestoneOut[] = MILESTONE_SEEDS.map((s) => {
  milestoneSeq += 1;
  const project = projectByCode.get(s.code);
  const projectName = project?.name ?? s.code;
  const projectId = project?.id ?? s.code;
  return {
    id: uuid("b2b0", milestoneSeq),
    project_id: projectId,
    project_code: s.code,
    project_name: projectName,
    title: s.title,
    owner: s.owner,
    due_date: s.due,
    status: s.status,
    progress_pct: s.progress,
  };
});

// Backfill each project's milestone count now that the milestones exist.
for (const project of PROJECTS) {
  project.milestone_count = MILESTONES.filter((m) => m.project_code === project.code).length;
}

/* -------------------------------- tasks --------------------------------- */

interface TaskSeed {
  code: string;
  title: string;
  assignee: Person;
  stage: TaskOut["stage"];
  priority: TaskOut["priority"];
  due: string | null;
  points: number;
}

const TASK_SEEDS: TaskSeed[] = [
  // Backlog
  { code: "PRJ-119", title: "Map cold-chain data sources", assignee: PEOPLE.grace, stage: "backlog", priority: "medium", due: "2026-09-04", points: 5 },
  { code: "PRJ-143", title: "Draft tenant-isolation model", assignee: PEOPLE.lena, stage: "backlog", priority: "high", due: "2026-09-06", points: 8 },
  { code: "PRJ-108", title: "Substation ingestion spike", assignee: PEOPLE.ravi, stage: "backlog", priority: "medium", due: null, points: 3 },
  { code: "PRJ-104", title: "Bulk-order CSV import", assignee: PEOPLE.marcus, stage: "backlog", priority: "low", due: "2026-09-12", points: 5 },
  { code: "PRJ-130", title: "Telemetry replay UX brief", assignee: PEOPLE.priya, stage: "backlog", priority: "medium", due: null, points: 2 },
  // In progress
  { code: "PRJ-111", title: "Instant-transfer flow", assignee: PEOPLE.diego, stage: "in_progress", priority: "urgent", due: "2026-08-27", points: 8 },
  { code: "PRJ-122", title: "Rewrite routing cost function", assignee: PEOPLE.marcus, stage: "in_progress", priority: "urgent", due: "2026-08-26", points: 13 },
  { code: "PRJ-140", title: "Streaming feature store", assignee: PEOPLE.fatima, stage: "in_progress", priority: "high", due: "2026-09-01", points: 8 },
  { code: "PRJ-137", title: "Conflict resolution for offline edits", assignee: PEOPLE.ravi, stage: "in_progress", priority: "high", due: "2026-08-28", points: 5 },
  { code: "PRJ-133", title: "KYC document upload", assignee: PEOPLE.sofia, stage: "in_progress", priority: "medium", due: "2026-09-03", points: 5 },
  // Review
  { code: "PRJ-126", title: "Pen-test remediation batch", assignee: PEOPLE.priya, stage: "review", priority: "urgent", due: "2026-08-26", points: 8 },
  { code: "PRJ-115", title: "Fleet command latency fixes", assignee: PEOPLE.kenji, stage: "review", priority: "high", due: "2026-08-27", points: 5 },
  { code: "PRJ-111", title: "Biometric enrolment edge cases", assignee: PEOPLE.diego, stage: "review", priority: "high", due: "2026-08-25", points: 3 },
  { code: "PRJ-146", title: "POS sync reconciliation report", assignee: PEOPLE.aarav, stage: "review", priority: "medium", due: "2026-08-28", points: 3 },
  // Done
  { code: "PRJ-146", title: "ERP cutover runbook", assignee: PEOPLE.aarav, stage: "done", priority: "high", due: "2026-08-18", points: 5 },
  { code: "PRJ-104", title: "Catalogue search indexing", assignee: PEOPLE.marcus, stage: "done", priority: "medium", due: "2026-08-15", points: 8 },
  { code: "PRJ-115", title: "Telemetry retention policy", assignee: PEOPLE.kenji, stage: "done", priority: "low", due: "2026-08-12", points: 2 },
  { code: "PRJ-140", title: "Transaction schema v3", assignee: PEOPLE.fatima, stage: "done", priority: "medium", due: "2026-08-19", points: 5 },
];

let taskSeq = 0;
export const TASKS: TaskOut[] = TASK_SEEDS.map((s) => {
  taskSeq += 1;
  const project = projectByCode.get(s.code);
  return {
    id: uuid("c3c0", taskSeq),
    project_id: project?.id ?? s.code,
    project_code: s.code,
    title: s.title,
    assignee: s.assignee,
    stage: s.stage,
    priority: s.priority,
    due_date: s.due,
    points: s.points,
  };
});

/* ------------------------------- burndown ------------------------------- */

/**
 * Release/sprint burndown of remaining scope (story points). The ideal line falls straight to
 * zero; the actual line trails it, flattening where a delay bit. The last actual point is the
 * live "remaining" figure and later ideal points have no actual yet (null).
 */
export const BURNDOWN: Record<BurndownRange, BurndownPoint[]> = {
  sprint: [
    { label: "Mon", ideal: 96, actual: 96 },
    { label: "Tue", ideal: 80, actual: 88 },
    { label: "Wed", ideal: 64, actual: 71 },
    { label: "Thu", ideal: 48, actual: 58 },
    { label: "Fri", ideal: 32, actual: 44 },
    { label: "Mon", ideal: 16, actual: 27 },
    { label: "Tue", ideal: 0, actual: null },
  ],
  release: [
    { label: "W1", ideal: 420, actual: 420 },
    { label: "W2", ideal: 372, actual: 388 },
    { label: "W3", ideal: 324, actual: 349 },
    { label: "W4", ideal: 276, actual: 300 },
    { label: "W5", ideal: 228, actual: 247 },
    { label: "W6", ideal: 180, actual: 205 },
    { label: "W7", ideal: 132, actual: 158 },
    { label: "W8", ideal: 84, actual: 118 },
    { label: "W9", ideal: 36, actual: null },
    { label: "W10", ideal: 0, actual: null },
  ],
};

/** Representative delta on the burndown headline, per range. */
export const BURNDOWN_DELTA: Record<BurndownRange, number> = {
  sprint: -14.2,
  release: -9.6,
};

/** A representative resource-utilization figure for the KPI row (no capacity service yet). */
export const RESOURCE_UTILIZATION = {
  pct: 87,
  delta: 3.1,
  series: [79, 82, 80, 84, 86, 85, 87],
};
