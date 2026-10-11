/**
 * Reading a bar code or QR from a camera frame (inventory Phase 9, ADR-0027).
 *
 * Two engines, one interface. The browser's own `BarcodeDetector` is used when it exists (Chrome
 * and Edge, Android included): it reads QR **and** Code 128 and costs no bundle. Where it does not
 * (Safari, Firefox) a JavaScript decoder (`jsqr`, loaded only then) reads **QR codes only** — the
 * label carries both for exactly this reason — and the screen says so, so nobody points a camera
 * at a Code 128 strip and waits.
 */

export interface FrameDetector {
  /** `"native"`: QR and Code 128. `"js"`: QR only. */
  kind: "native" | "js";
  /** Human sentence for the limits of this engine, shown under the camera. */
  note: string;
  /** The text of the first code in the frame, or `null`. Never throws for "nothing found". */
  detect(video: HTMLVideoElement): Promise<string | null>;
}

interface NativeBarcode {
  rawValue: string;
}
interface NativeDetector {
  detect(source: CanvasImageSource): Promise<NativeBarcode[]>;
}
interface NativeDetectorCtor {
  new (options?: { formats?: string[] }): NativeDetector;
  getSupportedFormats?: () => Promise<string[]>;
}

const WANTED_FORMATS = ["qr_code", "code_128"];

function nativeCtor(): NativeDetectorCtor | null {
  const ctor = (globalThis as { BarcodeDetector?: NativeDetectorCtor }).BarcodeDetector;
  return typeof ctor === "function" ? ctor : null;
}

/** Whether this browser can decode on its own (without the JS fallback). */
export function hasNativeDetector(): boolean {
  return nativeCtor() !== null;
}

async function nativeDetector(ctor: NativeDetectorCtor): Promise<FrameDetector> {
  let formats = WANTED_FORMATS;
  try {
    const supported = await ctor.getSupportedFormats?.();
    if (supported) formats = WANTED_FORMATS.filter((f) => supported.includes(f));
  } catch {
    // Keep the wanted list; the constructor refuses what it cannot do.
  }
  if (formats.length === 0) throw new Error("no supported formats");
  const detector = new ctor({ formats });
  const canCode128 = formats.includes("code_128");
  return {
    kind: "native",
    note: canCode128
      ? "Reads QR codes and Code 128 bar codes."
      : "Reads QR codes only on this device; type the Code 128 number if you need to.",
    async detect(video) {
      const found = await detector.detect(video);
      return found[0]?.rawValue ?? null;
    },
  };
}

async function jsDetector(): Promise<FrameDetector> {
  const { default: jsQR } = await import("jsqr");
  const canvas = document.createElement("canvas");
  return {
    kind: "js",
    note: "This browser reads QR codes only — point it at the QR on the label, or type the Code 128 number.",
    async detect(video) {
      const width = video.videoWidth;
      const height = video.videoHeight;
      if (!width || !height) return null;
      canvas.width = width;
      canvas.height = height;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) throw new Error("no canvas");
      context.drawImage(video, 0, 0, width, height);
      const image = context.getImageData(0, 0, width, height);
      return jsQR(image.data, width, height)?.data ?? null;
    },
  };
}

/** The best engine this browser supports. Rejects only if neither works (then: manual entry). */
export async function createDetector(): Promise<FrameDetector> {
  const ctor = nativeCtor();
  if (ctor) {
    try {
      return await nativeDetector(ctor);
    } catch {
      // A native detector that will not construct is no detector: fall through to JS.
    }
  }
  return jsDetector();
}
