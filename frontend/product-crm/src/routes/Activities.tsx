import { useCallback, useState } from "react";

import { listActivities } from "@/api/client";
import type { ActivityType } from "@/api/types";
import { useAsync } from "@atmora/client";
import {
  ActivityTimeline,
  Card,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  Skeleton,
} from "@/ui";
import { ActivityIcon } from "@atmora/ui";

type TypeFilter = ActivityType | "all";

const TYPE_FILTERS: { value: TypeFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "call", label: "Calls" },
  { value: "email", label: "Emails" },
  { value: "meeting", label: "Meetings" },
  { value: "note", label: "Notes" },
  { value: "task", label: "Tasks" },
];

export function ActivitiesPage() {
  const [type, setType] = useState<TypeFilter>("all");

  const loader = useCallback(() => listActivities({ type }), [type]);
  const activities = useAsync(loader, [type]);

  const count = activities.status === "success" ? activities.data.length : 0;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Activities"
        actions={
          <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
            <ActivityIcon className="size-4 text-muted" />
            <span className="tnum">{count}</span>
            <span className="font-medium text-muted">logged</span>
          </span>
        }
      />

      <Card className="p-4 sm:p-5">
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
          {TYPE_FILTERS.map((f) => (
            <Pill key={f.value} active={type === f.value} onClick={() => setType(f.value)}>
              {f.label}
            </Pill>
          ))}
        </div>
      </Card>

      <Card className="p-6">
        {activities.status === "loading" ? (
          <div className="flex flex-col gap-5">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="flex gap-3.5">
                <Skeleton className="size-10 rounded-full" />
                <div className="flex flex-1 flex-col gap-2">
                  <Skeleton className="h-4 w-48" />
                  <Skeleton className="h-3 w-72" />
                </div>
              </div>
            ))}
          </div>
        ) : null}
        {activities.status === "error" ? <ErrorState message={activities.error.message} /> : null}
        {activities.status === "success" ? (
          activities.data.length > 0 ? (
            <ActivityTimeline activities={activities.data} />
          ) : (
            <EmptyState
              icon={<ActivityIcon className="size-6" />}
              title="No activities of this type"
              description="Nothing has been logged for this filter yet. Try another type or clear it."
            />
          )
        ) : null}
      </Card>
    </div>
  );
}
