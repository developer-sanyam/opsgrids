import { useCallback, useMemo, useState } from "react";

import { TODAY, getPeople, listStandups } from "@/api/client";
import { addDays } from "@/api/dates";
import type { Person, StandupEntry } from "@/api/types";
import { formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import {
  AlertTriangleIcon,
  ArrowLeftIcon,
  ArrowRightIcon,
  Button,
  Card,
  CheckCircleIcon,
  EmptyState,
  ErrorState,
  MessageSquareIcon,
  PageHeader,
  Pill,
  Skeleton,
  TextField,
  useToast,
} from "@opsgrids/ui";
import { StandupList } from "@/ui";
import { StandupFormDialog } from "./StandupFormDialog";

interface StandupsData {
  entries: StandupEntry[];
  people: Person[];
}

export function StandupsPage() {
  const toast = useToast();
  const [date, setDate] = useState(TODAY);
  const [blockedOnly, setBlockedOnly] = useState(false);
  const [posting, setPosting] = useState(false);
  const [editing, setEditing] = useState<StandupEntry | null>(null);

  const loadStandups = useCallback(async (): Promise<StandupsData> => {
    const [page, people] = await Promise.all([listStandups({ date, blocked: blockedOnly || undefined }), getPeople()]);
    return { entries: page.items, people };
  }, [date, blockedOnly]);

  const standups = useAsync(loadStandups, [date, blockedOnly]);

  const stats = useMemo(() => {
    if (standups.status !== "success") return null;
    const blocked = standups.data.entries.filter((s) => s.blockers !== null).length;
    return { total: standups.data.entries.length, blocked, clear: standups.data.entries.length - blocked };
  }, [standups]);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Daily sync"
        title="Standups"
        actions={
          <div className="flex flex-wrap items-end gap-2">
            <Button variant="ghost" aria-label="Previous day" onClick={() => setDate((d) => addDays(d, -1))}>
              <ArrowLeftIcon className="size-4" />
            </Button>
            <TextField label="Date" type="date" value={date} onChange={(e) => setDate(e.target.value)} />
            <Button variant="ghost" aria-label="Next day" onClick={() => setDate((d) => addDays(d, 1))}>
              <ArrowRightIcon className="size-4" />
            </Button>
            <Can permission="pulseboard.standup.entry.write">
              <Button onClick={() => setPosting(true)}>Post check-in</Button>
            </Can>
          </div>
        }
      />

      <div role="group" aria-label="Filter check-ins" className="-my-1 flex items-center gap-1.5 py-1">
        <Pill active={!blockedOnly} onClick={() => setBlockedOnly(false)}>
          All check-ins
        </Pill>
        <Pill active={blockedOnly} onClick={() => setBlockedOnly(true)}>
          Blocked only
        </Pill>
      </div>

      {stats ? (
        <section aria-label="Standup summary" className="grid grid-cols-1 gap-4 sm:grid-cols-3">
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
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2" aria-busy="true">
          <span role="status" className="sr-only">
            Loading standups
          </span>
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
          <ErrorState
            title="Couldn't load standups"
            message={problemMessage(standups.error)}
            action={<Button onClick={standups.reload}>Try again</Button>}
          />
        </Card>
      ) : null}
      {standups.status === "success" ? (
        standups.data.entries.length > 0 ? (
          <StandupList entries={standups.data.entries} people={standups.data.people} onEdit={setEditing} />
        ) : (
          <Card className="p-6">
            <EmptyState
              icon={<MessageSquareIcon className="size-6" />}
              title={blockedOnly ? "Nobody's blocked" : `No standups yet for ${formatDate(date)}`}
              description={
                blockedOnly
                  ? "No check-in on this date reports a blocker."
                  : "Nobody has checked in for this date. Entries will appear here as the team posts them."
              }
              action={
                !blockedOnly ? (
                  <Can permission="pulseboard.standup.entry.write">
                    <Button onClick={() => setPosting(true)}>Post check-in</Button>
                  </Can>
                ) : null
              }
            />
          </Card>
        )
      ) : null}

      {posting && standups.status === "success" ? (
        <StandupFormDialog
          entry={null}
          people={standups.data.people}
          date={date}
          onClose={() => setPosting(false)}
          onSaved={(_entry, message) => {
            toast.success(message);
            standups.reload();
          }}
        />
      ) : null}
      {editing && standups.status === "success" ? (
        <StandupFormDialog
          entry={editing}
          people={standups.data.people}
          date={date}
          onClose={() => setEditing(null)}
          onSaved={(_entry, message) => {
            setEditing(null);
            toast.success(message);
            standups.reload();
          }}
        />
      ) : null}
    </div>
  );
}
