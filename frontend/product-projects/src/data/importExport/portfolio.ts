import { RowProblem, collectPages, defineImportEntity, makeLookup, minorToMajor } from "@opsgrids/client";
import type { ImportColumn, SheetValue, UpdateSpec } from "@opsgrids/client";

import { createProject, listMembers, listProjects, updateProject } from "@/api/client";
import type { ListProjectsParams } from "@/api/client";
import type { ProjectHealth, ProjectIn, ProjectOut, ProjectPhase, ProjectUpdate } from "@/api/types";
import { HEALTH_LABEL, PHASE_LABEL } from "@/data/labels";

import {
  labelOfOption,
  numberOf,
  optionOf,
  requiredTextOf,
  resolveLookup,
  resolveReference,
  sameKey,
  sameLines,
  storedText,
  textOf,
  upperCased,
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

/** The largest value of a 32-bit integer column (`team_size`, `open_tasks`). */
const MAX_INT32 = 2_147_483_647;

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
    lookupHint:
      "Use the team member's name exactly as on the Team screen. If two members share a name, rename one first.",
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
    // The column is a 32-bit integer: a larger number is not a validation error but a server error.
    max: MAX_INT32,
    help: "How many people are on the project.",
  },
  {
    key: "summary",
    header: "Summary",
    kind: "text",
    multiline: true,
    maxLength: 2000,
    help: "A sentence or two about what the project delivers. It may run over more than one line.",
  },
  {
    key: "open_tasks",
    header: "Open tasks",
    kind: "integer",
    readOnly: true,
    help: "The project's stored open-work figure, not counted from its tasks. Imported projects start at 0.",
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
  "Project codes must be unique. With \"Add new records only\", a code that already exists, even one held by a deleted project, is skipped and never updated.",
  "Owner is found by the member's name as listed on the Team screen. If two members share a name, the row is refused until one of them is renamed. Leave Owner blank for an unassigned project.",
  "Start date and target date are both required, and the target date cannot be before the start date.",
  `Budget and spent are written in whole currency units, for example 2500000 or 2500000.50, with the currency in the Currency column. A blank currency means ${DEFAULT_CURRENCY}.`,
  "Phase and health can be left blank: a new project starts in Discovery and On track.",
  "Milestones are counted from the project's milestones. Open tasks is a stored figure, not counted from tasks. Neither can be imported, and an imported project starts with 0 open tasks.",
  "A row with a Record ID came out of the system and is already there, so \"Add new records only\" refuses it. To use an exported file as a starting point, delete its Record ID column first. With \"Add new and update existing\", the Record ID says which project to update.",
  "Updating: the project code says which project a row is about and cannot be changed. A row that names a project by Record ID must carry that project's own code or leave it blank.",
  "Updating: [clear] empties Client, Owner and Summary. Every other column keeps its value when blank, and Start date and Target date can never be empty.",
  "Updating: the start and target dates are checked together with the project's stored dates, so a Start date after the stored target, or a Target date before the stored start, is refused.",
  "Updating: a blank Currency keeps the project's own currency, so its amounts are read in that currency and not in INR. Changing the currency does not convert the budget or the spend: a row that changes it must also give Budget and Spent in the new currency (a blank one is allowed only while that amount is 0), or it is refused.",
];

/**
 * How an existing project is updated from a sheet. `PATCH /projects-workspace/projects/{code}`
 * takes the code in the URL, has no null guard (a null on a required column is a 409, and on
 * either date a 500) and, unlike most endpoints, silently drops keys it does not know while
 * answering 200. So the code is `immutable` and refused when it differs, only the owner is cleared
 * with `null` and Client and Summary with `""` (their columns are never null), and no other column
 * can be cleared. The owner is found by member name and sent as the member's ref. The server
 * checks the dates after merging them with the stored ones and relabels a budget when the
 * currency changes instead of converting it; `check` does both before anything is sent.
 */
const projectUpdate: UpdateSpec<ProjectOut> = {
  loadExisting: (ctx) =>
    collectPages((page, size) => listProjects({ page, size }), {
      signal: ctx.signal,
      key: (project) => project.id,
    }),
  idOf: (project) => project.id,
  keyColumn: "code",
  keyOf: (project) => project.code,
  currencyOf: (project) => project.currency,
  fields: [
    { column: "code", current: (p) => p.code, immutable: true, same: sameKey },
    { column: "name", current: (p) => p.name },
    { column: "client", current: (p) => storedText(p.client), clear: "" },
    {
      column: "owner",
      field: "owner_ref" satisfies keyof ProjectUpdate,
      // An unowned project comes back as "Unassigned" with no id.
      current: (p) => storedText(p.owner.id),
      resolve: resolveLookup(OWNERS),
      clear: null,
    },
    { column: "phase", current: (p) => p.phase },
    { column: "health", current: (p) => p.health },
    { column: "progress_pct", current: (p) => p.progress_pct },
    { column: "start_date", current: (p) => p.start_date },
    { column: "target_date", current: (p) => p.target_date },
    { column: "budget", field: "budget_minor" satisfies keyof ProjectUpdate, current: (p) => p.budget_minor },
    { column: "spent", field: "spent_minor" satisfies keyof ProjectUpdate, current: (p) => p.spent_minor },
    { column: "currency", current: (p) => p.currency.toUpperCase(), resolve: upperCased },
    { column: "team_size", current: (p) => p.team_size },
    { column: "summary", current: (p) => storedText(p.summary), clear: "", same: sameLines },
  ],
  check(row, project) {
    const start = textOf(row, "start_date") ?? project.start_date;
    const target = textOf(row, "target_date") ?? project.target_date;
    if (target < start) {
      throw new RowProblem(
        `The target date cannot be before the start date. With this row the project would start on ${start} and be due on ${target}.`,
      );
    }

    const named = textOf(row, "currency")?.toUpperCase();
    if (named === undefined || named === project.currency.toUpperCase()) return;
    // The amounts are stored in the old currency and the server relabels them: an amount the row
    // does not restate would silently become a sum in the new currency. Zero has no unit to lose.
    const unstated = [
      { header: "Budget", blank: numberOf(row, "budget") === null, stored: project.budget_minor },
      { header: "Spent", blank: numberOf(row, "spent") === null, stored: project.spent_minor },
    ].filter(({ blank, stored }) => blank && stored !== 0);
    if (unstated.length > 0) {
      throw new RowProblem(
        `Currency would change from ${project.currency} to ${named}, but changing it does not convert amounts. Give ${unstated.map(({ header }) => header).join(" and ")} in ${named} too, or leave Currency blank.`,
      );
    }
  },
  async send(project, body) {
    // The code is the URL's, never the body's. The body holds only the keys this spec's fields
    // name, all of them keys of `ProjectUpdate`.
    await updateProject(project.code, body as ProjectUpdate);
  },
};

export const portfolioEntity = defineImportEntity<ProjectIn, ProjectOut, PortfolioFilters>({
  id: "projects.portfolio",
  noun: "projects",
  nounSingular: "project",
  fileStem: "projects",
  columns,
  sampleRows,
  notes,
  recordIdKey: "id",
  update: projectUpdate,
  async loadLookups(signal) {
    const members = await collectPages((page, size) => listMembers({ page, size }), {
      signal,
      key: (member) => member.id,
    });
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
    if (owner !== null) body.owner_ref = resolveReference(lookups[OWNERS], owner);
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
