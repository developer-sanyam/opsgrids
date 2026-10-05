/**
 * The bin-label step. The camera is `useBarcodeScanner`'s job (`lib/useBarcodeScanner.test.ts`); here
 * it is replaced by a stand-in that "detects" whatever the test says, so what is pinned is what this
 * step does with a label however it arrives: trimmed, validated, accepted once, and never typed over
 * by a camera that is no longer needed.
 */
import { createRef, useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import type { BarcodeScannerControls, CameraScanStatus } from "@/lib/useBarcodeScanner";

vi.mock("@/lib/useBarcodeScanner", () => ({ useBarcodeScanner: vi.fn() }));

import { useBarcodeScanner } from "@/lib/useBarcodeScanner";

import { PutawayBinScan } from "./PutawayBinScan";

const hook = vi.mocked(useBarcodeScanner);

/** A camera that, once started, reads `detected` — or sits in `status` if it can't start. */
function camera({ status = "idle", detected }: { status?: CameraScanStatus; detected?: string } = {}) {
  const stop = vi.fn();
  hook.mockImplementation((onDetected): BarcodeScannerControls => ({
    videoRef: createRef<HTMLVideoElement>(),
    status,
    errorMessage: null,
    start: () => {
      if (detected !== undefined) onDetected(detected);
    },
    stop,
  }));
  return { stop };
}

describe("PutawayBinScan", () => {
  it("a label read by the camera arrives trimmed of the CR/LF a barcode payload can carry", async () => {
    camera({ detected: "A-01-03\r\n" });
    const onAccept = vi.fn();
    render(<PutawayBinScan label={null} onAccept={onAccept} onClear={() => undefined} />);

    await userEvent.click(screen.getByRole("button", { name: "Scan with camera" }));

    expect(onAccept).toHaveBeenCalledWith("A-01-03");
  });

  it("a camera read that could never be a bin label is refused with a reason, not accepted", async () => {
    camera({ detected: "https://example.com/not a bin" });
    const onAccept = vi.fn();
    render(<PutawayBinScan label={null} onAccept={onAccept} onClear={() => undefined} />);

    await userEvent.click(screen.getByRole("button", { name: "Scan with camera" }));

    expect(onAccept).not.toHaveBeenCalled();
    expect(await screen.findByText("That doesn't look like a bin label. Check it and try again.")).toBeTruthy();
  });

  it("a hardware scanner typing into the field, Enter and all, is accepted the same way", async () => {
    camera();
    const onAccept = vi.fn();
    render(<PutawayBinScan label={null} onAccept={onAccept} onClear={() => undefined} />);

    await userEvent.type(screen.getByLabelText("Bin label"), "A-01-03{Enter}");

    expect(onAccept).toHaveBeenCalledWith("A-01-03");
  });

  it("Use this label is off until something has been typed, and refuses a label with a space in it", async () => {
    camera();
    const onAccept = vi.fn();
    render(<PutawayBinScan label={null} onAccept={onAccept} onClear={() => undefined} />);
    const use = screen.getByRole("button", { name: "Use this label" }) as HTMLButtonElement;
    expect(use.disabled).toBe(true);

    await userEvent.type(screen.getByLabelText("Bin label"), "A 01");
    expect(use.disabled).toBe(false);
    await userEvent.click(use);

    expect(onAccept).not.toHaveBeenCalled();
    expect(await screen.findByText(/doesn't look like a bin label/)).toBeTruthy();
  });

  it.each<[CameraScanStatus, RegExp]>([
    ["unsupported", /can't scan with the camera/],
    ["denied", /Camera access was denied/],
    ["no-camera", /No camera was found/],
  ])("%s: says so and points at the field, which is always there", (status, message) => {
    camera({ status });
    render(<PutawayBinScan label={null} onAccept={() => undefined} onClear={() => undefined} />);

    expect(screen.getByText(message)).toBeTruthy();
    expect(screen.getByLabelText("Bin label")).toBeTruthy();
  });

  describe("a label that is being replaced", () => {
    /** The parent owns the accepted label, as the Put away screen does. */
    function Screen() {
      const [label, setLabel] = useState<string | null>(null);
      return <PutawayBinScan label={label} onAccept={setLabel} onClear={() => setLabel(null)} />;
    }

    it("is selected when the field comes back, so the next scan replaces it rather than landing on the end of it", async () => {
      camera();
      const user = userEvent.setup();
      render(<Screen />);
      await user.type(screen.getByLabelText("Bin label"), "WRONG{Enter}");

      await user.click(screen.getByRole("button", { name: "Change bin label" }));

      const field = screen.getByLabelText("Bin label") as HTMLInputElement;
      expect(document.activeElement).toBe(field);
      expect(field.value).toBe("WRONG");
      expect([field.selectionStart, field.selectionEnd]).toEqual([0, 5]);

      // What a keyboard-wedge scanner does: type the label it read at the caret, then Enter.
      await user.keyboard("RIGHT{Enter}");
      expect(screen.getByText("RIGHT")).toBeTruthy();
    });

    it("can still be corrected by hand: the text is there to edit, not cleared", async () => {
      camera();
      const user = userEvent.setup();
      render(<Screen />);
      await user.type(screen.getByLabelText("Bin label"), "A-01-0O{Enter}");
      await user.click(screen.getByRole("button", { name: "Change bin label" }));

      const field = screen.getByLabelText("Bin label") as HTMLInputElement;
      expect(field.value).toBe("A-01-0O");
    });
  });

  it("an accepted label replaces the camera and the field, and can be changed", async () => {
    camera();
    const onClear = vi.fn();
    render(<PutawayBinScan label="A-01-03" onAccept={() => undefined} onClear={onClear} />);

    expect(screen.getByText("A-01-03")).toBeTruthy();
    expect(screen.queryByLabelText("Bin label")).toBeNull();
    // The camera lives only while a label is being looked for, so its stream is released.
    expect(hook).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("button", { name: "Change bin label" }));
    expect(onClear).toHaveBeenCalledTimes(1);
  });
});
