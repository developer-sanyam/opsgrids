import { useEffect, useRef, useState } from "react";

import { Button } from "@opsgrids/ui";

import { barcodeProblem, cleanBarcode } from "@/lib/putawayForm";
import { useBarcodeScanner } from "@/lib/useBarcodeScanner";
import type { CameraScanStatus } from "@/lib/useBarcodeScanner";
import { CameraIcon } from "@/ui/icons";
import { ScanInput } from "@/ui/ScanInput";

function cameraMessage(status: CameraScanStatus, errorMessage: string | null): string {
  switch (status) {
    case "unsupported":
      return "This browser can't scan with the camera. Type the label below instead.";
    case "requesting":
      return "Asking for camera access…";
    case "denied":
      return "Camera access was denied. Type the label below instead.";
    case "no-camera":
      return "No camera was found. Type the label below instead.";
    case "error":
      return errorMessage ?? "Couldn't start the camera. Type the label below instead.";
    default:
      return "Point the camera at the bin's label, or use a scanner or the keyboard below.";
  }
}

/** Mounted only while a label is being looked for, so the camera is released the moment one is
 *  accepted (the scanner hook stops its stream when it unmounts). */
function Camera({ onDetected }: { onDetected: (value: string) => void }) {
  const scanner = useBarcodeScanner(onDetected);
  return (
    <div className="flex flex-col gap-2">
      {scanner.status === "active" ? (
        <video
          ref={scanner.videoRef}
          aria-label="Camera view for scanning the bin label"
          className="aspect-video w-full rounded-tile bg-ink object-cover"
          muted
          playsInline
        />
      ) : (
        <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-tile bg-sunken text-center">
          <CameraIcon className="size-6 text-faint" aria-hidden />
          <p className="max-w-xs px-4 text-xs text-muted">{cameraMessage(scanner.status, scanner.errorMessage)}</p>
        </div>
      )}
      {scanner.status === "active" ? (
        <Button variant="secondary" size="sm" onClick={scanner.stop}>
          Turn camera off
        </Button>
      ) : scanner.status === "requesting" ? null : (
        <Button variant="secondary" size="sm" onClick={scanner.start} leadingIcon={<CameraIcon className="size-4" />}>
          Scan with camera
        </Button>
      )}
    </div>
  );
}

interface PutawayBinScanProps {
  /** The accepted label, or `null` while one is still being looked for. */
  label: string | null;
  onAccept: (label: string) => void;
  onClear: () => void;
}

/**
 * Step three: the bin label. The camera (`BarcodeDetector`, where the browser has it), a hardware
 * scanner typing into the field, or the keyboard all land in the same place — a label, trimmed of
 * the CR/LF a scanner adds, that the worker accepts before anything is sent. The server resolves it
 * to a bin; nothing here guesses which bin it is.
 */
export function PutawayBinScan({ label, onAccept, onClear }: PutawayBinScanProps) {
  const [draft, setDraft] = useState("");
  const [problem, setProblem] = useState<string | undefined>(undefined);
  const fieldRef = useRef<HTMLInputElement>(null);
  const acceptedRef = useRef<HTMLDivElement>(null);

  // This step mounts when stock is chosen, and swaps one control for another each time the label
  // is accepted or changed — so it says where focus goes. Looking for a label: the field, because a
  // keyboard-wedge scanner types into whatever has focus. Accepted: the label itself, a place a
  // scanner's trailing Enter does nothing, so a second read can never confirm the put-away.
  useEffect(() => {
    if (label === null) fieldRef.current?.focus();
    else acceptedRef.current?.focus();
  }, [label]);

  function accept(raw: string) {
    const cleaned = cleanBarcode(raw);
    const invalid = barcodeProblem(cleaned);
    setDraft(cleaned);
    if (invalid) {
      setProblem(invalid);
      return;
    }
    setProblem(undefined);
    onAccept(cleaned);
  }

  if (label !== null) {
    return (
      <div className="flex items-center justify-between gap-3 rounded-tile bg-ground p-3">
        <div ref={acceptedRef} tabIndex={-1} className="flex min-w-0 flex-col gap-1">
          <p className="text-xs font-semibold text-muted">Bin label</p>
          <p className="truncate font-mono text-lg font-bold text-ink">{label}</p>
        </div>
        <Button variant="secondary" size="sm" aria-label="Change bin label" onClick={onClear}>
          Change
        </Button>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <Camera onDetected={accept} />
      <ScanInput
        ref={fieldRef}
        label="Bin label"
        value={draft}
        onChange={(value) => {
          setDraft(value);
          setProblem(undefined);
        }}
        onSubmit={() => accept(draft)}
        placeholder="Scan the bin label, or type it and press enter"
        {...(problem !== undefined ? { error: problem } : {})}
      />
      <Button variant="secondary" onClick={() => accept(draft)} disabled={draft.trim() === ""}>
        Use this label
      </Button>
    </div>
  );
}
