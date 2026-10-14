import { Button } from "@opsgrids/ui";

import { useBarcodeScanner } from "@/lib/useBarcodeScanner";
import type { CameraScanStatus } from "@/lib/useBarcodeScanner";
import { CameraIcon } from "@/ui/icons";

function cameraMessage(status: CameraScanStatus, errorMessage: string | null, subject: string): string {
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
      return `Point the camera at ${subject}, or use a scanner or the keyboard below.`;
  }
}

interface ScanCameraProps {
  onDetected: (value: string) => void;
  /** What the camera is looking for, in the sentence "Point the camera at …" — "the bin's label". */
  subject: string;
  /** Names the video for a screen reader. */
  videoLabel: string;
}

/** The camera is the primary way in on a phone: it sits above the typed field on every screen
 *  that scans. Mounted only while something is being looked for, so the camera is released the
 *  moment it unmounts (the scanner hook stops its stream then). */
export function ScanCamera({ onDetected, subject, videoLabel }: ScanCameraProps) {
  const scanner = useBarcodeScanner(onDetected);
  return (
    <div className="flex flex-col gap-2">
      {scanner.status === "active" ? (
        <video
          ref={scanner.videoRef}
          aria-label={videoLabel}
          className="aspect-video w-full rounded-tile bg-ink object-cover"
          muted
          playsInline
        />
      ) : (
        <div className="flex aspect-video w-full flex-col items-center justify-center gap-2 rounded-tile bg-sunken text-center">
          <CameraIcon className="size-6 text-faint" aria-hidden />
          <p className="max-w-xs px-4 text-xs text-muted">
            {cameraMessage(scanner.status, scanner.errorMessage, subject)}
          </p>
        </div>
      )}
      {scanner.status === "active" ? (
        <Button variant="secondary" className="min-h-12" onClick={scanner.stop}>
          Turn camera off
        </Button>
      ) : scanner.status === "requesting" ? null : (
        <Button
          variant="secondary"
          className="min-h-12"
          onClick={scanner.start}
          leadingIcon={<CameraIcon className="size-4" />}
        >
          Scan with camera
        </Button>
      )}
    </div>
  );
}
