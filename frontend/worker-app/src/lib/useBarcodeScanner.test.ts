/**
 * jsdom has neither `BarcodeDetector` nor `navigator.mediaDevices.getUserMedia` — which is the
 * same starting point as a real browser without either (Safari/iOS, or any browser with no
 * camera). This is the "no dead end" reliability case: the hook must degrade to `"unsupported"`
 * rather than throw or hang, which is what lets the scan screen's manual-entry field stay usable.
 */
import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { useBarcodeScanner } from "./useBarcodeScanner";

describe("useBarcodeScanner", () => {
  it("reports unsupported when the browser has neither BarcodeDetector nor getUserMedia", () => {
    const { result } = renderHook(() => useBarcodeScanner(vi.fn()));

    expect(result.current.status).toBe("idle");
    act(() => {
      result.current.start();
    });

    expect(result.current.status).toBe("unsupported");
    expect(result.current.errorMessage).toBeNull();
  });

  it("stop() is always safe to call, even before start()", () => {
    const { result } = renderHook(() => useBarcodeScanner(vi.fn()));

    expect(() => {
      act(() => {
        result.current.stop();
      });
    }).not.toThrow();
  });
});
