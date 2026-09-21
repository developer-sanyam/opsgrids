import { useCallback, useEffect, useState } from "react";

import { listActivities } from "@/api/client";
import type { Activity, ActivityType } from "@/api/types";
import { useDirectory } from "@/data/directoryContext";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  ActivityTimeline,
  Button,
  Card,
  EmptyState,
  ErrorState,
  LoadMore,
  PageHeader,
  Pill,
  Skeleton,
} from "@/ui";
import { ActivityIcon, PlusIcon, useToast } from "@opsgrids/ui";
import { ActivityFormDialog } from "@/forms/ActivityFormDialog";

type TypeFilter = ActivityType | "all";

const TYPE_FILTERS: { value: TypeFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "call", label: "Calls" },
  { value: "email", label: "Emails" },
  { value: "meeting", label: "Meetings" },
  { value: "note", label: "Notes" },
  { value: "task", label: "Tasks" },
];

const PAGE_SIZE = 25;

export function ActivitiesPage() {
  const toast = useToast();
  const directory = useDirectory();
  const { companies, contacts } = directory;
  const [type, setType] = useState<TypeFilter>("all");
  const [loaded, setLoaded] = useState<Activity[]>([]);
  const [logging, setLogging] = useState(false);

  const loader = useCallback(() => listActivities({ type, page: 1, size: PAGE_SIZE }), [type]);
  const activities = useAsync(loader, [type]);

  // `activities` always holds page 1 for the current filter; `loaded` is that page plus whatever
  // `LoadMore` appended. Reset it every time page 1 changes under it (a new filter, a reload).
  useEffect(() => {
    if (activities.status === "success") setLoaded(activities.data.items);
  }, [activities.status, activities.data]);

  const loadMore = useMutation(
    (nextPage: number) => listActivities({ type, page: nextPage, size: PAGE_SIZE }),
    { onSuccess: (page) => setLoaded((current) => [...current, ...page.items]) },
  );

  const rows = activities.status === "success" ? loaded : [];
  const total = activities.status === "success" ? activities.data.total : 0;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Activities"
        actions={
          <>
            <span className="inline-flex h-11 items-center gap-2 rounded-full bg-surface px-4 text-sm font-semibold shadow-card">
              <ActivityIcon className="size-4 text-muted" />
              <span className="tnum">{total}</span>
              <span className="font-medium text-muted">logged</span>
            </span>
            <Can permission="crm.pipeline.activity.write" scope="OWN">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setLogging(true)}>
                Log activity
              </Button>
            </Can>
          </>
        }
      />

      <Card className="p-4 sm:p-5">
        <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
          {TYPE_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={type === f.value}
              onClick={() => {
                setType(f.value);
                setLoaded([]);
              }}
            >
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
        {activities.status === "error" ? (
          <ErrorState
            title="Couldn't load activities"
            message={problemMessage(activities.error)}
            onRetry={activities.reload}
          />
        ) : null}
        {activities.status === "success" ? (
          rows.length > 0 ? (
            <div className="flex flex-col gap-5">
              <ActivityTimeline activities={rows} />
              <LoadMore
                loaded={rows.length}
                total={total}
                loading={loadMore.pending}
                noun="activities"
                onLoadMore={() => {
                  const nextPage = Math.floor(rows.length / PAGE_SIZE) + 1;
                  void loadMore.mutate(nextPage);
                }}
              />
            </div>
          ) : (
            <EmptyState
              icon={<ActivityIcon className="size-6" />}
              title="No activities of this type"
              description="Nothing has been logged for this filter yet. Try another type or clear it."
            />
          )
        ) : null}
      </Card>

      {logging ? (
        <ActivityFormDialog
          companies={companies}
          contacts={contacts}
          onClose={() => setLogging(false)}
          onSaved={() => {
            setLogging(false);
            toast.success("Activity logged.");
            setLoaded([]);
            activities.reload();
          }}
        />
      ) : null}
    </div>
  );
}
