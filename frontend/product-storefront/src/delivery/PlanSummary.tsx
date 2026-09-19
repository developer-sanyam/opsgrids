import type { ReactNode } from "react";

import { Tag } from "@opsgrids/ui";

import { rejectionMessage } from "./postalCodes";
import type { ImportPlan, SavePlan } from "./postalCodes";

/**
 * What a save would do, before it does it.
 *
 * **This is the screen the whole postal-code job hangs on.** A postal code belongs to exactly one
 * zone per organization (ADR-0010 §2), so pasting a list that overlaps another zone is a
 * re-assignment — a decision, not an accident — and the merchant makes it with the list in front
 * of them. Last-writer-wins with no preview is the silent wrong answer a customer finds, not the
 * merchant.
 *
 * Counts are honest in both directions: what arrives, what moves and from where, what was already
 * here, what is dropped, and what was refused with the rule that refused it.
 */

function Count({
  value,
  label,
  tone = "neutral",
}: {
  value: number;
  label: string;
  tone?: "neutral" | "positive" | "warning" | "negative";
}) {
  const ink =
    tone === "positive"
      ? "text-positive"
      : tone === "warning"
        ? "text-late"
        : tone === "negative"
          ? "text-negative"
          : "text-ink";
  return (
    <div className="flex flex-col gap-0.5">
      <span className={`tnum text-2xl font-extrabold ${ink}`}>{value}</span>
      <span className="text-xs font-semibold uppercase tracking-wide text-faint">{label}</span>
    </div>
  );
}

/**
 * A long list of codes, kept readable.
 *
 * Wraps rather than scrolls sideways, caps its own height and scrolls vertically past that, and
 * says how many are hidden — a 400-code move is a normal size for this and a list that simply
 * grew to 400 rows would push every control on the screen below the fold.
 */
function CodeList({ codes, limit = 60 }: { codes: readonly string[]; limit?: number }) {
  const shown = codes.slice(0, limit);
  const hidden = codes.length - shown.length;
  return (
    <div className="flex flex-col gap-1.5">
      <ul className="m-0 flex max-h-40 list-none flex-wrap gap-1.5 overflow-y-auto p-0">
        {shown.map((code) => (
          <li key={code}>
            <Tag tone="neutral" className="tnum">
              {code}
            </Tag>
          </li>
        ))}
      </ul>
      {hidden > 0 ? (
        <p className="text-xs text-muted">and {hidden} more</p>
      ) : null}
    </div>
  );
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h4 className="text-sm font-bold text-ink">{title}</h4>
      {children}
    </section>
  );
}

export function PlanSummary({ plan }: { plan: SavePlan }) {
  const movesByZone = new Map<string, { zoneName: string; codes: string[] }>();
  for (const move of plan.moves) {
    const bucket = movesByZone.get(move.from.zoneId);
    if (bucket) bucket.codes.push(move.value);
    else movesByZone.set(move.from.zoneId, { zoneName: move.from.zoneName, codes: [move.value] });
  }

  return (
    <div className="flex flex-col gap-5">
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Count value={plan.added.length} label="New" tone="positive" />
        <Count value={plan.moves.length} label="Moved here" tone="warning" />
        <Count value={plan.unchanged.length} label="Already here" />
        <Count value={plan.removed.length} label="Dropped" tone={plan.removed.length > 0 ? "negative" : "neutral"} />
      </div>

      {movesByZone.size > 0 ? (
        <Section title="Moving out of another zone">
          <ul className="m-0 flex list-none flex-col gap-3 p-0">
            {[...movesByZone].map(([zoneId, bucket]) => (
              <li key={zoneId} className="flex flex-col gap-1.5">
                <p className="text-sm text-ink-soft">
                  <span className="font-semibold">{bucket.codes.length}</span> leaving{" "}
                  <span className="font-semibold">{bucket.zoneName}</span>
                </p>
                <CodeList codes={bucket.codes} />
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {plan.removed.length > 0 ? (
        <Section title="Dropped from this zone">
          <p className="text-sm text-muted">
            These are in the zone today and missing from what you pasted. Saving removes them, and
            buyers there go back to “not mapped to any zone yet”.
          </p>
          <CodeList codes={plan.removed} />
        </Section>
      ) : null}

      {plan.duplicates.length > 0 ? (
        <Section title="Written more than once">
          <p className="text-sm text-muted">
            Collapsed to one each. Nothing is lost — this is only so the count adds up.
          </p>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-ink-soft">
            {plan.duplicates.map((duplicate) => (
              <li key={duplicate.value} className="tnum">
                {duplicate.value} — lines {duplicate.lines.join(", ")}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {plan.rejected.length > 0 ? (
        <Section title={`Refused (${plan.rejected.length})`}>
          <ul className="m-0 flex max-h-40 list-none flex-col gap-1 overflow-y-auto p-0 text-sm text-ink-soft">
            {plan.rejected.map((entry) => (
              <li key={`${entry.line}-${entry.raw}`}>
                <span className="tnum font-semibold">Line {entry.line}:</span> “{entry.raw}” —{" "}
                {rejectionMessage(entry.reason)}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}

/** The import's extra facts: what blocks the file, and where each code is headed. */
export function ImportBlockers({ plan }: { plan: ImportPlan }) {
  if (plan.duplicates.length === 0 && plan.unknownZones.length === 0 && plan.malformed.length === 0) {
    return null;
  }
  return (
    <div className="flex flex-col gap-4">
      {plan.duplicates.length > 0 ? (
        <Section title={`The same postal code appears twice (${plan.duplicates.length})`}>
          <p className="text-sm text-muted">
            A postal code belongs to exactly one zone, so a file that names one twice has no single
            right answer. Fix these lines and upload again — the import is all or nothing.
          </p>
          <ul className="m-0 flex max-h-40 list-none flex-col gap-1 overflow-y-auto p-0 text-sm text-ink-soft">
            {plan.duplicates.map((duplicate) => (
              <li key={duplicate.value} className="tnum">
                {duplicate.value} — lines {duplicate.lines.join(", ")}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {plan.unknownZones.length > 0 ? (
        <Section title={`Zone codes that don't exist (${plan.unknownZones.length})`}>
          <p className="text-sm text-muted">
            Create these zones first, or correct the spelling. Nothing can be written to a zone that
            isn't there.
          </p>
          <ul className="m-0 flex max-h-40 list-none flex-col gap-1 overflow-y-auto p-0 text-sm text-ink-soft">
            {plan.unknownZones.map((zone) => (
              <li key={zone.code}>
                <span className="font-semibold">{zone.code}</span>
                <span className="tnum"> — lines {zone.lines.slice(0, 8).join(", ")}</span>
                {zone.lines.length > 8 ? ` and ${zone.lines.length - 8} more` : null}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {plan.malformed.length > 0 ? (
        <Section title={`Rows that weren't two values (${plan.malformed.length})`}>
          <ul className="m-0 flex max-h-40 list-none flex-col gap-1 overflow-y-auto p-0 text-sm text-ink-soft">
            {plan.malformed.map((row) => (
              <li key={row.line}>
                <span className="tnum font-semibold">Line {row.line}:</span> {row.detail}
              </li>
            ))}
          </ul>
        </Section>
      ) : null}
    </div>
  );
}
