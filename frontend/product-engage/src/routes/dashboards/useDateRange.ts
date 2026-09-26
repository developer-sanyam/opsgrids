import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

import type { DateRange } from "@/api/analytics";
import { addDays, parseIsoDate, toIsoDate } from "./dates";

/**
 * The date range every dashboard reads, held in the URL so a link shares it, a refresh keeps it and
 * the browser's Back button steps through it.
 *
 * Three presets count back from today (`?range=7d|30d|90d`, inclusive of today) and `custom` carries
 * its own ends (`?range=custom&from=…&to=…`). A preset is stored as the preset rather than as two
 * dates, so a bookmarked "last 30 days" is still the last 30 days tomorrow. A URL that names a
 * custom range with a malformed or inverted end falls back to the default rather than asking the
 * service a question with no answer.
 */

export type RangePreset = "7d" | "30d" | "90d" | "custom";
export type FixedPreset = Exclude<RangePreset, "custom">;

export const PRESET_DAYS: Record<FixedPreset, number> = {
  "7d": 7,
  "30d": 30,
  "90d": 90,
};

export const DEFAULT_PRESET: FixedPreset = "30d";

export interface DateRangeControls {
  preset: RangePreset;
  range: DateRange;
  setPreset: (preset: FixedPreset) => void;
  /** Both ends inclusive. The caller has already checked `from <= to`. */
  setCustom: (from: string, to: string) => void;
}

/** Whether `from` and `to` are real dates in order. */
export function isValidRange(from: string, to: string): boolean {
  const start = parseIsoDate(from);
  const end = parseIsoDate(to);
  return start !== null && end !== null && start.getTime() <= end.getTime();
}

function presetRange(preset: FixedPreset, today: Date): DateRange {
  return { from: toIsoDate(addDays(today, 1 - PRESET_DAYS[preset])), to: toIsoDate(today) };
}

function isPreset(value: string | null): value is FixedPreset {
  return value === "7d" || value === "30d" || value === "90d";
}

export function useDateRange(): DateRangeControls {
  const [params, setParams] = useSearchParams();
  const requested = params.get("range");
  const from = params.get("from");
  const to = params.get("to");

  const { preset, range } = useMemo(() => {
    if (requested === "custom" && from !== null && to !== null && isValidRange(from, to)) {
      return { preset: "custom" as const, range: { from, to } };
    }
    const chosen = isPreset(requested) ? requested : DEFAULT_PRESET;
    return { preset: chosen, range: presetRange(chosen, new Date()) };
  }, [requested, from, to]);

  const setPreset = useCallback(
    (next: FixedPreset) => {
      setParams(
        (current) => {
          const updated = new URLSearchParams(current);
          updated.set("range", next);
          updated.delete("from");
          updated.delete("to");
          return updated;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  const setCustom = useCallback(
    (nextFrom: string, nextTo: string) => {
      setParams(
        (current) => {
          const updated = new URLSearchParams(current);
          updated.set("range", "custom");
          updated.set("from", nextFrom);
          updated.set("to", nextTo);
          return updated;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  return { preset, range, setPreset, setCustom };
}
