import { collectPages, defineImportEntity, makeLookup, minorToMajor } from "@opsgrids/client";
import type { ImportColumn, LookupSet, SheetValue } from "@opsgrids/client";

import { createProject, listMembers, listProjects } from "@/api/client";
import type { ListProjectsParams } from "@/api/client";
import type { ProjectHealth, ProjectIn, ProjectOut, ProjectPhase } from "@/api/types";
import { HEALTH_LABEL, PHASE_LABEL } from "@/data/labels";

import {
  labelOfOption,
  numberOf,
  optionOf,
  requiredTextOf,
  resolveReference,
  textOf,
} from "./rowValues";

/** What the Portfolio screen tracks, and so what an export of it must respect. */
export interface PortfolioFilters {
  q: string;
  health: ProjectHealth | "all";
  phase: ProjectPhase | "all";
  sort: NonNullable<ListProjectsParams["sort"]>;
}

const PHASES: readonly ProjectPhase[] = [
  "discovery",
  "design",
  "build",
  "stabilize",
  "launch",
  "live",
];
const PHASE_OPTIONS = PHASES.map((value) => ({ value, label: PHASE_LABEL[value] }));

const HEALTHS: readonly ProjectHealth[] = ["on_track", "at_risk", "delayed"];
const HEALTH_OPTIONS = HEALTHS.map((value) => ({ value, label: HEALTH_LABEL[value] }));

/** The currency budget and spend are in when the row names none, on the sheet and on the wire. */
const DEFAULT_CURRENCY = "INR";

const OWNERS = "owners";

const columns: readonly ImportColumn[] = [
  {
    key: "code",
    header: "Project code",
    kind: "text",
    required: true,
    maxLength: 40,
    pattern: /^[A-Za-z0-9_-]+$/,
    patternHint: "Letters, digits, hyphens and underscores, with no spaces.",
    help: "The project's code. It must be unique and cannot be changed later.",
  },
  {
    key: "name",
    header: "Name",
    kind: "text",
    required: true,
    maxLength: 300,
    help: "The project's name.",
  },
  {
    key: "client",
    header: "Client",
    kind: "text",
    maxLength: 200,
    help: "Who the project is for. Leave blank for an internal project.",
  },
  {
    key: "owner",
    header: "Owner",
    kind: "text",
    lookup: OWNERS,
    help: "The delivery lead, by name as listed on the Team screen. Leave blank for an unassigned project.",
  },
  {
    key: "phase",
    header: "Phase",
    kind: "enum",
    options: PHASE_OPTIONS,
    help: "Where the project is. Leave blank for Discovery.",
  },
  {
    key: "health",
    header: "Health",
    kind: "enum",
    options: HEALTH_OPTIONS,
    help: "How delivery is going. Leave blank for On track.",
  },
  {
    key: "progress_pct",
    header: "Progress (%)",
    kind: "integer",
    min: 0,
    max: 100,
    help: "How much of the project is done, from 0 to 100.",
  },
  {
    key: "start_date",
    header: "Start date",
    kind: "date",
    required: true,
    help: "The day the project starts, for example 2026-03-02.",
  },
  {
    key: "target_date",
    header: "Target date",
    kind: "date",
    required: true,
    help: "The day the project is due, for example 2026-09-30. It cannot be before the start date.",
  },
  {
    key: "budget",
    header: "Budget",
    kind: "money",
    min: 0,
    currencyKey: "currency",
    defaultCurrency: DEFAULT_CURRENCY,
    help: "The approved budget, in whole currency units and not in paise or cents, for example 2500000 or 2500000.50.",
  },
  {
    key: "spent",
    header: "Spent",
    kind: "money",
    min: 0,
    currencyKey: "currency",
    defaultCurrency: DEFAULT_CURRENCY,
    help: "What has been spent so far, in the same units as the budget.",
  },
  {
    key: "currency",
    header: "Currency",
    kind: "text",
    maxLength: 3,
    pattern: /^[A-Za-z]{3}$/,
    patternHint: "A three-letter currency code such as INR or USD.",
    defaultCurrency: DEFAULT_CURRENCY,
    help: `The currency of the budget and the spend. Leave blank for ${DEFAULT_CURRENCY}.`,
  },
  {
    key: "team_size",
    header: "Team size",
    kind: "integer",
    min: 0,
    help: "How many people are on the project.",
  },
  {
    key: "summary",
    header: "Summary",
    kind: "text",
    maxLength: 2000,
    help: "A sentence or two about what the project delivers.",
  },
  {
    key: "open_tasks",
    header: "Open tasks",
    kind: "integer",
    readOnly: true,
    help: "How many tasks are still open. Counted by the system.",
  },
  {
    key: "milestone_count",
    header: "Milestones",
    kind: "integer",
    readOnly: true,
    help: "How many milestones the project has. Counted by the system.",
  },
  {
    key: "id",
    header: "Record ID",
    kind: "text",
    readOnly: true,
    help: "The system's id for this project. Set by the system.",
  },
];

