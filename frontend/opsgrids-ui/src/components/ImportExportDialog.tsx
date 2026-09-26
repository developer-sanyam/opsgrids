import { useEffect, useRef, useState } from "react";
import type { ChangeEvent, DragEvent, ReactNode } from "react";

import { RefreshIcon } from "../icons";
import { liveMessage, phaseTitle } from "../lib/importExportCopy";
import type {
  ImportExportMeta,
  ImportExportViewActions,
  ImportExportViewState,
} from "../lib/importExportView";
import { useUnsavedChangesWarning } from "../lib/useUnsavedChangesWarning";
import { Button } from "./Button";
import { Dialog } from "./Dialog";
import { ImportExportMenu } from "./ImportExportMenu";
import { ImportExportResult, ImportExportResultActions } from "./ImportExportResult";
import { ImportExportReview, ImportExportReviewActions } from "./ImportExportReview";
import { ImportExportRunning, ImportExportRunningActions } from "./ImportExportRunning";
import { InlineAlert } from "./InlineAlert";
import { Skeleton } from "./States";

/** Only `.xlsx` (ADR-0022). The extension for desktop pickers, the type for phones' file apps. */
const ACCEPT = ".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";

/**
 * The dialog's own heading, found the way a screen reader finds it: by the `aria-labelledby` the
 * dialog carries. `Dialog` keeps that heading's ref to itself, and it is the element focus should
 * land on when the phase, and with it the title, changes.
 */
function headingOf(inside: HTMLElement | null): HTMLElement | null {
  const id = inside?.closest('[role="dialog"]')?.getAttribute("aria-labelledby");
  return id ? document.getElementById(id) : null;
}

function isFileDrag(event: DragEvent): boolean {
  return Array.from(event.dataTransfer.types).includes("Files");
}

function Reading() {
  return (
    <div aria-busy="true" className="flex flex-col gap-4">
      <div className="flex items-center gap-3 text-ink-soft">
        <RefreshIcon aria-hidden className="spin size-5 shrink-0" />
        <p className="text-body">Checking the columns and every row. A large file takes a few seconds.</p>
      </div>
      <div className="flex flex-col gap-3">
        <Skeleton className="h-4 w-1/3" />
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
        <Skeleton className="h-8" />
      </div>
    </div>
  );
}

interface ImportExportDialogProps {
  /** Called once the dialog has reset the session and is ready to go. */
  onClose: () => void;
  meta: ImportExportMeta;
  state: ImportExportViewState;
  actions: ImportExportViewActions;
  canImport: boolean;
  /** Already the answer to both "may read" and "the list can be exported at all". */
  canExport: boolean;
}

/**
 * The Import / Export dialog, one phase at a time. Mounted only while it is open, so closing it
 * drops everything local to it: a half-made drag, a stopped cancel, a blocked close.
 *
 * **Focus.** On open it lands on the first action. When the phase changes the control that had it
 * is usually gone, so focus moves to the new heading, where a screen reader reads what the phase
 * is before anything else. Closing returns it to the button that opened the dialog (`Dialog`).
 *
 * **Closing.** Escape, the close control and Done all end the session with `reset()`. While rows
 * are going out none of them does anything but say so: a dialog that vanishes mid-import leaves
 * the user unsure whether it is still writing. The browser's own leave-the-page prompt covers the
 * tab being closed.
 *
 * **Files.** One hidden input serves "Import from Excel" and "Choose another file", and dropping a
 * file works in the menu and the preview. The engine decides whether the file is acceptable, so
 * this passes on whatever it is given. Elsewhere in the dialog a dragged file is refused, because
 * the browser's default for a dropped file is to navigate to it, and that would end an import.
 */
