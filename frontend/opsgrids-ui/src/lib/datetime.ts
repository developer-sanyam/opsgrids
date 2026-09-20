/**
 * Instants, rendered in the zone that owns them.
 *
 * ADR-0000 settles this: timestamps are `TIMESTAMPTZ` in UTC and are converted at the edge using
 * **the relevant location's** timezone — never the browser's. A formatter that reaches for the
 * host zone (or hard-codes `UTC`, which is a different wrong answer) puts a due time in front of
 * an approver that is hours from the one the server meant.
 *
 * So nothing here reads the browser's zone. Every function takes the zone it must render in,
 * either as a named IANA zone (`formatInZone`) or as a location code resolved against the
 * organization's cached location list (`formatInstant`). When that resolution falls back to
 * organization time the result says so **in the text**, because a silently wrong zone is worse
 * than an obviously approximate one.
 */

/** Rendered when there is no instant to render — the platform's em-dash placeholder. */
const ABSENT = "—";

export type DateTimeStyle =
  /** `15 Sep, 6:40 pm IST` — the workhorse. Gains a year when the instant is not this year. */
  | "datetime"
  /** `Tue 15 Sep, 6:40 pm IST` — where the weekday is part of the decision (a working day). */
  | "datetime-weekday"
  /** `12 Sep 2026` — a calendar day with no time of day. Always carries its year. */
  | "date"
  /** `6:40 pm IST` — a time of day whose date is already established by its surroundings. */
  | "time";

/** The organization's zone facts: the cached `GET /locations` list, plus the org-wide fallback. */
export interface ZoneLookup {
  /** IANA zone per location `code`, from `LocationOut.timezone`. Fetched once per session. */
  locations: Readonly<Record<string, string>>;
  /** `GET /organization`.`timezone` — used, and visibly labelled, when a code resolves to nothing. */
  organizationZone: string;
}

/** Which zone an instant will render in, and whether that answer is the labelled fallback. */
export interface ZoneResolution {
  zone: string;
  /** True when the location code was null, or matched no location, and organization time was used. */
  fallback: boolean;
}

/** A formatted instant, with everything a screen needs to render it honestly. */
export interface FormattedInstant {
  /** What the user reads. Carries `· organization time` when the zone came from the fallback. */
  text: string;
  /** The same value without the fallback label, for a caller that renders the label itself. */
  value: string;
  /** The `title` attribute: the full zone name, named as organization time on a fallback. */
  title: string;
  /** The UTC instant, unchanged — the value for a `<time datetime="…">` attribute. */
  dateTime: string;
  zone: string;
  fallback: boolean;
}

function toDate(instant: string | Date): Date | null {
  const date = instant instanceof Date ? instant : new Date(instant);
  return Number.isNaN(date.getTime()) ? null : date;
}

/**
 * The display format is fixed by design, so the month names are ours rather than the locale's.
 * CLDR abbreviates September as "Sept" in several English locales and "Sep" in others, and a
 * column of dates that is three characters wide in one browser and four in another is exactly
 * the kind of drift a design system exists to remove.
 */
const MONTHS = [
  "Jan",
  "Feb",
  "Mar",
  "Apr",
  "May",
  "Jun",
  "Jul",
  "Aug",
  "Sep",
  "Oct",
  "Nov",
  "Dec",
];

function partsIn(
  date: Date,
  zone: string,
  options: Intl.DateTimeFormatOptions,
): Record<string, string> {
  const formatter = new Intl.DateTimeFormat("en-US", { timeZone: zone, ...options });
  const out: Record<string, string> = {};
  for (const part of formatter.formatToParts(date)) out[part.type] = part.value;
  return out;
}

/**
 * The zone's own abbreviation — `IST`, `EDT` — falling back to the `GMT+5:30` form only when no
 * locale knows a short name for it. `en-IN` is asked first because this platform's default zone
 * is `Asia/Kolkata`, whose abbreviation only exists in that locale; `en-US` answers for the
 * Americas. A bare "6:40 pm" is a bug, so this never returns an empty string.
 */
export function zoneAbbreviation(date: Date, zone: string): string {
  for (const locale of ["en-IN", "en-US"]) {
    const formatter = new Intl.DateTimeFormat(locale, {
      timeZone: zone,
      timeZoneName: "short",
    });
    const name = formatter
      .formatToParts(date)
      .find((part) => part.type === "timeZoneName")?.value;
    if (name && !name.startsWith("GMT") && !name.startsWith("UTC")) return name;
    if (name && locale === "en-US") return name;
  }
  return "UTC";
}

/** The calendar year an instant falls in, as read in the given zone — not in the host's. */
function yearIn(date: Date, zone: string): string {
  return partsIn(date, zone, { year: "numeric" }).year ?? "";
}

/**
 * Render an instant in a named IANA zone. The zone is required and is never defaulted to the
 * browser's: a caller who does not know the zone does not yet know what the time means.
 */
