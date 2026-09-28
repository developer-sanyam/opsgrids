import { useEffect, useRef, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { ApiError, problemMessage, useAsync } from "@opsgrids/client";
import { Button, Card, ErrorState, InlineAlert, PageHeader, Skeleton, Tabs, TextField } from "@opsgrids/ui";

import { getTask, resolveLocationByBarcode } from "@/api/client";
import type { PickTaskLineOut } from "@/api/types";
import { RequireGrant } from "@/auth/RequireGrant";
import { useBarcodeScanner } from "@/lib/useBarcodeScanner";
import type { CameraScanStatus } from "@/lib/useBarcodeScanner";
import { useScanQueue } from "@/lib/useScanQueue";
import { shortId } from "@/lib/shortId";
import { CameraIcon, WifiOffIcon } from "@/ui/icons";

type Mode = "item" | "location";

interface Feedback {
  tone: "success" | "danger" | "warning";
  message: string;
}

export function ScanPage() {
  return (
    <RequireGrant permission="operations.fulfilment.task.execute" what="scanning">
      <ScanContent />
    </RequireGrant>
  );
}

function ScanContent() {
  const { taskId } = useParams<{ taskId: string }>();
  const state = useAsync(() => getTask(taskId ?? ""), [taskId]);

  if (!taskId) return null;

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6">
        <Skeleton className="h-9 w-32 rounded-tile" />
        <Skeleton className="h-64 w-full rounded-tile" />
      </div>
    );
  }

  if (state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader title="Scan" />
        <Card className="p-2">
          <ErrorState
            title="Couldn't load this task"
            message={problemMessage(state.error)}
            onRetry={state.reload}
            action={
              <Link to={`/tasks/${taskId}`} className="text-sm font-semibold text-accent hover:underline">
                Back to task
              </Link>
            }
          />
        </Card>
      </div>
    );
  }

  return <Scanner taskId={taskId} lines={state.data.lines} />;
}

