import { useCallback, useMemo } from "react";
import type { ReactNode } from "react";
import { Link, useNavigate } from "react-router-dom";

import { Can, problemMessage, useAsync, useCan } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  BriefcaseIcon,
  ChartCard,
  CheckCircleIcon,
  DonutChart,
  FlagIcon,
  isStatusWord,
  MetricCard,
  PlusIcon,
  StatusBadge,
} from "@opsgrids/ui";
import type { DonutSlice } from "@opsgrids/ui";

import {
  countCompletedProjects,
  getAllMilestones,
  getAllProjects,
  getAllTasks,
  listMyTasks,
} from "@/api/client";
import type { MilestoneOut, ProjectHealth, ProjectOut, TaskOut } from "@/api/types";
import {
  activeProjects,
  healthBreakdown,
  milestonesDue,
  overdueTasks,
  sortByDueDate,
} from "@/data/derive";
import { MILESTONE_STATUS_LABEL, STAGE_LABEL } from "@/data/labels";
import { formatDate, today } from "@/lib/format";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  ProgressBar,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";

const WRITE = "projects.portfolio.project.write";

const COUNT = new Intl.NumberFormat("en-IN");

/** A stable empty reference so the derived-data memos don't churn while a source is loading. */
const EMPTY_PROJECTS: ProjectOut[] = [];
const EMPTY_MILESTONES: MilestoneOut[] = [];
const EMPTY_TASKS: TaskOut[] = [];

const HEALTH_TONE: Record<ProjectHealth, "accent" | "positive" | "info" | "cyan" | "warning" | "danger"> = {
  on_track: "positive",
  at_risk: "warning",
  delayed: "danger",
};

/** A due date sorts before an undated one, which sorts last — the same rule `derive.ts` applies
 *  to milestones, needed again here for tasks (a different shape, same two fields). */
function byDueDateSoonestFirst(a: { due_date: string | null }, b: { due_date: string | null }): number {
  if (a.due_date === null && b.due_date === null) return 0;
  if (a.due_date === null) return 1;
  if (b.due_date === null) return -1;
  return a.due_date.localeCompare(b.due_date);
}

/** The Overview's top bar: breadcrumb-free title, a sentence of orientation, then "New project" —
 *  gated on the write grant, not merely hidden by convention (the dashboard audit flagged this
 *  control as reachable with no permission check at all). */
function OverviewHeader({ onNewProject }: { onNewProject: () => void }) {
  return (
    <PageHeader
      title="Overview"
      description="Delivery across every project."
      actions={
        <Can permission={WRITE}>
          <Button leadingIcon={<PlusIcon className="size-4" />} onClick={onNewProject}>
            New project
          </Button>
        </Can>
      }
    />
  );
}

/** The KPI row's shared branch: a skeleton while loading, an inline retry on failure, or the
 *  real card — one shape per source, so a failed task scan never blanks the projects the
 *  portfolio call already returned. */
function KpiSlot({
  status,
  label,
  error,
  onRetry,
  children,
}: {
  status: "loading" | "success" | "error";
  label: string;
  error: Error | null;
  onRetry: () => void;
  children: ReactNode;
}) {
  if (status === "loading") {
    return (
      <Card className="flex items-start gap-4 p-4 sm:p-6" role="status" aria-busy="true">
        <span className="sr-only">{`Loading ${label}`}</span>
        <Skeleton className="size-11 shrink-0 rounded-tile" />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <Skeleton className="h-3 w-24 rounded-full" />
          <Skeleton className="h-7 w-16 rounded-tile" />
        </div>
      </Card>
    );
  }
  if (status === "error") {
    return (
      <Card className="flex flex-col justify-center gap-3 p-4 sm:p-6">
        <p className="text-sm font-semibold text-ink">{`Couldn't load ${label.toLowerCase()}.`}</p>
        <p className="text-xs text-muted">{problemMessage(error)}</p>
        <Button variant="secondary" size="sm" onClick={onRetry}>
          Try again
        </Button>
      </Card>
    );
  }
  return <>{children}</>;
}

/**
 * The Project Manager's home: delivery KPIs with no invented trend, the portfolio's own progress
 * and health, the caller's own tasks, and the deadlines coming up next.
 *
 * `utilization_pct` and the release/sprint burndown are gone from this screen entirely —
 * `/projects-workspace/overview` now answers both as `null` (this service has no capacity source
 * and no dated delivery snapshot), and a placeholder number here previously looked live and was
 * not (dashboard audit, §3.9). "On hold" is not a project phase this service has (`discovery`,
 * `design`, `build`, `stabilize`, `launch`, `live`), so the KPI row does not carry a card for it;
 * "Milestones due" takes that slot instead, with a real count behind it.
 */
