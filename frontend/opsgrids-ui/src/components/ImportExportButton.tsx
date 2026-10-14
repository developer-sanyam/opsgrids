/**
 * The one Import / Export button (ADR-0022). Presentational: it renders the state a data-exchange
 * hook reports and calls the actions it is given. It imports no HTTP and no spreadsheet code; the
 * app wires `useImportExport` from `@opsgrids/client` to it.
 *
 * The view types repeat `@opsgrids/client`'s `DataExchange*` types field for field (the two
 * packages may not import each other). An app that passes one to the other fails its typecheck on
 * any drift. They are declared in `lib/importExportView.ts` and re-exported here, because the
 * phase views need them too and importing them from this file would be an import cycle.
 */

import { useEffect, useRef, useState } from "react";

import { FileTextIcon } from "../icons";
import type { ImportExportButtonProps } from "../lib/importExportView";
import { Button } from "./Button";
import { ImportExportDialog } from "./ImportExportDialog";

export type {
  ImportExportBaselineUse,
  ImportExportButtonProps,
  ImportExportMeta,
  ImportExportMode,
  ImportExportPhase,
  ImportExportPreview,
  ImportExportPreviewAction,
  ImportExportPreviewRow,
  ImportExportProgress,
  ImportExportRowOutcome,
  ImportExportRowStatus,
  ImportExportSummary,
  ImportExportViewActions,
  ImportExportViewState,
} from "../lib/importExportView";

/**
 * A secondary button that opens the Import / Export dialog: the sample sheet, importing a filled
 * one, and exporting the list. It is the only thing a list screen adds to its header for all three.
 *
 * It renders nothing at all when the person may neither import nor export. Hiding it is
 * presentation and not security: every request it makes is checked by the server, which refuses
 * a row this person may not create whatever the button showed.
 */
export function ImportExportButton({
  meta,
  state,
  actions,
  canImport,
  canExport,
  open: controlledOpen,
  onOpenChange,
  hideTrigger = false,
}: ImportExportButtonProps) {
  const [ownOpen, setOwnOpen] = useState(false);
  const controlled = controlledOpen !== undefined;
  const open = controlled ? controlledOpen : ownOpen;
  const setOpen = (next: boolean) => {
    if (!controlled) setOwnOpen(next);
    onOpenChange?.(next);
  };
  const triggerRef = useRef<HTMLButtonElement>(null);
  const mayExport = canExport && meta.canExport;

  // `Dialog` returns focus to whatever had it when it opened, and a click does not focus a button
  // in every browser; so the way back is stated, not left to what the browser did.
  const wasOpen = useRef(false);
  useEffect(() => {
    if (wasOpen.current && !open) triggerRef.current?.focus();
    wasOpen.current = open;
  }, [open]);

  if (!canImport && !mayExport) return null;

  return (
    <>
      {hideTrigger ? null : (
        <Button
          ref={triggerRef}
          variant="secondary"
          aria-haspopup="dialog"
          leadingIcon={<FileTextIcon className="size-4" />}
          onClick={() => setOpen(true)}
        >
          Import / Export
        </Button>
      )}
      {open ? (
        <ImportExportDialog
          onClose={() => setOpen(false)}
          meta={meta}
          state={state}
          actions={actions}
          canImport={canImport}
          canExport={mayExport}
        />
      ) : null}
    </>
  );
}
