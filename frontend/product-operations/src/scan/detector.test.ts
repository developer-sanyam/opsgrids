/**
 * Which decoder a browser gets: the native `BarcodeDetector` when it exists (QR and Code 128), the
 * JavaScript QR decoder when it does not — and what each one says about its limits.
 */
import { afterEach, describe, expect, it, vi } from "vitest";

const jsQR = vi.fn();
vi.mock("jsqr", () => ({ default: (...args: unknown[]) => jsQR(...args) }));

import { createDetector, hasNativeDetector } from "./detector";

function video(width = 640, height = 480): HTMLVideoElement {
  return { videoWidth: width, videoHeight: height } as HTMLVideoElement;
}

afterEach(() => {
  vi.unstubAllGlobals();
  jsQR.mockReset();
});

describe("createDetector", () => {
  it("uses the native detector for QR and Code 128 when the browser has one", async () => {
    const detect = vi.fn().mockResolvedValue([{ rawValue: "og:roll:R-1" }]);
    class Native {
      static getSupportedFormats = () => Promise.resolve(["qr_code", "code_128", "ean_13"]);
      constructor(public options: { formats: string[] }) {
        nativeOptions.push(options);
      }
      detect = detect;
    }
    const nativeOptions: { formats: string[] }[] = [];
    vi.stubGlobal("BarcodeDetector", Native);

    expect(hasNativeDetector()).toBe(true);
    const detector = await createDetector();
    expect(detector.kind).toBe("native");
    expect(nativeOptions[0]?.formats).toEqual(["qr_code", "code_128"]);
    expect(detector.note).toMatch(/Code 128/);
    expect(await detector.detect(video())).toBe("og:roll:R-1");
  });

  it("says so when the native detector reads only QR on this device", async () => {
    class Native {
      static getSupportedFormats = () => Promise.resolve(["qr_code"]);
      detect = () => Promise.resolve([]);
    }
    vi.stubGlobal("BarcodeDetector", Native);
    const detector = await createDetector();
    expect(detector.kind).toBe("native");
    expect(detector.note).toMatch(/QR codes only/);
    expect(await detector.detect(video())).toBeNull();
  });

  it("falls back to the JavaScript QR decoder when there is no native detector", async () => {
    vi.stubGlobal("BarcodeDetector", undefined);
    expect(hasNativeDetector()).toBe(false);
    const context = {
      drawImage: vi.fn(),
      getImageData: vi.fn(() => ({ data: new Uint8ClampedArray(4), width: 640, height: 480 })),
    };
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
      context as unknown as CanvasRenderingContext2D,
    );
    jsQR.mockReturnValueOnce({ data: "og:location:BIN-07" }).mockReturnValueOnce(null);

    const detector = await createDetector();
    expect(detector.kind).toBe("js");
    expect(detector.note).toMatch(/QR codes only/);
    expect(await detector.detect(video())).toBe("og:location:BIN-07");
    expect(await detector.detect(video())).toBeNull();
    expect(context.drawImage).toHaveBeenCalledTimes(2);
  });

  it("falls back to JavaScript when the native detector will not construct", async () => {
    class Broken {
      constructor() {
        throw new Error("denied");
      }
    }
    vi.stubGlobal("BarcodeDetector", Broken);
    const detector = await createDetector();
    expect(detector.kind).toBe("js");
  });

  it("the JavaScript decoder reports nothing for a video that has no frame yet", async () => {
    vi.stubGlobal("BarcodeDetector", undefined);
    const detector = await createDetector();
    expect(await detector.detect(video(0, 0))).toBeNull();
    expect(jsQR).not.toHaveBeenCalled();
  });
});
