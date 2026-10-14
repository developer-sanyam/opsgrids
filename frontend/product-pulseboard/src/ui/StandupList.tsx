import { useMemo } from "react";

import type { Person, StandupEntry } from "@/api/types";
import { Can } from "@opsgrids/client";
import { AlertTriangleIcon, Avatar, CheckCircleIcon, RowActions, cn } from "@opsgrids/ui";

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="text-[0.65rem] font-bold uppercase tracking-wider text-faint">{label}</span>
      <p className="text-sm text-ink-soft leading-snug">{children}</p>
    </div>
  );
}

/**
 * One person's standup: yesterday / today / blockers. Blockers are the signal — they get a
 * dedicated, coloured row (amber when present, a quiet green "all clear" when not) so a blocked
 * person is never buried in prose.
 */
function StandupCard({
  entry,
  who,
  onEdit,
}: {
  entry: StandupEntry;
  who: Person | undefined;
  onEdit: () => void;
}) {
  const blocked = entry.blockers !== null;
  return (
    <article className="flex flex-col gap-4 rounded-card bg-surface p-5 shadow-card">
      <header className="flex items-center gap-3">
        <Avatar name={who?.name ?? "Unknown"} size="md" />
        <div className="flex min-w-0 flex-col leading-tight">
          <span className="truncate font-bold text-ink">{who?.name ?? entry.person_id}</span>
          <span className="truncate text-xs text-muted">{who?.role}</span>
        </div>
        {blocked ? (
          <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-late-soft px-2.5 py-1 text-xs font-semibold text-late">
            <AlertTriangleIcon className="size-3.5" />
            Blocked
          </span>
        ) : (
          <span className="ml-auto inline-flex items-center gap-1.5 rounded-full bg-present-soft px-2.5 py-1 text-xs font-semibold text-present">
            <CheckCircleIcon className="size-3.5" />
            Clear
          </span>
        )}
        <Can permission="pulseboard.standup.entry.write">
          <RowActions label={who?.name ?? "check-in"} actions={[{ label: "Edit", onSelect: onEdit }]} />
        </Can>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Field label="Yesterday">{entry.yesterday}</Field>
        <Field label="Today">{entry.today}</Field>
      </div>

      <div className={cn("flex items-start gap-2.5 rounded-tile px-3 py-2.5", blocked ? "bg-late-soft/60" : "bg-ground")}>
        <span className={cn("mt-0.5 shrink-0", blocked ? "text-late" : "text-present")}>
          {blocked ? <AlertTriangleIcon className="size-4" /> : <CheckCircleIcon className="size-4" />}
        </span>
        <div className="flex flex-col gap-0.5">
          <span className="text-[0.65rem] font-bold uppercase tracking-wider text-faint">Blockers</span>
          <p className={cn("text-sm leading-snug", blocked ? "text-ink-soft" : "text-muted")}>
            {entry.blockers ?? "No blockers — clear to keep moving."}
          </p>
        </div>
      </div>
    </article>
  );
}

export function StandupList({
  entries,
  people,
  onEdit,
}: {
  entries: StandupEntry[];
  people: Person[];
  onEdit: (entry: StandupEntry) => void;
}) {
  const personById = useMemo(() => new Map(people.map((p) => [p.id, p])), [people]);
  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
      {entries.map((entry) => (
        <StandupCard key={entry.id} entry={entry} who={personById.get(entry.person_id)} onEdit={() => onEdit(entry)} />
      ))}
    </div>
  );
}
