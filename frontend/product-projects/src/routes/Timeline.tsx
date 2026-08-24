import { useMemo, useState } from "react";

import { getAllProjects, listMilestones } from "@/api/client";
import type { MilestoneOut, ProjectHealth, ProjectOut } from "@/api/types";
import { HEALTH_LABEL } from "@/data/labels";
import { useAsync } from "@/lib/useAsync";
import {
  Card,
  EmptyState,
  ErrorState,
  GanttTimeline,
  PageHeader,
  Pill,
  Skeleton,
} from "@/ui";
import { GanttIcon } from "@/ui/icons";

const EMPTY_PROJECTS: ProjectOut[] = [];
const EMPTY_MILESTONES: MilestoneOut[] = [];

type HealthFilter = ProjectHealth | "all";

const FILTERS: { value: HealthFilter; label: string }[] = [
  { value: "all", label: "All health" },
  { value: "on_track", label: HEALTH_LABEL.on_track },
  { value: "at_risk", label: HEALTH_LABEL.at_risk },
  { value: "delayed", label: HEALTH_LABEL.delayed },
];

export function TimelinePage() {
  const projectsState = useAsync(getAllProjects, []);
  const milestonesState = useAsync(listMilestones, []);
  const [filter, setFilter] = useState<HealthFilter>("all");

  const projects = useMemo(
    () => (projectsState.status === "success" ? projectsState.data.items : EMPTY_PROJECTS),
    [projectsState],
  );
  const milestones = useMemo(
    () => (milestonesState.status === "success" ? milestonesState.data.items : EMPTY_MILESTONES),
    [milestonesState],
  );

  const shown = useMemo(
    () => (filter === "all" ? projects : projects.filter((p) => p.health === filter)),
    [projects, filter],
  );

  const ready = projectsState.status === "success" && milestonesState.status === "success";
  const failed = projectsState.status === "error" || milestonesState.status === "error";
  const errorMessage =
    (projectsState.status === "error" && projectsState.error.message) ||
    (milestonesState.status === "error" && milestonesState.error.message) ||
    "Couldn't load the timeline.";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Delivery"
        title="Timeline"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <GanttIcon className="size-4 text-muted" />
            <span className="tnum">{shown.length}</span>
            <span className="text-muted font-medium">projects</span>
          </span>
        }
      />

      <Card className="flex flex-col gap-5 p-4 sm:p-6">
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
          {FILTERS.map((f) => (
            <Pill key={f.value} active={filter === f.value} onClick={() => setFilter(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>

        {!ready && !failed ? <Skeleton className="h-96 w-full rounded-tile" /> : null}
        {failed ? <ErrorState message={errorMessage} /> : null}
        {ready ? (
          shown.length === 0 ? (
            <EmptyState
              icon={<GanttIcon className="size-6" />}
              title="No projects in this view"
              description="No projects match that health filter. Switch back to all health to see the full plan."
            />
          ) : (
            <GanttTimeline projects={shown} milestones={milestones} />
          )
        ) : null}
      </Card>
    </div>
  );
}