const sampleRows: readonly Record<string, SheetValue>[] = [
  {
    code: "PRJ-104",
    name: "Customer portal redesign",
    client: "Northwind Traders",
    owner: "Meera Iyer",
    phase: "Build",
    health: "On track",
    progress_pct: 45,
    start_date: "2026-03-02",
    target_date: "2026-09-30",
    budget: "2500000.00",
    spent: "1120000.00",
    currency: "INR",
    team_size: 6,
    summary: "Rebuild the customer portal on the new design system.",
  },
  {
    code: "PRJ-105",
    name: "Warehouse scanner rollout",
    client: "Contoso Logistics",
    owner: "Arjun Rao",
    phase: "Discovery",
    health: "At risk",
    progress_pct: 10,
    start_date: "2026-05-18",
    target_date: "2026-12-15",
    budget: "1800000.00",
    spent: "150000.00",
    team_size: 4,
    summary: "Pilot handheld scanners in two warehouses before a wider rollout.",
  },
  {
    code: "PRJ-106",
    name: "Payments gateway migration",
    client: "Fabrikam Retail",
    owner: "Meera Iyer",
    phase: "Stabilize",
    health: "Delayed",
    progress_pct: 80,
    start_date: "2025-11-10",
    target_date: "2026-06-30",
    budget: "120000.00",
    spent: "131500.00",
    currency: "USD",
    team_size: 5,
    summary: "Move card payments to the new gateway. The cut-over is behind plan.",
  },
];

const notes = [
  "Project codes must be unique. A code that already exists, even one held by a deleted project, is skipped and never updated.",
  "Owner is found by the member's name as listed on the Team screen. If two members share a name, the row is refused until one of them is renamed. Leave Owner blank for an unassigned project.",
  "Start date and target date are both required, and the target date cannot be before the start date.",
  `Budget and spent are written in whole currency units, for example 2500000 or 2500000.50, with the currency in the Currency column. A blank currency means ${DEFAULT_CURRENCY}.`,
  "Phase and health can be left blank: a new project starts in Discovery and On track.",
  "Open tasks and milestones are counted by the system and cannot be imported.",
];

function ownerRef(lookups: LookupSet, name: string): string {
  return resolveReference(lookups[OWNERS], name, {
    missing: `There is no team member called "${name}". Add them on the Team screen first, or leave Owner blank.`,
    ambiguous: `More than one team member is called "${name}", so the owner cannot be chosen. Rename one of them on the Team screen, then import again.`,
  });
}

export const portfolioEntity = defineImportEntity<ProjectIn, ProjectOut, PortfolioFilters>({
  id: "projects.portfolio",
  noun: "projects",
  nounSingular: "project",
  fileStem: "projects",
  columns,
  sampleRows,
  notes,
  async loadLookups(signal) {
    const members = await collectPages((page, size) => listMembers({ page, size }), { signal });
    return {
      [OWNERS]: makeLookup(members.map((member) => ({ label: member.name, value: member.id }))),
    };
  },
  validateRow(row) {
    const start = textOf(row, "start_date");
    const target = textOf(row, "target_date");
    return start !== null && target !== null && target < start
      ? "The target date cannot be before the start date."
      : null;
  },
  toPayload(row, { lookups }) {
    const body: ProjectIn = {
      code: requiredTextOf(row, "code", "Project code"),
      name: requiredTextOf(row, "name", "Name"),
      start_date: requiredTextOf(row, "start_date", "Start date"),
      target_date: requiredTextOf(row, "target_date", "Target date"),
    };
    const client = textOf(row, "client");
    if (client !== null) body.client = client;
    const owner = textOf(row, "owner");
    if (owner !== null) body.owner_ref = ownerRef(lookups, owner);
    const phase = optionOf(row, "phase", PHASE_OPTIONS);
    if (phase !== null) body.phase = phase;
    const health = optionOf(row, "health", HEALTH_OPTIONS);
    if (health !== null) body.health = health;
    const progress = numberOf(row, "progress_pct");
    if (progress !== null) body.progress_pct = progress;
    // The engine fills a blank currency cell with the default, so the currency column says nothing
    // about whether one was written: it is sent with an amount, and only then.
    const budget = numberOf(row, "budget");
    if (budget !== null) body.budget_minor = budget;
    const spent = numberOf(row, "spent");
    if (spent !== null) body.spent_minor = spent;
    if (budget !== null || spent !== null) {
      body.currency = (textOf(row, "currency") ?? DEFAULT_CURRENCY).toUpperCase();
    }
    const teamSize = numberOf(row, "team_size");
    if (teamSize !== null) body.team_size = teamSize;
    const summary = textOf(row, "summary");
    if (summary !== null) body.summary = summary;
    return body;
  },
  async create(payload) {
    const project = await createProject(payload);
    return { id: project.id };
  },
  export: {
    fetchPage: ({ page, size, filters }) =>
      listProjects({
        q: filters.q,
        health: filters.health,
        phase: filters.phase,
        sort: filters.sort,
        page,
        size,
      }),
    toRow: (project) => ({
      code: project.code,
      name: project.name,
      client: project.client,
      // An unowned project comes back as "Unassigned" with no id: that is not a member's name.
      owner: project.owner.id === "" ? "" : project.owner.name,
      phase: labelOfOption(PHASE_OPTIONS, project.phase),
      health: labelOfOption(HEALTH_OPTIONS, project.health),
      progress_pct: project.progress_pct,
      start_date: project.start_date,
      target_date: project.target_date,
      budget: minorToMajor(project.budget_minor, project.currency),
      spent: minorToMajor(project.spent_minor, project.currency),
      currency: project.currency,
      team_size: project.team_size,
      summary: project.summary,
      open_tasks: project.open_tasks,
      milestone_count: project.milestone_count,
      id: project.id,
    }),
  },
});
