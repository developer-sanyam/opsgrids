import { useCallback, useMemo } from "react";

import { getStandups, TODAY } from "@/api/client";
import { formatDate } from "@/lib/format";
import { useAsync } from "@/lib/useAsync";
import { Card, EmptyState, ErrorState, PageHeader, Skeleton, StandupList } from "@/ui";
import { AlertTriangleIcon, CheckCircleIcon, MessageSquareIcon } from "@/ui/icons";

export function StandupsPage() {
  const standups = useAsync(useCallback(() => getStandups(TODAY), []), []);

  const stats = useMemo(() => {
    if (standups.status !== "success") return null;
    const blocked = standups.data.filter((s) => s.blockers !== null).length;
    return { total: standups.data.length, blocked, clear: standups.data.length - blocked };
  }, [standups]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Daily sync"
        title="Standups"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <MessageSquareIcon className="size-4 text-muted" />
            <span className="tnum">{formatDate(TODAY)}</span>
          </span>
        }
      />

      {stats ? (
        <section aria-label="Standup summary" className="grid grid-cols-3 gap-4">
          <Card className="p-4 flex items-center justify-between">
            <div className="flex flex-col">
              <span className="text-2xl font-extrabold tnum text-ink">{stats.total}</span>
              <span className="text-xs font-medium text-muted">Checked in</span>
            </div>
            <MessageSquareIcon className="size-5 text-faint" />
          </Card>
          <Card className="p-4 flex items-center justify-between">
            <div className="flex flex-col">
              <span className="text-2xl font-extrabold tnum text-present">{stats.clear}</span>
              <span className="text-xs font-medium text-muted">Clear</span>
            </div>
            <CheckCircleIcon className="size-5 text-present" />
          </Card>
          <Card className="p-4 flex items-center justify-between">
            <div className="flex flex-col">
              <span className="text-2xl font-extrabold tnum text-late">{stats.blocked}</span>
              <span className="text-xs font-medium text-muted">Blocked</span>
            </div>
            <AlertTriangleIcon className="size-5 text-late" />
          </Card>
        </section>
      ) : null}

      {standups.status === "loading" ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {Array.from({ length: 4 }).map((_, i) => (
            <Card key={i} className="p-5 flex flex-col gap-4">
              <div className="flex items-center gap-3">
                <Skeleton className="size-10" />
                <div className="flex flex-1 flex-col gap-2">
                  <Skeleton className="h-4 w-32" />
                  <Skeleton className="h-3 w-24" />
                </div>
              </div>
              <Skeleton className="h-16 w-full rounded-tile" />
            </Card>
          ))}
        </div>
      ) : null}
      {standups.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={standups.error.message} />
        </Card>
      ) : null}
      {standups.status === "success" ? (
        standups.data.length > 0 ? (
          <StandupList entries={standups.data} />
        ) : (
          <Card className="p-6">
            <EmptyState
              icon={<MessageSquareIcon className="size-6" />}
              title="No standups yet today"
              description="Nobody has checked in for this date. Entries will appear here as the team posts them."
            />
          </Card>
        )
      ) : null}
    </div>
  );
}
