import { useState } from "react";

import { Select, TextField } from "@opsgrids/ui";
import type { SelectOption } from "@opsgrids/ui";

import { formatDayLong } from "./dates";
import { isValidRange } from "./useDateRange";
import type { DateRangeControls, FixedPreset } from "./useDateRange";

/**
 * The range picker every dashboard shares: a preset list, and — on "Custom range" — two date
 * fields. It owns nothing but the half-typed dates; the range itself lives in the URL
 * (`useDateRange`), so this control and the page's fetch can never disagree about what is showing.
 *
 * A custom range is only committed once both ends are real dates in order. Until then the person's
 * typing stays in the fields with the reason under the one that is wrong, and the dashboard keeps
 * showing the last range that was valid — never a request the service could only refuse.
 */

const OPTIONS: SelectOption[] = [
  { value: "7d", label: "Last 7 days" },
  { value: "30d", label: "Last 30 days" },
  { value: "90d", label: "Last 90 days" },
  { value: "custom", label: "Custom range" },
];

interface Draft {
  from?: string;
  to?: string;
}

function problems(from: string, to: string): { from?: string; to?: string } {
  if (from === "") return { from: "Choose a start date." };
  if (to === "") return { to: "Choose an end date." };
  if (!isValidRange(from, to)) return { to: "The end date must be on or after the start date." };
  return {};
}

export function DateRangeControl({ controls }: { controls: DateRangeControls }) {
  const { preset, range, setPreset, setCustom } = controls;
  const [draft, setDraft] = useState<Draft>({});

  const from = draft.from ?? range.from;
  const to = draft.to ?? range.to;
  const errors = preset === "custom" ? problems(from, to) : {};

  function edit(next: Draft) {
    const merged = { from, to, ...next };
    setDraft({ ...draft, ...next });
    if (Object.keys(problems(merged.from, merged.to)).length === 0) {
      setCustom(merged.from, merged.to);
      setDraft({});
    }
  }

  function choose(value: string) {
    setDraft({});
    if (value === "custom") setCustom(range.from, range.to);
    else setPreset(value as FixedPreset);
  }

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-start gap-3">
        <Select
          label="Date range"
          hideLabel
          options={OPTIONS}
          value={preset}
          onChange={(event) => choose(event.target.value)}
          containerClassName="w-44"
        />
        {preset === "custom" ? (
          <fieldset className="m-0 flex flex-wrap items-start gap-3 border-0 p-0">
            <legend className="sr-only">Custom date range</legend>
            <TextField
              type="date"
              label="From"
              value={from}
              max={to === "" ? undefined : to}
              onChange={(event) => edit({ from: event.target.value })}
              {...(errors.from !== undefined ? { error: errors.from } : {})}
              containerClassName="w-44"
            />
            <TextField
              type="date"
              label="To"
              value={to}
              min={from === "" ? undefined : from}
              onChange={(event) => edit({ to: event.target.value })}
              {...(errors.to !== undefined ? { error: errors.to } : {})}
              containerClassName="w-44"
            />
          </fieldset>
        ) : null}
      </div>
      <p role="status" className="text-xs text-muted tnum">
        Showing {formatDayLong(range.from)} – {formatDayLong(range.to)}
      </p>
    </div>
  );
}
