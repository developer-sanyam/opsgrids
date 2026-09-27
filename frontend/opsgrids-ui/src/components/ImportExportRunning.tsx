import { useState } from "react";

import { formatCount } from "../lib/importExportCopy";
import type { ImportExportMode, ImportExportProgress } from "../lib/importExportView";
import { Button } from "./Button";
import { ImportExportProgressBar } from "./ImportExportProgressBar";
import { ImportExportStats } from "./ImportExportStats";
import { InlineAlert } from "./InlineAlert";

interface RunningProps {
  /** Null between the click and the runner's first report. */
  progress: ImportExportProgress | null;
  mode: ImportExportMode;
  /** Escape, the close control or the backdrop was tried while rows are going out. */
  closeBlocked: boolean;
}

/**
 * The importing phase: how far along it is in numbers, what happened to the rows so far, the wait
 * when the server asks for one, and the one instruction that matters, which is to leave the tab
 * alone.
 *
 * Nothing here is live. The countdown ticks every second and the rows every few hundred
 * milliseconds; the dialog's own polite region says the milestones instead.
 */
export function ImportExportRunning({ progress, mode, closeBlocked }: RunningProps) {
  const done = progress?.done ?? 0;
  const total = progress?.total ?? 0;
  const waiting = progress?.waiting ?? null;

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-2">
        <p className="tnum text-card-title text-ink">
          {total > 0 ? `${formatCount(done)} of ${formatCount(total)} rows sent` : "Starting the import…"}
        </p>
        <ImportExportProgressBar
          label="Import progress"
          value={done}
          max={total}
          valueText={total > 0 ? `${formatCount(done)} of ${formatCount(total)} rows sent` : "Starting"}
        />
      </div>

      <ImportExportStats
        columns="grid-cols-3"
        stats={
          mode === "add"
            ? [
                { label: "Created", value: progress?.created ?? 0 },
                { label: "Skipped, already exist", value: progress?.skipped ?? 0 },
                { label: "Failed", value: progress?.failed ?? 0 },
              ]
            : [
                { label: "Created", value: progress?.created ?? 0 },
                { label: "Updated", value: progress?.updated ?? 0 },
                { label: "Failed", value: progress?.failed ?? 0 },
              ]
        }
      />

      {waiting !== null ? (
        <InlineAlert tone="info" live="off" title={`Waiting for the rate limit… ${formatCount(waiting.seconds)} s`}>
          The server limits how fast rows can be sent. The import carries on by itself.
        </InlineAlert>
      ) : null}

      <p className="text-body-sm text-muted">
        {mode === "add"
          ? "Keep this tab open until the import finishes. Rows already created stay created if you cancel."
          : "Keep this tab open until the import finishes. Rows already created or updated stay that way if you cancel."}
      </p>
      <p role="status" className="text-body-sm font-semibold text-ink">
        {closeBlocked ? "The import is still running. Choose Cancel import to stop it." : ""}
      </p>
    </div>
  );
}

/** The importing phase's footer: the only way out is to stop, and stopping takes a moment. */
export function ImportExportRunningActions({ onCancel }: { onCancel: () => void }) {
  const [stopping, setStopping] = useState(false);
  return (
    <Button
      variant="secondary"
      loading={stopping}
      onClick={() => {
        if (stopping) return;
        setStopping(true);
        onCancel();
      }}
    >
      {stopping ? "Stopping…" : "Cancel import"}
    </Button>
  );
}
