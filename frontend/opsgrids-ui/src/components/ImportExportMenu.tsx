import { useId, useRef } from "react";
import type { ReactNode, Ref, RefObject } from "react";

import { ArrowUp, ChevronRightIcon, DownloadIcon, FileTextIcon, RefreshIcon } from "../icons";
import { cn } from "../lib/cn";
import { formatCount } from "../lib/importExportCopy";
import type {
  ImportExportMeta,
  ImportExportMode,
  ImportExportViewActions,
  ImportExportViewState,
} from "../lib/importExportView";
import { Button } from "./Button";
import { ImportExportProgressBar } from "./ImportExportProgressBar";
import { InlineAlert } from "./InlineAlert";

interface ActionRowProps {
  icon: ReactNode;
  title: string;
  /** One line, under the title. Tied to the button as its description, not part of its name. */
  description: string;
  onSelect: () => void;
  /** Working: the row says so and ignores clicks, but keeps focus and stays in the tab order. */
  busy?: boolean | undefined;
  /** The action the dialog exists for: it takes the accent. */
  primary?: boolean | undefined;
  /** A file is being dragged over the dialog and this row is where it will land. */
  dragging?: boolean | undefined;
  ref?: Ref<HTMLButtonElement> | undefined;
}

/**
 * One of the three things the dialog offers, as a real button with a sentence under it.
 *
 * `busy` is `aria-disabled`, not `disabled`: a disabled button that had focus drops it, and the
 * keyboard user who pressed Enter on "Download sample sheet" would find themselves nowhere while
 * the file is being built.
 */
function ActionRow({ icon, title, description, onSelect, busy = false, primary = false, dragging = false, ref }: ActionRowProps) {
  const base = useId();
  const titleId = `${base}-title`;
  const descriptionId = `${base}-description`;
  return (
    <button
      ref={ref}
      type="button"
      aria-labelledby={titleId}
      aria-describedby={descriptionId}
      aria-disabled={busy || undefined}
      aria-busy={busy || undefined}
      data-dragging={dragging || undefined}
      onClick={() => {
        if (!busy) onSelect();
      }}
      className={cn(
        "flex w-full items-center gap-4 rounded-tile border bg-surface p-4 text-left",
        "transition-colors duration-(--duration-quick)",
        primary ? "border-accent" : "border-line",
        "hover:bg-ground active:bg-ground aria-disabled:cursor-progress aria-disabled:hover:bg-surface",
        "data-dragging:border-dashed data-dragging:bg-ground",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "grid size-10 shrink-0 place-items-center rounded-tile",
          primary ? "bg-accent text-on-accent" : "bg-sunken text-ink-soft",
        )}
      >
        {icon}
      </span>
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span id={titleId} className="text-card-title text-ink">
          {title}
        </span>
        <span id={descriptionId} className="text-body-sm text-muted">
          {description}
        </span>
      </span>
      {busy ? (
        <RefreshIcon aria-hidden className="spin size-5 shrink-0 text-muted" />
      ) : (
        <ChevronRightIcon aria-hidden className="size-4 shrink-0 text-muted" />
      )}
    </button>
  );
}

interface MenuPhaseProps {
  meta: ImportExportMeta;
  state: ImportExportViewState;
  actions: ImportExportViewActions;
  canImport: boolean;
  canExport: boolean;
  /** The choice between adding only and updating too, or null where the list cannot be updated or the
   *  person may not import. It sits between the sample sheet and Import, the action it changes. */
  modeChoice: ReactNode;
  /** A file is being dragged over the dialog. */
  dragging: boolean;
  onChooseFile: () => void;
  /** The first action shown, so the dialog can put focus on it when it opens. */
  firstActionRef: RefObject<HTMLButtonElement | null>;
}

