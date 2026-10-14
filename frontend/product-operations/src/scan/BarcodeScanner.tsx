import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";

import { useMediaQuery } from "@opsgrids/ui";
import { Button, InlineAlert, TextField } from "@/ui";
import { createDetector } from "./detector";
import type { FrameDetector } from "./detector";

/** How the camera is doing. Every state except `live` leaves manual entry fully working. */
type CameraState =
  | { kind: "idle" }
  | { kind: "starting" }
  | { kind: "live"; note: string }
  | { kind: "denied" }
  | { kind: "no-camera" }
  | { kind: "insecure" }
  | { kind: "error"; message: string };

const FRAME_INTERVAL_MS = 180;

/** A phone or tablet held in the hand: the camera is the way in, and the keyboard is the exception. */
const TOUCH_QUERY = "(pointer: coarse)";

interface BarcodeScannerProps {
  /** A code was read (camera) or entered (typed / hardware scanner). Already trimmed. */
  onScan: (text: string) => void;
  /** A lookup is in flight: the controls stop accepting another. */
  busy?: boolean;
}

function describeFailure(error: unknown): CameraState {
  const name = error instanceof DOMException || error instanceof Error ? error.name : "";
  if (name === "NotAllowedError" || name === "SecurityError") return { kind: "denied" };
  if (name === "NotFoundError" || name === "OverconstrainedError") return { kind: "no-camera" };
  return {
    kind: "error",
    message: error instanceof Error && error.message ? error.message : "The camera did not start.",
  };
}

/**
 * Scan a label, or type what is printed under it.
 *
 * Manual entry is **always** on the screen — a handheld scanner that types the code and presses
 * Enter lands in it, and a person with a phone whose camera is blocked, or a browser that cannot
 * open one, loses nothing but the camera button. With a mouse and keyboard it comes first, with
 * focus. On a touch device the camera is the primary action and typing sits under it, without
 * focus: raising the keyboard on arrival would cover the very thing the person came to point at a
 * label. The camera is still opt-in: asking for permission on page load, before anyone said they
 * wanted to scan, is how a prompt gets dismissed and never returns.
 */