export function ImportExportDialog({ onClose, meta, state, actions, canImport, canExport }: ImportExportDialogProps) {
  const [dragging, setDragging] = useState(false);
  const [blocked, setBlocked] = useState(false);
  const dragDepth = useRef(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const firstActionRef = useRef<HTMLButtonElement>(null);
  const phase = state.phase;
  const importing = phase === "importing";
  const takesFiles = (phase === "menu" && canImport) || phase === "preview";

  useUnsavedChangesWarning(importing);

  const shownPhase = useRef(phase);
  useEffect(() => {
    if (shownPhase.current === phase) return;
    shownPhase.current = phase;
    headingOf(bodyRef.current)?.focus();
  }, [phase]);

  function requestClose() {
    if (importing) {
      setBlocked(true);
      return;
    }
    if (state.exporting !== null) actions.cancelExport();
    actions.reset();
    onClose();
  }

  function chooseFile() {
    inputRef.current?.click();
  }

  function onPicked(event: ChangeEvent<HTMLInputElement>) {
    // Read before the value is cleared: clearing empties the live list. Cleared so that choosing
    // the same file again, after fixing it in Excel, still fires a change.
    const file = event.target.files?.[0];
    event.target.value = "";
    if (file !== undefined) void actions.openFile(file);
  }

  // Every child the drag passes over fires its own enter and leave. Counting them, rather than
  // asking where the pointer went (Safari does not say), is what keeps the drop cue from flickering.
  function onDragEnter(event: DragEvent) {
    if (!takesFiles || !isFileDrag(event)) return;
    event.preventDefault();
    dragDepth.current += 1;
    setDragging(true);
  }

  function onDragOver(event: DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    event.dataTransfer.dropEffect = takesFiles ? "copy" : "none";
  }

  function onDragLeave() {
    dragDepth.current = Math.max(0, dragDepth.current - 1);
    if (dragDepth.current === 0) setDragging(false);
  }

  function onDrop(event: DragEvent) {
    if (!isFileDrag(event)) return;
    event.preventDefault();
    dragDepth.current = 0;
    setDragging(false);
    const file = event.dataTransfer.files[0];
    if (takesFiles && file !== undefined) void actions.openFile(file);
  }

  let content: ReactNode;
  let footer: ReactNode = undefined;
  switch (phase) {
    case "menu":
      content = (
        <ImportExportMenu
          meta={meta}
          state={state}
          actions={actions}
          canImport={canImport}
          canExport={canExport}
          dragging={dragging}
          onChooseFile={chooseFile}
          firstActionRef={firstActionRef}
        />
      );
      footer = (
        <Button variant="ghost" onClick={requestClose}>
          Close
        </Button>
      );
      break;
    case "reading":
      content = <Reading />;
      break;
    case "preview":
      content = state.preview === null ? <Reading /> : <ImportExportReview preview={state.preview} />;
      footer =
        state.preview === null ? undefined : (
          <ImportExportReviewActions
            preview={state.preview}
            onChooseAnother={chooseFile}
            onImport={() => actions.startImport()}
          />
        );
      break;
    case "importing":
      content = <ImportExportRunning progress={state.progress} closeBlocked={blocked} />;
      footer = <ImportExportRunningActions onCancel={() => actions.cancelImport()} />;
      break;
    case "done":
      content = <ImportExportResult summary={state.summary} meta={meta} notice={state.notice} />;
      footer = <ImportExportResultActions onDownload={() => actions.downloadReport()} onDone={requestClose} />;
      break;
    case "error":
      content = (
        <InlineAlert tone="danger" title={state.error ?? "Something went wrong while reading your file."}>
          Choose the file again, or download the sample sheet to see the columns and format we expect.
        </InlineAlert>
      );
      footer = (
        <>
          <Button variant="ghost" onClick={requestClose}>
            Close
          </Button>
          <Button variant="primary" onClick={() => actions.reset()}>
            Try again
          </Button>
        </>
      );
      break;
  }

  return (
    <Dialog
      open
      onClose={requestClose}
      title={phaseTitle(state, meta)}
      size="lg"
      {...(phase === "menu" ? { initialFocusRef: firstActionRef } : {})}
      footer={footer}
    >
      <div
        ref={bodyRef}
        className="flex flex-col gap-4"
        onDragEnter={onDragEnter}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
      >
        {content}
        <p aria-live="polite" aria-atomic="true" className="sr-only">
          {liveMessage(state)}
        </p>
        <input
          ref={inputRef}
          type="file"
          hidden
          tabIndex={-1}
          aria-hidden
          accept={ACCEPT}
          onChange={onPicked}
        />
      </div>
    </Dialog>
  );
}
