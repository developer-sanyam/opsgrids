import { useCallback, useMemo, useState } from "react";

import { getAllMilestones, getAllProjects } from "@/api/client";
import type { ProjectHealth } from "@/api/types";
import { HEALTH_LABEL } from "@/data/labels";
import { today } from "@/lib/format";
import { problemMessage, useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  EmptyState,
  ErrorState,
  GanttTimeline,
  PageHeader,
  Pill,
  Skeleton,
} from "@/ui";
import { GanttIcon } from "@opsgrids/ui";

type HealthFilter = ProjectHealth | "all";

const FILTERS: { value: HealthFilter; label: string }[] = [
  { value: "all", label: "All health" },
  { value: "on_track", label: HEALTH_LABEL.on_track },
  { value: "at_risk", label: HEALTH_LABEL.at_risk },
  { value: "delayed", label: HEALTH_LABEL.delayed },
];

export function TimelinePage() {
  const now = today();
  const projectsState = useAsync(useCallback(getAllProjects, []), []);
  const milestonesState = useAsync(useCallback(() => getAllMilestones(), []), []);
  const [filter, setFilter] = useState<HealthFilter>("all");

  const projects = useMemo(
    () => (projectsState.status === "success" ? projectsState.data : []),
    [projectsState],
  );
  const milestones = useMemo(
    () => (milestonesState.status === "success" ? milestonesState.data : []),
    [milestonesState],
  );

  const shown = useMemo(
    () => (filter === "all" ? projects : projects.filter((p) => p.health === filter)),
    [projects, filter],
  );

  const ready = projectsState.status === "success" && milestonesState.status === "success";
  const failed = projectsState.status === "error" || milestonesState.status === "error";
  const errorMessage =
    (projectsState.status === "error" && problemMessage(projectsState.error)) ||
    (milestonesState.status === "error" && problemMessage(milestonesState.error)) ||
    "Couldn't load the timeline.";
  const reload = () => {
    projectsState.reload();
    milestonesState.reload();
  };

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
        <div role="group" aria-label="Filter by health" className="flex items-center gap-1.5 overflow-x-auto -mx-1 -my-1 px-1 py-1">
          {FILTERS.map((f) => (
            <Pill key={f.value} active={filter === f.value} onClick={() => setFilter(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>

        {!ready && !failed ? <Skeleton className="h-96 w-full rounded-tile" /> : null}
        {failed ? (
          <ErrorState
            title="Couldn't load the timeline"
            message={errorMessage}
            action={<Button onClick={reload}>Try again</Button>}
          />
        ) : null}
        {ready ? (
          shown.length === 0 ? (
            <EmptyState
              icon={<GanttIcon className="size-6" />}
              title="No projects in this view"
              description="No projects match that health filter. Switch back to all health to see the full plan."
              action={
                filter !== "all" ? (
                  <Button variant="outline" onClick={() => setFilter("all")}>
                    Clear filter
                  </Button>
                ) : null
              }
            />
          ) : (
            <GanttTimeline projects={shown} milestones={milestones} now={now} />
          )
        ) : null}
      </Card>
    </div>
  );
}