function Scanner({ taskId, lines }: { taskId: string; lines: PickTaskLineOut[] }) {
  const [mode, setMode] = useState<Mode>("item");
  const [manualValue, setManualValue] = useState("");
  const [feedback, setFeedback] = useState<Feedback | null>(null);
  const [matchedLine, setMatchedLine] = useState<PickTaskLineOut | null>(null);
  const [resolving, setResolving] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  const queue = useScanQueue(taskId);

  const scanner = useBarcodeScanner((value) => void handleValue(value));

  useEffect(() => {
    scanner.start();
    return scanner.stop;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleValue(raw: string) {
    const value = raw.trim();
    if (!value) return;
    setMatchedLine(null);

    if (mode === "item") {
      const line = lines.find((candidate) => candidate.sku.trim().toLowerCase() === value.toLowerCase());
      if (line) {
        setFeedback({ tone: "success", message: `Matched ${line.sku}.` });
        setMatchedLine(line);
        queue.enqueue({ barcode: value, inventory_item_id: line.inventory_item_id });
      } else {
        setFeedback({ tone: "danger", message: `No line on this task expects "${value}".` });
        queue.enqueue({ barcode: value });
      }
      // Only try to send it now if the browser believes it's online — offline, this stays
      // queued rather than racing a `fetch` that a flaky connection might let through
      // out of order. The `online` event listener replays it once connectivity is back.
      if (queue.online) queue.replay();
      setManualValue("");
      return;
    }

    setResolving(true);
    try {
      const location = await resolveLocationByBarcode(value);
      const expected = lines.some(
        (line) => line.status !== "picked" && line.source_location_id === location.id,
      );
      setFeedback(
        expected
          ? { tone: "success", message: `Confirmed ${location.code || shortId(location.id)}.` }
          : { tone: "danger", message: `${location.code || shortId(location.id)} isn't a pick location for this task.` },
      );
      queue.enqueue({ barcode: value, location_id: location.id });
    } catch (err) {
      if (err instanceof ApiError && err.status === 403) {
        setFeedback({
          tone: "warning",
          message: "This location can't be verified right now — logged, but check the physical label carefully before picking.",
        });
      } else if (err instanceof ApiError && err.status === 404) {
        setFeedback({ tone: "danger", message: "We don't recognize that location barcode." });
      } else {
        setFeedback({
          tone: "warning",
          message: "Couldn't verify that location right now — logged, and will be checked again once you're back online.",
        });
      }
      queue.enqueue({ barcode: value });
    } finally {
      setResolving(false);
      if (queue.online) queue.replay();
      setManualValue("");
      inputRef.current?.focus();
    }
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title="Scan" description={`Task ${shortId(taskId)}`} />

      {!queue.online ? (
        <InlineAlert tone="warning" title="You're offline">
          <span className="inline-flex items-center gap-2">
            <WifiOffIcon className="size-4" aria-hidden />
            Scans are being saved on this phone and will sync automatically once you're back online.
          </span>
        </InlineAlert>
      ) : null}

      {queue.pending > 0 ? (
        <InlineAlert
          tone={queue.replaying ? "info" : "warning"}
          title={
            queue.replaying
              ? "Syncing queued scans…"
              : `${queue.pending} scan${queue.pending === 1 ? "" : "s"} waiting to sync`
          }
          action={
            queue.online ? (
              <Button variant="secondary" size="sm" onClick={queue.replay} disabled={queue.replaying}>
                Sync now
              </Button>
            ) : undefined
          }
        >
          {queue.replayError ? problemMessage(queue.replayError) : null}
        </InlineAlert>
      ) : null}

      <Tabs
        label="What are you scanning"
        variant="segmented"
        value={mode}
        onChange={(id) => setMode(id as Mode)}
        items={[
          { id: "item", label: "Item" },
          { id: "location", label: "Location" },
        ]}
      />

      <Card className="flex flex-col gap-3 p-4">
        {scanner.status === "active" ? (
          <video ref={scanner.videoRef} className="aspect-video w-full rounded-tile bg-ink object-cover" muted playsInline />
        ) : (
          <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-tile bg-sunken text-center">
            <CameraIcon className="size-6 text-faint" aria-hidden />
            <p className="max-w-xs px-4 text-xs text-muted">{cameraStatusMessage(scanner.status, scanner.errorMessage)}</p>
            {scanner.status === "unsupported" || scanner.status === "denied" || scanner.status === "no-camera" || scanner.status === "error" ? (
              <p className="max-w-xs px-4 text-xs text-muted">Use the field below instead — it always works.</p>
            ) : null}
          </div>
        )}

        <form
          onSubmit={(event) => {
            event.preventDefault();
            void handleValue(manualValue);
          }}
          className="flex items-end gap-2"
        >
          <TextField
            ref={inputRef}
            label={mode === "item" ? "Item barcode or SKU" : "Location barcode"}
            containerClassName="flex-1"
            value={manualValue}
            onChange={(event) => setManualValue(event.target.value)}
            autoComplete="off"
          />
          <Button type="submit" disabled={resolving || manualValue.trim() === ""} loading={resolving}>
            Log
          </Button>
        </form>
      </Card>

      {feedback ? (
        <InlineAlert tone={feedback.tone} title={feedback.message} />
      ) : null}

      {matchedLine ? (
        <Link
          to={`/tasks/${taskId}/lines/${matchedLine.id}/pick`}
          className="flex h-12 items-center justify-center rounded-tile bg-accent px-4 font-semibold text-on-accent transition-colors hover:bg-accent-strong"
        >
          Confirm pick for {matchedLine.sku}
        </Link>
      ) : null}
    </div>
  );
}

function cameraStatusMessage(status: CameraScanStatus, errorMessage: string | null): string {
  switch (status) {
    case "unsupported":
      return "This browser can't scan with the camera.";
    case "requesting":
      return "Asking for camera access…";
    case "denied":
      return "Camera access was denied.";
    case "no-camera":
      return "No camera was found on this device.";
    case "error":
      return errorMessage ?? "Couldn't start the camera.";
    default:
      return "Camera off.";
  }
}
