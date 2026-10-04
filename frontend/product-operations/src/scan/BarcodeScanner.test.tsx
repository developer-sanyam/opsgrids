/**
 * The scan fallbacks: manual entry always works; a blocked, missing or insecure camera says why
 * and leaves it working; and a camera read — by the native detector or the JavaScript one — hands
 * the text to `onScan` exactly once and releases the camera.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const createDetector = vi.fn();
vi.mock("./detector", () => ({ createDetector: () => createDetector() }));

import { BarcodeScanner } from "./BarcodeScanner";

function stream() {
  const stop = vi.fn();
  return { stop, value: { getTracks: () => [{ stop }] } as unknown as MediaStream };
}

function stubCamera(getUserMedia: unknown) {
  Object.defineProperty(navigator, "mediaDevices", {
    configurable: true,
    value: getUserMedia === undefined ? undefined : { getUserMedia },
  });
}

beforeEach(() => {
  Object.defineProperty(window, "isSecureContext", { configurable: true, value: true });
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  // jsdom never reaches HAVE_CURRENT_DATA; the scanner only decodes frames that are ready.
  vi.spyOn(HTMLMediaElement.prototype, "readyState", "get").mockReturnValue(4);
});

afterEach(() => {
  createDetector.mockReset();
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: undefined });
});

describe("manual entry", () => {
  it("is on screen first and submits a typed code, trimmed, on Enter", async () => {
    const onScan = vi.fn();
    const user = userEvent.setup();
    render(<BarcodeScanner onScan={onScan} />);
    const field = screen.getByLabelText("Type or scan a code");
    expect(document.activeElement).toBe(field);
    await user.type(field, "  og:roll:R-1  {Enter}");
    expect(onScan).toHaveBeenCalledExactlyOnceWith("og:roll:R-1");
    expect((field as HTMLInputElement).value).toBe("");
  });

  it("does not submit an empty code, or a second code while a lookup is running", async () => {
    const onScan = vi.fn();
    const user = userEvent.setup();
    const { rerender } = render(<BarcodeScanner onScan={onScan} />);
    expect((screen.getByRole("button", { name: "Look up" }) as HTMLButtonElement).disabled).toBe(true);
    await user.type(screen.getByLabelText("Type or scan a code"), "X1");
    rerender(<BarcodeScanner onScan={onScan} busy />);
    await user.keyboard("{Enter}");
    expect(onScan).not.toHaveBeenCalled();
  });

  it("makes every control a large touch target", () => {
    render(<BarcodeScanner onScan={vi.fn()} />);
    for (const name of ["Look up", "Use camera"]) {
      expect(screen.getByRole("button", { name }).className).toMatch(/min-h-14/);
    }
  });
});

describe("camera fallbacks", () => {
  it("explains a blocked camera and leaves manual entry working", async () => {
    stubCamera(vi.fn().mockRejectedValue(new DOMException("no", "NotAllowedError")));
    const onScan = vi.fn();
    const user = userEvent.setup();
    render(<BarcodeScanner onScan={onScan} />);
    await user.click(screen.getByRole("button", { name: "Use camera" }));
    expect(await screen.findByText("The camera is blocked")).toBeTruthy();
    expect(screen.getByText(/Allow camera access for this site/)).toBeTruthy();
    await user.type(screen.getByLabelText("Type or scan a code"), "BIN-07{Enter}");
    expect(onScan).toHaveBeenCalledWith("BIN-07");
    // and the button to try again is still there
    expect(screen.getByRole("button", { name: "Use camera" })).toBeTruthy();
  });

  it("says there is no camera when the browser has no media devices at all", async () => {
    stubCamera(undefined);
    const user = userEvent.setup();
    render(<BarcodeScanner onScan={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Use camera" }));
    expect(await screen.findByText("No camera available")).toBeTruthy();
  });

  it("says there is no camera when the device has none", async () => {
    stubCamera(vi.fn().mockRejectedValue(new DOMException("none", "NotFoundError")));
    const user = userEvent.setup();
    render(<BarcodeScanner onScan={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Use camera" }));
    expect(await screen.findByText("No camera available")).toBeTruthy();
  });

  it("asks for https instead of failing opaquely on an insecure origin", async () => {
    Object.defineProperty(window, "isSecureContext", { configurable: true, value: false });
    const getUserMedia = vi.fn();
    stubCamera(getUserMedia);
    const user = userEvent.setup();
    render(<BarcodeScanner onScan={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Use camera" }));
    expect(await screen.findByText("The camera needs a secure connection")).toBeTruthy();
    expect(getUserMedia).not.toHaveBeenCalled();
  });

  it("releases the camera and explains when no decoder works in this browser", async () => {
    const s = stream();
    stubCamera(vi.fn().mockResolvedValue(s.value));
    createDetector.mockRejectedValue(new Error("nothing"));
    const user = userEvent.setup();
    render(<BarcodeScanner onScan={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Use camera" }));
    expect(await screen.findByText("The camera did not start")).toBeTruthy();
    expect(s.stop).toHaveBeenCalled();
  });
});

describe("a camera read", () => {
  async function scanWith(detector: { kind: "native" | "js"; note: string; detect: () => Promise<string | null> }) {
    const s = stream();
    stubCamera(vi.fn().mockResolvedValue(s.value));
    createDetector.mockResolvedValue(detector);
    const onScan = vi.fn();
    const user = userEvent.setup();
    render(<BarcodeScanner onScan={onScan} />);
    await user.click(screen.getByRole("button", { name: "Use camera" }));
    return { s, onScan };
  }

  it("hands a native read to onScan once and stops the camera", async () => {
    const detect = vi.fn().mockResolvedValueOnce(null).mockResolvedValue("  og:lot:LOT-A ");
    const { s, onScan } = await scanWith({ kind: "native", note: "Reads QR codes and Code 128 bar codes.", detect });
    expect(await screen.findByText("Reads QR codes and Code 128 bar codes.")).toBeTruthy();
    await waitFor(() => expect(onScan).toHaveBeenCalledExactlyOnceWith("og:lot:LOT-A"), { timeout: 2000 });
    expect(s.stop).toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Use camera" })).toBeTruthy();
  });

  it("works the same through the JavaScript fallback, and says it reads QR only", async () => {
    const detect = vi.fn().mockResolvedValueOnce(null).mockResolvedValue("og:roll:R-9");
    const { onScan } = await scanWith({
      kind: "js",
      note: "This browser reads QR codes only — point it at the QR on the label, or type the Code 128 number.",
      detect,
    });
    expect(await screen.findByText(/reads QR codes only/)).toBeTruthy();
    await waitFor(() => expect(onScan).toHaveBeenCalledExactlyOnceWith("og:roll:R-9"), { timeout: 2000 });
  });

  it("keeps looking through frames that fail to decode", async () => {
    const detect = vi
      .fn()
      .mockRejectedValueOnce(new Error("bad frame"))
      .mockResolvedValue("BIN-07");
    const { onScan } = await scanWith({ kind: "js", note: "n", detect });
    await waitFor(() => expect(onScan).toHaveBeenCalledWith("BIN-07"), { timeout: 2000 });
  });

  it("stops the camera on request and when the screen unmounts", async () => {
    const detect = vi.fn().mockResolvedValue(null);
    const s = stream();
    stubCamera(vi.fn().mockResolvedValue(s.value));
    createDetector.mockResolvedValue({ kind: "native", note: "n", detect });
    const user = userEvent.setup();
    const { unmount } = render(<BarcodeScanner onScan={vi.fn()} />);
    await user.click(screen.getByRole("button", { name: "Use camera" }));
    await user.click(await screen.findByRole("button", { name: "Stop camera" }));
    expect(s.stop).toHaveBeenCalledTimes(1);

    await user.click(screen.getByRole("button", { name: "Use camera" }));
    await screen.findByRole("button", { name: "Stop camera" });
    unmount();
    expect(s.stop).toHaveBeenCalledTimes(2);
  });
});