export function OverviewPage() {
  const navigate = useNavigate();
  const canWrite = useCan(WRITE);

  const projectsState = useAsync(useCallback(getAllProjects, []), []);
  const milestonesState = useAsync(useCallback(() => getAllMilestones(), []), []);
  const tasksState = useAsync(useCallback(() => getAllTasks(), []), []);
  const completedState = useAsync(useCallback(countCompletedProjects, []), []);
  const myTasksState = useAsync(useCallback(() => listMyTasks(100), []), []);

  const projects = useMemo(
    () => (projectsState.status === "success" ? projectsState.data : EMPTY_PROJECTS),
    [projectsState],
  );
  const milestones = useMemo(
    () => (milestonesState.status === "success" ? milestonesState.data : EMPTY_MILESTONES),
    [milestonesState],
  );
  const tasks = useMemo(
    () => (tasksState.status === "success" ? tasksState.data : EMPTY_TASKS),
    [tasksState],
  );
  const myTasks = useMemo(
    () => (myTasksState.status === "success" ? myTasksState.data.items : EMPTY_TASKS),
    [myTasksState],
  );

  const active = activeProjects(projects);
  const dueSoon = milestonesDue(milestones, today());
  const overdue = overdueTasks(tasks, today());
  const completed = completedState.status === "success" ? completedState.data : 0;

  const health = useMemo(() => healthBreakdown(projects), [projects]);
  const healthSlices: DonutSlice[] = health.map((slice) => ({
    key: slice.key,
    label: slice.label,
    value: slice.count,
    tone: HEALTH_TONE[slice.key],
  }));

  const topProjects = useMemo(
    () =>
      [...projects]
        .filter((p) => p.phase !== "live")
        .sort((a, b) => a.target_date.localeCompare(b.target_date))
        .slice(0, 8),
    [projects],
  );

  const myUpcoming = useMemo(
    () => myTasks.filter((t) => t.stage !== "done").sort(byDueDateSoonestFirst).slice(0, 6),
    [myTasks],
  );

  const deadlines = useMemo(
    () => sortByDueDate(milestones.filter((m) => m.status !== "completed")).slice(0, 6),
    [milestones],
  );

  const goToNewProject = () => navigate("/portfolio?new=1");

  const projectColumns: Column<ProjectOut>[] = [
    {
      key: "project",
      header: "Project",
      render: (row) => (
        <div className="flex min-w-0 flex-col">
          <span className="truncate font-semibold text-ink">{row.name}</span>
          <span className="text-xs text-muted">{row.code}</span>
        </div>
      ),
      mobile: "title",
    },
    {
      key: "health",
      header: "Health",
      render: (row) => <StatusBadge status={row.health} />,
      mobile: "status",
    },
    {
      key: "progress",
      header: "Progress",
      render: (row) => <ProgressBar value={row.progress_pct} health={row.health} showLabel />,
      mobile: "field",
    },
    {
      key: "target",
      header: "Target date",
      render: (row) => formatDate(row.target_date),
      mobile: "field",
      cellClassName: "whitespace-nowrap",
    },
  ];

  const taskColumns: Column<TaskOut>[] = [
    {
      key: "title",
      header: "Task",
      render: (row) => <span className="font-semibold text-ink">{row.title}</span>,
      mobile: "title",
    },
    { key: "project", header: "Project", render: (row) => row.project_code, mobile: "field" },
    {
      key: "stage",
      header: "Stage",
      render: (row) =>
        isStatusWord(row.stage) ? <StatusBadge status={row.stage} /> : <StatusBadge tone="neutral" label={STAGE_LABEL[row.stage]} />,
      mobile: "status",
    },
    {
      key: "due",
      header: "Due",
      render: (row) => formatDate(row.due_date),
      mobile: "field",
      cellClassName: "whitespace-nowrap",
    },
  ];

  const milestoneColumns: Column<MilestoneOut>[] = [
    {
      key: "title",
      header: "Milestone",
      render: (row) => <span className="font-semibold text-ink">{row.title}</span>,
      mobile: "title",
    },
    { key: "project", header: "Project", render: (row) => row.project_name, mobile: "field" },
    {
      key: "status",
      header: "Status",
      render: (row) =>
        isStatusWord(row.status) ? (
          <StatusBadge status={row.status} />
        ) : (
          <StatusBadge tone="danger" label={MILESTONE_STATUS_LABEL[row.status]} />
        ),
      mobile: "status",
    },
    {
      key: "due",
      header: "Due",
      render: (row) => formatDate(row.due_date),
      mobile: "field",
      cellClassName: "whitespace-nowrap",
    },
  ];

  return (
    <div className="rise flex flex-col gap-5">
      <OverviewHeader onNewProject={goToNewProject} />

      {/* Row 1 — KPI cards. No delta on any of the four: none has a real comparison period. */}
      <section aria-label="Key metrics" className="grid grid-cols-2 gap-4 md:grid-cols-4">
        <KpiSlot status={projectsState.status} label="Active projects" error={projectsState.error} onRetry={projectsState.reload}>
          <MetricCard label="Active projects" value={COUNT.format(active)} icon={<BriefcaseIcon className="size-5" />} tone="accent" />
        </KpiSlot>

        <KpiSlot status={completedState.status} label="Completed" error={completedState.error} onRetry={completedState.reload}>
          <MetricCard label="Completed" value={COUNT.format(completed)} icon={<CheckCircleIcon className="size-5" />} tone="positive" />
        </KpiSlot>

        <KpiSlot status={tasksState.status} label="Overdue tasks" error={tasksState.error} onRetry={tasksState.reload}>
          <MetricCard label="Overdue tasks" value={COUNT.format(overdue)} icon={<AlertTriangleIcon className="size-5" />} tone="danger" />
        </KpiSlot>

        <KpiSlot status={milestonesState.status} label="Milestones due (14d)" error={milestonesState.error} onRetry={milestonesState.reload}>
          <MetricCard label="Milestones due (14d)" value={COUNT.format(dueSoon)} icon={<FlagIcon className="size-5" />} tone="info" />
        </KpiSlot>
      </section>

      {/* Row 2 — project progress · project health */}
      <section aria-label="Delivery overview" className="grid grid-cols-1 gap-5 xl:grid-cols-3">
        <Card className="flex flex-col gap-4 p-4 sm:p-6 xl:col-span-2">
          <SectionHeader
            title="Project progress"
            subtitle="Active projects, soonest target date first"
            action={
              <Link to="/portfolio" className="text-sm font-semibold text-accent-ink transition-colors hover:text-accent">
                View all
              </Link>
            }
          />
          <DataTable
            columns={projectColumns}
            rows={topProjects}
            rowKey={(row) => row.id}
            loading={projectsState.status === "loading"}
            errorState={
              projectsState.status === "error" ? (
                <ErrorState message={problemMessage(projectsState.error)} onRetry={projectsState.reload} />
              ) : undefined
            }
            emptyState={
              <EmptyState
                title="No projects in delivery"
                description="Create a project to track its milestones and tasks here."
                action={
                  canWrite ? (
                    <Button size="sm" onClick={goToNewProject}>
                      New project
                    </Button>
                  ) : undefined
                }
              />
            }
            mobileCard="auto"
          />
        </Card>

        <div className="xl:col-span-1">
          <ChartCard
            title="Project health"
            description="Across the portfolio"
            loading={projectsState.status === "loading"}
            error={
              projectsState.status === "error" ? (
                <ErrorState message={problemMessage(projectsState.error)} onRetry={projectsState.reload} />
              ) : undefined
            }
            empty={
              projectsState.status === "success" && projects.length === 0 ? (
                <EmptyState title="No projects yet" description="Health appears here once a project exists." />
              ) : undefined
            }
            skeleton="donut"
          >
            {projectsState.status === "success" && projects.length > 0 ? (
              <DonutChart label="Project health" slices={healthSlices} />
            ) : null}
          </ChartCard>
        </div>
      </section>

      {/* Row 3 — my tasks · deadlines */}
      <section aria-label="My tasks and deadlines" className="grid grid-cols-1 gap-5 lg:grid-cols-2">
        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <SectionHeader
            title="My tasks"
            action={
              <Link to="/board" className="text-sm font-semibold text-accent-ink transition-colors hover:text-accent">
                View all
              </Link>
            }
          />
          <DataTable
            columns={taskColumns}
            rows={myUpcoming}
            rowKey={(row) => row.id}
            loading={myTasksState.status === "loading"}
            errorState={
              myTasksState.status === "error" ? (
                <ErrorState message={problemMessage(myTasksState.error)} onRetry={myTasksState.reload} />
              ) : undefined
            }
            emptyState={
              <EmptyState title="Nothing assigned to you" description="Cards assigned to you on the board appear here." />
            }
            mobileCard="auto"
          />
        </Card>

        <Card className="flex flex-col gap-4 p-4 sm:p-6">
          <SectionHeader
            title="Deadlines"
            action={
              <Link to="/milestones" className="text-sm font-semibold text-accent-ink transition-colors hover:text-accent">
                View all
              </Link>
            }
          />
          <DataTable
            columns={milestoneColumns}
            rows={deadlines}
            rowKey={(row) => row.id}
            loading={milestonesState.status === "loading"}
            errorState={
              milestonesState.status === "error" ? (
                <ErrorState message={problemMessage(milestonesState.error)} onRetry={milestonesState.reload} />
              ) : undefined
            }
            emptyState={
              <EmptyState title="No upcoming deadlines" description="Milestone due dates appear here as you set them." />
            }
            mobileCard="auto"
          />
        </Card>
      </section>
    </div>
  );
}