export function formatInZone(
  instant: string | Date,
  zone: string,
  style: DateTimeStyle = "datetime",
  now: Date = new Date(),
): string {
  const date = toDate(instant);
  if (!date) return ABSENT;

  const p = partsIn(date, zone, {
    weekday: "short",
    day: "numeric",
    month: "numeric",
    year: "numeric",
    hour: "numeric",
    minute: "2-digit",
    hour12: true,
  });

  const day = p.day ?? "";
  const month = MONTHS[Number(p.month) - 1] ?? "";
  const clock = `${p.hour ?? ""}:${p.minute ?? ""} ${(p.dayPeriod ?? "").toLowerCase()}`;
  const abbreviation = zoneAbbreviation(date, zone);

  if (style === "time") return `${clock} ${abbreviation}`;
  if (style === "date") return `${day} ${month} ${p.year ?? ""}`;

  // The year is noise for this year's requests and load-bearing for last year's, so it appears
  // only when the instant is outside the current calendar year *in the rendering zone*.
  const sameYear = p.year === yearIn(now, zone);
  const calendar = sameYear ? `${day} ${month}` : `${day} ${month} ${p.year ?? ""}`;
  const weekday = style === "datetime-weekday" ? `${p.weekday ?? ""} ` : "";
  return `${weekday}${calendar}, ${clock} ${abbreviation}`;
}

/**
 * Resolve a location code to the zone an instant renders in: the code's location, else the
 * organization's zone marked as a fallback. §2.1 of the approval screen spec makes the fallback
 * visible rather than silent, which is what `fallback` is for — it is not an error flag.
 */
export function resolveZone(
  locationCode: string | null | undefined,
  lookup: ZoneLookup,
): ZoneResolution {
  const zone = locationCode ? lookup.locations[locationCode] : undefined;
  if (zone) return { zone, fallback: false };
  return { zone: lookup.organizationZone, fallback: true };
}

/**
 * The zone-aware formatter a screen actually calls: a location code plus the cached lookup in,
 * a labelled, titled, machine-readable instant out.
 *
 * On a fallback the text carries `· organization time` — visible, never only a tooltip — and the
 * title names the zone in full. The approver acts on this value, so it says what it is.
 */
export function formatInstant(
  instant: string | Date,
  locationCode: string | null | undefined,
  lookup: ZoneLookup,
  style: DateTimeStyle = "datetime",
  now: Date = new Date(),
): FormattedInstant {
  const { zone, fallback } = resolveZone(locationCode, lookup);
  const date = toDate(instant);
  const value = formatInZone(instant, zone, style, now);
  return {
    value,
    text: fallback && style !== "date" ? `${value} · organization time` : value,
    title: fallback ? `Organization time (${zone})` : zone,
    dateTime: date ? date.toISOString() : "",
    zone,
    fallback,
  };
}

function zoneOffsetMs(date: Date, zone: string): number {
  const p = partsIn(date, zone, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hourCycle: "h23",
  });
  const asUTC = Date.UTC(
    Number(p.year),
    Number(p.month) - 1,
    Number(p.day),
    Number(p.hour),
    Number(p.minute),
    Number(p.second),
  );
  return asUTC - date.getTime();
}

/**
 * An instant as the wall clock a `datetime-local` input shows — `2026-09-20T09:00` — read in the
 * given zone. The inverse of `wallClockToInstant`.
 */
export function instantToWallClock(instant: string | Date, zone: string): string {
  const date = toDate(instant);
  if (!date) return "";
  const p = partsIn(date, zone, {
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  });
  return `${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}`;
}

/**
 * The wall clock a user typed, in the zone they were thinking in, as a UTC instant.
 *
 * This is the conversion a bare `<input type="datetime-local">` gets wrong: the browser hands
 * back "20 Sep, 9:00 am" with no zone, and `new Date(value)` then reads it in *the viewer's*
 * zone — so a delegation set up by an approver travelling abroad would start at the wrong hour.
 * The offset is resolved twice because the first guess can land on the far side of a DST change.
 */
export function wallClockToInstant(wallClock: string, zone: string): string {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(wallClock);
  if (!match) return "";
  const [, year, month, day, hour, minute] = match;
  const naive = Date.UTC(
    Number(year),
    Number(month) - 1,
    Number(day),
    Number(hour),
    Number(minute),
  );
  const firstPass = naive - zoneOffsetMs(new Date(naive), zone);
  const settled = naive - zoneOffsetMs(new Date(firstPass), zone);
  return new Date(settled).toISOString();
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

function plural(count: number, unit: string): string {
  return `${count} ${unit}${count === 1 ? "" : "s"}`;
}

/**
 * A bare duration — `6 hours`, `1 day` — for copy that supplies its own framing, such as
 * "Overdue by 6 hours". Always positive: a negative duration is never shown to a user.
 */
export function formatDuration(ms: number): string {
  const abs = Math.abs(ms);
  if (abs < MINUTE) return "less than a minute";
  if (abs < HOUR) return plural(Math.floor(abs / MINUTE), "minute");
  if (abs < DAY) return plural(Math.floor(abs / HOUR), "hour");
  return plural(Math.floor(abs / DAY), "day");
}

/**
 * `2 days ago`, `4 hours ago`, `just now`, `in 6 hours`. Zone-free by construction — the distance
 * between two instants is the same in every timezone — and always rendered beside an absolute
 * value, never instead of one.
 */
export function formatRelative(instant: string | Date, now: Date = new Date()): string {
  const date = toDate(instant);
  if (!date) return ABSENT;
  const diff = now.getTime() - date.getTime();
  if (Math.abs(diff) < MINUTE) return "just now";
  return diff > 0 ? `${formatDuration(diff)} ago` : `in ${formatDuration(diff)}`;
}