export function BarcodeScanner({ onScan, busy = false }: BarcodeScannerProps) {
  const [typed, setTyped] = useState("");
  const [camera, setCamera] = useState<CameraState>({ kind: "idle" });
  const videoRef = useRef<HTMLVideoElement>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const runRef = useRef(0);
  const fieldRef = useRef<HTMLInputElement>(null);
  const touch = useMediaQuery(TOUCH_QUERY, false);

  const stopCamera = useCallback(() => {
    runRef.current += 1; // invalidates any loop still waiting on a frame
    if (timerRef.current) clearTimeout(timerRef.current);
    timerRef.current = null;
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  useEffect(() => stopCamera, [stopCamera]);
  // Focus the field on arrival (a handheld scanner types into it) without the `autoFocus`
  // attribute, which moves focus on re-render too.
  useEffect(() => {
    if (!touch) fieldRef.current?.focus();
    // Focus is for arrival only; a later change of pointer type must not steal it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startCamera = useCallback(async () => {
    if (!window.isSecureContext) {
      setCamera({ kind: "insecure" });
      return;
    }
    const devices = navigator.mediaDevices as MediaDevices | undefined;
    if (!devices?.getUserMedia) {
      setCamera({ kind: "no-camera" });
      return;
    }
    stopCamera();
    const run = runRef.current;
    setCamera({ kind: "starting" });
    let detector: FrameDetector;
    let stream: MediaStream;
    try {
      stream = await devices.getUserMedia({
        video: { facingMode: { ideal: "environment" } },
        audio: false,
      });
    } catch (error) {
      if (run === runRef.current) setCamera(describeFailure(error));
      return;
    }
    if (run !== runRef.current) {
      stream.getTracks().forEach((t) => t.stop());
      return;
    }
    streamRef.current = stream;
    try {
      detector = await createDetector();
    } catch {
      stopCamera();
      setCamera({
        kind: "error",
        message: "This browser cannot read bar codes from the camera. Type the code instead.",
      });
      return;
    }
    if (run !== runRef.current) return;
    const video = videoRef.current;
    if (!video) return;
    video.srcObject = stream;
    try {
      await video.play();
    } catch {
      // Autoplay refusal on a muted inline video is not a thing; if it happens the frame
      // loop simply never sees `readyState`, and the manual field is still there.
    }
    setCamera({ kind: "live", note: detector.note });

    const tick = async (): Promise<void> => {
      if (run !== runRef.current) return;
      try {
        if (video.readyState >= 2) {
          const found = await detector.detect(video);
          if (run !== runRef.current) return;
          if (found) {
            stopCamera();
            setCamera({ kind: "idle" });
            onScan(found.trim());
            return;
          }
        }
      } catch {
        // A frame that cannot be decoded is a frame with nothing in it.
      }
      timerRef.current = setTimeout(() => void tick(), FRAME_INTERVAL_MS);
    };
    void tick();
  }, [onScan, stopCamera]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const value = typed.trim();
    if (value === "" || busy) return;
    onScan(value);
    setTyped("");
  };

  const live = camera.kind === "live" || camera.kind === "starting";

  const typeForm = (
    <form onSubmit={submit} className="flex flex-col gap-3" aria-label="Enter a code">
      <TextField
        label="Type or scan a code"
        value={typed}
        onChange={(e) => setTyped(e.target.value)}
        placeholder="og:roll:R-0001 or R-0001"
        ref={fieldRef}
        autoComplete="off"
        autoCapitalize="off"
        spellCheck={false}
        enterKeyHint="go"
        className="min-h-14 text-lg"
        helper="A handheld scanner types here and presses Enter."
      />
      <Button
        type="submit"
        variant={touch ? "secondary" : "primary"}
        className="min-h-14 w-full text-base"
        loading={busy}
        disabled={typed.trim() === ""}
      >
        Look up
      </Button>
    </form>
  );

  const where = touch ? "below" : "above";

  const cameraControls = (
    <div className="flex flex-col gap-3">
      {live ? (
        <>
          <div className="overflow-hidden rounded-tile border border-line bg-ink">
            <video
              ref={videoRef}
              className="aspect-video w-full object-cover"
              playsInline
              muted
              aria-label="Camera view"
            />
          </div>
          {camera.kind === "live" ? <p className="text-sm text-muted">{camera.note}</p> : null}
          <Button
            type="button"
            variant="secondary"
            className="min-h-14 w-full text-base"
            onClick={() => {
              stopCamera();
              setCamera({ kind: "idle" });
            }}
          >
            Stop camera
          </Button>
        </>
      ) : (
        <Button
          type="button"
          variant={touch ? "primary" : "secondary"}
          className="min-h-14 w-full text-base"
          onClick={() => void startCamera()}
        >
          Use camera
        </Button>
      )}
      {/* The video element must exist before `srcObject` is set, so it is also mounted while
          idle-but-starting; this hidden one keeps the ref valid for the first start. */}
      {!live ? <video ref={videoRef} className="hidden" playsInline muted aria-hidden /> : null}

      {camera.kind === "denied" ? (
        <InlineAlert tone="warning" title="The camera is blocked">
          Allow camera access for this site in your browser settings, then try again — or type
          the code {where}.
        </InlineAlert>
      ) : null}
      {camera.kind === "no-camera" ? (
        <InlineAlert tone="warning" title="No camera available">
          This device has no camera this browser can open. Type the code {where}, or use a handheld
          scanner.
        </InlineAlert>
      ) : null}
      {camera.kind === "insecure" ? (
        <InlineAlert tone="warning" title="The camera needs a secure connection">
          Open this page over https to use the camera. You can still type the code {where}.
        </InlineAlert>
      ) : null}
      {camera.kind === "error" ? (
        <InlineAlert tone="danger" title="The camera did not start">
          {camera.message}
        </InlineAlert>
      ) : null}
    </div>
  );

  return (
    <div className="flex flex-col gap-4">
      {touch ? cameraControls : typeForm}
      {touch ? typeForm : cameraControls}
    </div>
  );
}
