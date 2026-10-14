import { useRef, useState } from "react";

import { recordTimesheetWeek } from "@/api/client";
import type { Person, Project, TimesheetEntryIn, TimesheetRow } from "@/api/types";
import { formatDayMonth } from "@/lib/format";
import { withOptional } from "@/lib/optional";
import { ApiError, problemMessage, useMutation } from "@opsgrids/client";
import { FormDialog, Select, TextField, useFormDirty } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

interface DayDraft {
  date: string;
  projectRef: string;
  entered: string;
  billable: string;
}

function draftsFrom(row: TimesheetRow | null, weekDates: readonly string[]): DayDraft[] {
  return weekDates.map((date) => {
    const entry = row?.entries.find((e) => e.date === date);
    return {
      date,
      projectRef: entry?.project_ref ?? "",
      entered: String(entry?.entered_hours ?? 0),
      billable: String(entry?.billable_hours ?? 0),
    };
  });
}

function weekdayLabel(iso: string): string {
  return new Date(`${iso}T00:00:00Z`).toLocaleDateString("en-GB", { weekday: "long", timeZone: "UTC" });
}

/**
 * Record or edit one person's week. The server replaces every entry on record — see
 * `services/timesheet/app/services.py` — so this resends all seven days, not just the one that
 * changed, prefilled from what's already on the sheet.
 */
export function TimesheetEntryDialog({
  row,
  person,
  projects,
  weekDates,
  onClose,
  onSaved,
}: {
  /** `null` starts a fresh week for a roster member who has no row yet. */
  row: TimesheetRow | null;
  person: Person;
  projects: Project[];
  weekDates: readonly string[];
  onClose: () => void;
  onSaved: (row: TimesheetRow, message: string) => void;
}) {
  const [days, setDays] = useState<DayDraft[]>(() => draftsFrom(row, weekDates));
  const [initialDays] = useState(days);
  const dirty = useFormDirty(initialDays, days);

  const [dayErrors, setDayErrors] = useState<Record<string, string>>({});
  const firstFieldRef = useRef<HTMLInputElement>(null);

  function setDay(date: string, patch: Partial<DayDraft>) {
    setDays((prev) => prev.map((d) => (d.date === date ? { ...d, ...patch } : d)));
  }

  const save = useMutation(
    (entries: TimesheetEntryIn[]) =>
      recordTimesheetWeek({
        person_ref: person.id,
        person_id: person.uuid,
        week_start: weekDates[0] ?? row?.week_start ?? "",
        status: "draft",
        capacity_hours: person.capacity_hours,
        entries,
      }),
    {
      onSuccess: (saved) => {
        onClose();
        onSaved(saved, `${person.name}'s week saved.`);
      },
    },
  );

  function submit() {
    const errors: Record<string, string> = {};
    const entries: TimesheetEntryIn[] = [];
    for (const day of days) {
      const entered = Number(day.entered);
      const billable = Number(day.billable);
      if (!Number.isFinite(entered) || entered < 0 || entered > 24) {
        errors[`${day.date}-entered`] = "0–24 hours.";
      }
      if (!Number.isFinite(billable) || billable < 0 || billable > 24) {
        errors[`${day.date}-billable`] = "0–24 hours.";
      } else if (billable > entered) {
        errors[`${day.date}-billable`] = "Can't exceed entered hours.";
      }
      entries.push({
        date: day.date,
        project_ref: day.projectRef.trim() === "" ? null : day.projectRef,
        entered_hours: entered,
        billable_hours: billable,
      });
    }
    setDayErrors(errors);
    if (Object.keys(errors).length > 0) {
      firstFieldRef.current?.focus();
      return;
    }
    void save.mutate(entries);
  }

  const projectOptions: SelectOption[] = [
    { value: "", label: "No engagement" },
    ...projects.map((p) => ({ value: p.code, label: p.code })),
  ];

  const nonFieldError =
    save.error && Object.keys(save.fieldErrors).length === 0 ? problemMessage(save.error) : undefined;

  return (
    <FormDialog
      open
      onClose={onClose}
      onSubmit={submit}
      title={`${person.name}'s week`}
      description={`Week of ${formatDayMonth(weekDates[0] ?? "")}. Every day resends — leave an untouched day as it is.`}
      submitLabel="Save week"
      pendingLabel="Saving…"
      pending={save.pending}
      initialFocusRef={firstFieldRef}
      size="lg"
      dirty={dirty}
      {...withOptional("error", nonFieldError)}
      {...withOptional("reference", (save.error as ApiError | null)?.problem?.request_id)}
    >
      <div className="flex flex-col gap-4">
        {days.map((day, index) => (
          <div key={day.date} className="grid grid-cols-1 gap-3 rounded-tile border border-line p-3 sm:grid-cols-4">
            <div className="flex flex-col justify-center sm:col-span-1">
              <span className="text-sm font-semibold text-ink">{weekdayLabel(day.date)}</span>
              <span className="text-xs text-faint tnum">{formatDayMonth(day.date)}</span>
            </div>
            <Select
              label="Engagement"
              value={day.projectRef}
              onChange={(e) => setDay(day.date, { projectRef: e.target.value })}
              options={projectOptions}
            />
            <TextField
              {...(index === 0 ? { ref: firstFieldRef } : {})}
              label="Entered (h)"
              inputMode="decimal"
              value={day.entered}
              onChange={(e) => setDay(day.date, { entered: e.target.value })}
              {...withOptional("error", dayErrors[`${day.date}-entered`])}
            />
            <TextField
              label="Billable (h)"
              inputMode="decimal"
              value={day.billable}
              onChange={(e) => setDay(day.date, { billable: e.target.value })}
              {...withOptional("error", dayErrors[`${day.date}-billable`])}
            />
          </div>
        ))}
      </div>
    </FormDialog>
  );
}
