import { useCallback, useRef, useState } from "react";

import { ApiError, useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  InlineAlert,
  PaperclipIcon,
  RadioGroup,
  SectionHeader,
  Skeleton,
} from "@opsgrids/ui";

import { importPostalCodes, loadPostalCodeMap } from "./api";
import { DeliveryProblem } from "./DeliveryProblem";
import { ImportBlockers, PlanSummary } from "./PlanSummary";
import { planImport } from "./postalCodes";
import type { ImportPlan, PostalCodeMap } from "./postalCodes";
import type { ImportMode, ImportResultOut, ZoneOut } from "./types";

/**
 * The whole-map CSV import: two columns, `postal_code,zone_code` (ADR-0010 §9).
 *
 * What makes it safe is the order of operations. **The file is read in the browser, planned
 * against the current map, and shown to the merchant before a single byte is posted.** A 19,000-row
 * file that would move four hundred codes out of a zone they thought they were growing is a
 * decision, and a decision needs to be visible while it is still reversible.
 *
 * Two of the checks are hard stops, matching the server's own refusals: a postal code written twice
 * has no single right answer, and a `zone_code` that names nothing has no destination. Both are
 * reported by line number. The client check mirrors the server's rule and does not replace it — the
 * import is all-or-nothing on the server whatever this screen decided.
 */
export function PostalCodeImport({
  zones,
  onImported,
}: {
  zones: readonly ZoneOut[];
  onImported: (message: string) => void;
}) {
  const zoneKey = zones.map((zone) => zone.id).join(",");
  const load = useCallback(() => loadPostalCodeMap(zones), [zones]);
  const state = useAsync(load, [zoneKey]);

  return (
    <Card className="flex flex-col gap-5 p-5 sm:p-6">
      <SectionHeader
        title="Import a postal-code map"
        subtitle="A CSV of postal_code, zone_code. Nothing is written until you have read what it would do."
      />

      {state.status === "loading" ? (
        <div className="flex flex-col gap-3">
          <Skeleton className="h-9 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </div>
      ) : null}

      {state.status === "error" ? (
        <DeliveryProblem
          error={state.error}
          subject="Your current postal-code map"
          onRetry={() => window.location.reload()}
        />
      ) : null}

      {state.status === "success" ? (
        <ImportForm zones={zones} map={state.data} onImported={onImported} />
      ) : null}
    </Card>
  );
}

type Phase =
  | { step: "choosing" }
  | { step: "planned"; fileName: string; csv: string; plan: ImportPlan }
  | { step: "importing"; fileName: string }
  | { step: "done"; result: ImportResultOut };

