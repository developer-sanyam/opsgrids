import { useCallback, useEffect, useRef, useState } from "react";
import type { RefObject } from "react";

/**
 * Camera-based barcode scanning, ADR-0024 §7's own choice: `BarcodeDetector` where the browser
 * has it, manual entry everywhere else — never a second scanning library. There is no barcode or
 * QR dependency anywhere in this monorepo today (every product app's `package.json` under
 * `frontend/` was checked before writing this), and `BarcodeDetector` is a zero-dependency
 * browser API that covers Chrome, Edge and Android WebView — the devices a warehouse Android
 * handset actually runs. Safari/iOS lacks it; those callers fall straight to `"unsupported"` and
 * the manual-entry field the scan screen always renders regardless of this hook's status (never a
 * dead end, mission brief).
 */

export type CameraScanStatus =
  | "idle"
  | "unsupported"
  | "requesting"
  | "denied"
  | "no-camera"
  | "active"
  | "error";

export interface BarcodeScannerControls {
  videoRef: RefObject<HTMLVideoElement | null>;
  status: CameraScanStatus;
  errorMessage: string | null;
  start: () => void;
  stop: () => void;
}

/** How long after one detection before the same (or a different) code can fire again — long
 *  enough that a barcode still in frame does not fire a second scan the moment the first is
 *  handled, short enough that a worker moving to the next line is not kept waiting. */
const COOLDOWN_MS = 1500;

declare global {
  interface Window {
    BarcodeDetector?: new (options?: { formats?: string[] }) => {
      detect: (source: HTMLVideoElement) => Promise<{ rawValue: string }[]>;
    };
  }
}

export function useBarcodeScanner(onDetected: (value: string) => void): BarcodeScannerControls {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const rafRef = useRef<number | null>(null);
  const lastFireRef = useRef(0);
  const onDetectedRef = useRef(onDetected);
  onDetectedRef.current = onDetected;

  const [status, setStatus] = useState<CameraScanStatus>("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  const stop = useCallback(() => {
    if (rafRef.current !== null) {
      cancelAnimationFrame(rafRef.current);
      rafRef.current = null;
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    setStatus((current) => (current === "active" ? "idle" : current));
  }, []);

  const start = useCallback(() => {
    if (typeof window === "undefined" || !window.BarcodeDetector) {
      setStatus("unsupported");
      return;
    }
    if (typeof navigator === "undefined" || !navigator.mediaDevices?.getUserMedia) {
      setStatus("unsupported");
      return;
    }

    setStatus("requesting");
    setErrorMessage(null);

    navigator.mediaDevices
      .getUserMedia({ video: { facingMode: "environment" } })
      .then((stream) => {
        streamRef.current = stream;
        const video = videoRef.current;
        if (!video) {
          stream.getTracks().forEach((track) => track.stop());
          return;
        }
        video.srcObject = stream;
        void video.play();
        setStatus("active");

        const BarcodeDetectorCtor = window.BarcodeDetector;
        if (!BarcodeDetectorCtor) {
          setStatus("unsupported");
          return;
        }
        const detector = new BarcodeDetectorCtor();

        const tick = () => {
          if (!videoRef.current) return;
          detector
            .detect(videoRef.current)
            .then((codes) => {
              const now = Date.now();
              const first = codes[0];
              if (first && now - lastFireRef.current > COOLDOWN_MS) {
                lastFireRef.current = now;
                onDetectedRef.current(first.rawValue);
              }
            })
            .catch(() => {
              // A single failed frame is not a scan failure — try the next one. Only
              // `getUserMedia` rejecting (caught below) is a real "camera unavailable".
            })
            .finally(() => {
              rafRef.current = requestAnimationFrame(tick);
            });
        };
        rafRef.current = requestAnimationFrame(tick);
      })
      .catch((err: unknown) => {
        const name = err instanceof DOMException ? err.name : "";
        if (name === "NotAllowedError" || name === "SecurityError") {
          setStatus("denied");
        } else if (name === "NotFoundError" || name === "OverconstrainedError") {
          setStatus("no-camera");
        } else {
          setStatus("error");
          setErrorMessage(err instanceof Error ? err.message : "Couldn't start the camera.");
        }
      });
  }, []);

  // Always release the camera on unmount — a picker who navigates away mid-scan must not leave
  // the light on and the sensor running in the background.
  useEffect(() => stop, [stop]);

  return { videoRef, status, errorMessage, start, stop };
}