/** The rules, in plain words, for the actions this person has. */
function rulesFor(meta: ImportExportMeta, canImport: boolean, canExport: boolean, mode: ImportExportMode): string[] {
  const rules: string[] = [];
  if (canImport) {
    rules.push(`Up to ${formatCount(meta.maxRows)} rows per file.`);
    // The rules of updating are under the choice; "never overwritten" would contradict them.
    if (mode === "add") rules.push("Rows that already exist are skipped, never overwritten.");
    rules.push("The file is read in your browser. It is never uploaded.");
    if (meta.requiredHeaders.length > 0) {
      rules.push(`Required columns: ${meta.requiredHeaders.join(", ")}.`);
    }
    rules.push(
      "Only .xlsx files work. To use a CSV or an older .xls file, open it in Excel and save it as an Excel Workbook.",
    );
  }
  if (canExport) {
    rules.push(`Export includes up to ${formatCount(meta.exportMaxRows)} rows matching the current filters.`);
  }
  return rules;
}

/** The menu phase: the three actions, the rules, and the last download's failure if it had one. */
export function ImportExportMenu({
  meta,
  state,
  actions,
  canImport,
  canExport,
  modeChoice,
  dragging,
  onChooseFile,
  firstActionRef,
}: MenuPhaseProps) {
  const ownExportRef = useRef<HTMLButtonElement>(null);
  // When Export is the only action it is also the first one, and one ref serves both.
  const exportRef = canImport ? ownExportRef : firstActionRef;
  const exporting = state.exporting;

  const exportDescription =
    exporting === null
      ? `Download the ${meta.noun} matching your current filters as an Excel file.`
      : exporting.total > 0
        ? `Exported ${formatCount(exporting.fetched)} of ${formatCount(exporting.total)}`
        : "Preparing the export…";

  function cancelExport() {
    actions.cancelExport();
    // The Cancel button is about to leave the page; focus goes to the row it belongs to.
    exportRef.current?.focus();
  }

  return (
    <div className="flex flex-col gap-4">
      {state.notice !== null ? <InlineAlert tone="danger" title={state.notice} /> : null}

      <div className="flex flex-col gap-3">
        {canImport ? (
          <>
            <ActionRow
              ref={firstActionRef}
              icon={<FileTextIcon className="size-5" />}
              title="Download sample sheet"
              description={
                state.downloadingTemplate
                  ? "Preparing the sample sheet…"
                  : `An Excel file with the columns, example rows and instructions for ${meta.noun}.`
              }
              busy={state.downloadingTemplate}
              onSelect={() => void actions.downloadTemplate()}
            />
            {modeChoice}
            <ActionRow
              primary
              dragging={dragging}
              icon={<ArrowUp className="size-5" />}
              title="Import from Excel"
              description={
                dragging
                  ? "Drop the file to read it."
                  : `${state.mode === "addAndUpdate" ? "Add and update" : "Add"} ${meta.noun} from an .xlsx file. Choose a file or drop it here.`
              }
              onSelect={onChooseFile}
            />
          </>
        ) : null}

        {canExport ? (
          <div className="flex flex-col gap-3">
            <ActionRow
              ref={exportRef}
              icon={<DownloadIcon className="size-5" />}
              title="Export to Excel"
              description={exportDescription}
              busy={exporting !== null}
              onSelect={() => void actions.exportAll()}
            />
            {exporting !== null ? (
              <div className="flex items-center gap-3 px-1">
                <div className="min-w-0 flex-1">
                  <ImportExportProgressBar
                    label="Export progress"
                    value={exporting.fetched}
                    max={exporting.total}
                    valueText={`Exported ${formatCount(exporting.fetched)} of ${formatCount(exporting.total)}`}
                  />
                </div>
                <Button variant="secondary" size="sm" onClick={cancelExport}>
                  Cancel export
                </Button>
              </div>
            ) : null}
          </div>
        ) : null}
      </div>

      <ul aria-label="How importing and exporting work" className="flex list-disc flex-col gap-1 pl-4 text-body-sm text-muted">
        {rulesFor(meta, canImport, canExport, state.mode).map((rule) => (
          <li key={rule}>{rule}</li>
        ))}
      </ul>
    </div>
  );
}