function ImportForm({
  zones,
  map,
  onImported,
}: {
  zones: readonly ZoneOut[];
  map: PostalCodeMap;
  onImported: (message: string) => void;
}) {
  const [mode, setMode] = useState<ImportMode>("merge");
  const [phase, setPhase] = useState<Phase>({ step: "choosing" });
  const [failure, setFailure] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);

  async function onFile(file: File) {
    setFailure(null);
    try {
      const csv = await file.text();
      setPhase({
        step: "planned",
        fileName: file.name,
        csv,
        plan: planImport(csv, mode, zones, map),
      });
    } catch {
      setFailure("That file couldn't be read. Export it as CSV and try again.");
    } finally {
      if (fileRef.current) fileRef.current.value = "";
    }
  }

  function changeMode(next: ImportMode) {
    setMode(next);
    // Re-plan against the same file: `replace` and `merge` differ by every removal, which is the
    // whole reason the toggle exists.
    setPhase((current) =>
      current.step === "planned"
        ? { ...current, plan: planImport(current.csv, next, zones, map) }
        : current,
    );
  }

  async function runImport(csv: string, fileName: string) {
    if (phase.step === "importing") return;
    setPhase({ step: "importing", fileName });
    setFailure(null);
    try {
      const result = await importPostalCodes(csv, mode);
      setPhase({ step: "done", result });
      onImported(
        `Imported ${fileName}: ${result.rows_read} rows read, ${result.inserted} inserted, ${result.updated} updated, ${result.deleted} deleted.`,
      );
    } catch (error) {
      // A failed import wrote nothing — it is one transaction on the server (§9). The plan is kept
      // so the merchant can retry without re-picking the file.
      setPhase({ step: "planned", fileName, csv, plan: planImport(csv, mode, zones, map) });
      setFailure(
        error instanceof ApiError
          ? error.message
          : "Couldn't reach the server. Nothing was imported.",
      );
    }
  }

  if (phase.step === "done") {
    const { result } = phase;
    return (
      <div className="flex flex-col gap-4">
        {/* The server's own five counters (ADR-0010 §9), not the preview's. The preview said what
            *would* happen; this says what did. */}
        <InlineAlert tone="success" title="Import applied">
          {result.rows_read} rows read — {result.inserted} inserted, {result.updated} updated,{" "}
          {result.deleted} deleted. Applied as <span className="font-semibold">{result.mode}</span>.
        </InlineAlert>
        {(result.rejected ?? 0) > 0 ? (
          <InlineAlert tone="warning" title={`${result.rejected} rows were rejected`}>
            {/* ADR-0010 §9 says the offending line numbers come back in the response body; the
                published schema carries only a count. Until it carries more, the line numbers a
                merchant can act on are the ones the preview found before the upload, and this says
                so rather than implying the server listed them. */}
            The server did not say which. The preview above lists every line this browser could
            fault before the upload — re-check the file against it.
          </InlineAlert>
        ) : null}
        <div>
          <Button variant="outline" onClick={() => setPhase({ step: "choosing" })}>
            Import another file
          </Button>
        </div>
      </div>
    );
  }

  const importing = phase.step === "importing";

  return (
    <div className="flex flex-col gap-5">
      {failure !== null ? (
        <InlineAlert tone="danger" title="That import didn't run">
          {failure}
        </InlineAlert>
      ) : null}

      <RadioGroup
        legend="How to apply it"
        name="import-mode"
        value={mode}
        onChange={(value) => changeMode(value as ImportMode)}
        options={[
          {
            value: "merge",
            label: "Merge",
            description:
              "Adds and moves what is in the file. Anything not mentioned keeps its current zone.",
          },
          {
            value: "replace",
            label: "Replace everything",
            description:
              "Clears your whole postal-code map first, then applies the file. This is what re-uploading a master file means — and it is the only way removals take effect.",
          },
        ]}
      />

      <div className="flex flex-wrap items-center gap-3">
        <label className="inline-flex h-11 cursor-pointer items-center gap-2 rounded-full border border-line bg-surface px-5 font-semibold text-ink transition-colors hover:bg-ground focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2">
          <PaperclipIcon className="size-4" aria-hidden />
          {phase.step === "choosing" ? "Choose a CSV" : "Choose a different CSV"}
          <input
            ref={fileRef}
            type="file"
            accept=".csv,text/csv"
            className="sr-only"
            disabled={importing}
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) void onFile(file);
            }}
          />
        </label>
        <p className="text-xs text-muted">
          Two columns, <span className="font-semibold">postal_code</span> and{" "}
          <span className="font-semibold">zone_code</span>. A header row is optional.
        </p>
      </div>

      {phase.step === "choosing" ? (
        <p className="text-sm text-muted">
          Nothing chosen yet. Around nineteen thousand rows is the size this is built for — a file
          that large is roughly 230 KB and imports in a couple of seconds.
        </p>
      ) : null}

      {phase.step === "planned" || phase.step === "importing" ? (
        <ImportPreview
          phase={phase}
          importing={importing}
          onRun={runImport}
          onCancel={() => setPhase({ step: "choosing" })}
        />
      ) : null}
    </div>
  );
}

function ImportPreview({
  phase,
  importing,
  onRun,
  onCancel,
}: {
  phase: Extract<Phase, { step: "planned" } | { step: "importing" }>;
  importing: boolean;
  onRun: (csv: string, fileName: string) => Promise<void>;
  onCancel: () => void;
}) {
  if (phase.step === "importing") {
    return (
      <div aria-live="polite" className="border-t border-line pt-4">
        <p className="text-sm font-semibold text-ink">Importing {phase.fileName}…</p>
        <p className="text-sm text-muted">
          One transaction — it either all lands or none of it does.
        </p>
      </div>
    );
  }

  const { plan, csv, fileName } = phase;

  return (
    <div className="flex flex-col gap-4 border-t border-line pt-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h3 className="text-sm font-bold text-ink">{fileName}</h3>
        <p className="text-xs text-muted tnum">{plan.rowsRead} rows read</p>
      </div>

      {plan.blocked ? (
        <InlineAlert tone="danger" title="This file can't be imported as it is">
          The import is all or nothing, so these have to be fixed first. Nothing has been sent.
        </InlineAlert>
      ) : null}

      <ImportBlockers plan={plan} />

      {plan.mode === "replace" && plan.removed.length > 0 ? (
        <InlineAlert
          tone="warning"
          title={`Replace drops ${plan.removed.length} postal ${plan.removed.length === 1 ? "code" : "codes"}`}
        >
          They are mapped today and are not in this file. Buyers there go back to being told the
          store doesn't deliver to them.
        </InlineAlert>
      ) : null}

      <PlanSummary plan={plan} />

      {plan.perZone.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h4 className="text-sm font-bold text-ink">Where the file puts them</h4>
          <ul className="m-0 flex list-none flex-col gap-1 p-0 text-sm text-ink-soft">
            {plan.perZone.map((zone) => (
              <li key={zone.zoneId}>
                <span className="tnum font-semibold">{zone.count}</span> into {zone.zoneName}{" "}
                <span className="text-faint">({zone.zoneCode})</span>
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
        <Button variant="ghost" onClick={onCancel} disabled={importing}>
          Cancel
        </Button>
        <Button
          variant={plan.mode === "replace" ? "danger" : "primary"}
          onClick={() => void onRun(csv, fileName)}
          disabled={importing || plan.blocked || plan.save.length === 0}
        >
          {plan.mode === "replace" ? "Replace the whole map" : "Import these postal codes"}
        </Button>
      </div>
    </div>
  );
}
