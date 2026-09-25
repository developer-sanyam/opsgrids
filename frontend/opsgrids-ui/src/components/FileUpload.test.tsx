/**
 * An upload field is a small state machine per file — checked, sending, stored, failed — and
 * every state has to be reachable and readable without a mouse. The transport is the caller's;
 * these tests stand in for it with a promise they settle by hand.
 */

import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { FileUpload } from "./FileUpload";
import type { UploadControls } from "./FileUpload";

interface Pending {
  file: File;
  controls: UploadControls;
  resolve: (value: string) => void;
  reject: (error: Error) => void;
}

/** A transport the test drives: every call waits until the test resolves or rejects it. */
function manualTransport() {
  const calls: Pending[] = [];
  const onUpload = vi.fn(
    (file: File, controls: UploadControls) =>
      new Promise<string>((resolve, reject) => {
        calls.push({ file, controls, resolve, reject });
      }),
  );
  return { calls, onUpload };
}

function png(name = "bag.png", size = 1024): File {
  const file = new File(["x"], name, { type: "image/png" });
  Object.defineProperty(file, "size", { value: size });
  return file;
}

function fileInput(): HTMLInputElement {
  const input = document.querySelector<HTMLInputElement>("input[type=file]");
  if (input === null) throw new Error("no file input");
  return input;
}

const MB = 1024 * 1024;

describe("FileUpload", () => {
  it("is a button named by its label and prompt, described by the rule", () => {
    render(
      <FileUpload label="Product images" accept={["image/png", "image/jpeg"]} maxSize={5 * MB} maxFiles={6} onUpload={vi.fn()} />,
    );
    const zone = screen.getByRole("button", { name: /Product images Choose files/ });
    const hint = document.getElementById(zone.getAttribute("aria-describedby") ?? "");
    expect(hint?.textContent).toBe("PNG or JPG, up to 5 MB each, 6 files at most");
  });

  it("opens the picker from the keyboard", async () => {
    const user = userEvent.setup();
    render(<FileUpload label="Logo" onUpload={vi.fn()} />);
    const click = vi.spyOn(fileInput(), "click");
    await user.tab();
    await user.keyboard("{Enter}");
    expect(click).toHaveBeenCalled();
  });

  it("uploads a chosen file, shows its progress, and reports the stored value", async () => {
    const user = userEvent.setup();
    const { calls, onUpload } = manualTransport();
    const onChange = vi.fn();
    render(<FileUpload label="Logo" accept={["image/png"]} onUpload={onUpload} onChange={onChange} />);

    await user.upload(fileInput(), png());
    expect(onUpload).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("progressbar", { name: "Uploading bag.png" }).hasAttribute("aria-valuenow")).toBe(false);

    act(() => calls[0]?.controls.onProgress(0.4));
    expect(screen.getByRole("progressbar", { name: "Uploading bag.png" }).getAttribute("aria-valuenow")).toBe("40");
    expect(screen.getByText("1 KB · Uploading… 40%")).toBeTruthy();

    await act(async () => calls[0]?.resolve("asset-1"));
    expect(screen.queryByRole("progressbar")).toBeNull();
    expect(screen.getByText("1 KB · Uploaded")).toBeTruthy();
    expect(onChange).toHaveBeenLastCalledWith(["asset-1"]);
    expect(screen.getByText("bag.png uploaded.")).toBeTruthy();
  });

  it("refuses a file of the wrong kind or size without sending it, and says why", () => {
    const { onUpload } = manualTransport();
    render(<FileUpload label="Photos" accept={["image/png"]} maxSize={5 * MB} maxFiles={3} onUpload={onUpload} />);
    const pdf = new File(["x"], "spec.pdf", { type: "application/pdf" });
    fireEvent.change(fileInput(), { target: { files: [pdf, png("huge.png", 7 * MB)] } });
    expect(onUpload).not.toHaveBeenCalled();
    expect(screen.getAllByText("spec.pdf isn't a PNG file.").length).toBeGreaterThan(0);
    expect(screen.getAllByText("huge.png is 7 MB; the limit is 5 MB.").length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Dismiss spec.pdf" })).toBeTruthy();
  });

  it("refuses files past the most it takes", () => {
    const { onUpload } = manualTransport();
    render(<FileUpload label="Photos" maxFiles={2} onUpload={onUpload} />);
    fireEvent.change(fileInput(), { target: { files: [png("a.png"), png("b.png"), png("c.png")] } });
    expect(onUpload).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("c.png wasn't added: 2 files is the most this field takes.").length).toBeGreaterThan(0);
  });

  it("shows the server's sentence when an upload fails, and tries again on Retry", async () => {
    const user = userEvent.setup();
    const { calls, onUpload } = manualTransport();
    render(<FileUpload label="Logo" onUpload={onUpload} />);
    await user.upload(fileInput(), png());
    await act(async () => calls[0]?.reject(new Error("Storage is full for this organization.")));
    expect(screen.getAllByText("Storage is full for this organization.").length).toBeGreaterThan(0);

    await user.click(screen.getByRole("button", { name: "Retry" }));
    expect(onUpload).toHaveBeenCalledTimes(2);
    await act(async () => calls[1]?.resolve("asset-2"));
    expect(screen.getByText("1 KB · Uploaded")).toBeTruthy();
  });

  it("cancels an upload that is removed, and puts focus back on the drop zone", async () => {
    const user = userEvent.setup();
    const { calls, onUpload } = manualTransport();
    render(<FileUpload label="Logo" onUpload={onUpload} />);
    await user.upload(fileInput(), png());
    await user.click(screen.getByRole("button", { name: "Remove bag.png" }));
    expect(calls[0]?.controls.signal.aborted).toBe(true);
    expect(screen.queryByText("bag.png")).toBeNull();
    expect(document.activeElement).toBe(screen.getByRole("button", { name: /Logo/ }));
  });

  it("replaces the one file of a single-file field", async () => {
    const user = userEvent.setup();
    const { calls, onUpload } = manualTransport();
    const onChange = vi.fn();
    render(<FileUpload label="Logo" onUpload={onUpload} onChange={onChange} />);
    await user.upload(fileInput(), png("old.png"));
    await act(async () => calls[0]?.resolve("old"));
    await user.upload(fileInput(), png("new.png"));
    expect(screen.queryByText("old.png")).toBeNull();
    await act(async () => calls[1]?.resolve("new"));
    expect(onChange).toHaveBeenLastCalledWith(["new"]);
  });

  it("shows files stored before the form opened without reporting them as a change", () => {
    const onChange = vi.fn();
    render(
      <FileUpload
        label="Documents"
        maxFiles={3}
        onUpload={vi.fn()}
        onChange={onChange}
        initialFiles={[{ name: "specifications.pdf", size: 250_880, value: "doc-1" }]}
      />,
    );
    expect(screen.getByText("specifications.pdf")).toBeTruthy();
    expect(screen.getByText("245 KB · Uploaded")).toBeTruthy();
    expect(onChange).not.toHaveBeenCalled();
  });

  it("takes a dropped file the same way as a chosen one", async () => {
    const { onUpload } = manualTransport();
    render(<FileUpload label="Logo" onUpload={onUpload} />);
    const zone = screen.getByRole("button", { name: /Logo/ });
    fireEvent.dragEnter(zone, { dataTransfer: { files: [] } });
    expect(zone.hasAttribute("data-dragging")).toBe(true);
    fireEvent.drop(zone, { dataTransfer: { files: [png("dropped.png")] } });
    await waitFor(() => expect(onUpload).toHaveBeenCalledTimes(1));
    expect(zone.hasAttribute("data-dragging")).toBe(false);
  });

  it("carries a field error to the drop zone", () => {
    render(<FileUpload label="Logo" required error="Add a logo before publishing." onUpload={vi.fn()} />);
    const zone = screen.getByRole("button", { name: /Logo/ });
    expect(zone.getAttribute("aria-invalid")).toBe("true");
    // A button cannot carry `aria-required`; "Required." is read as part of its description.
    const description = (zone.getAttribute("aria-describedby") ?? "")
      .split(" ")
      .map((id) => document.getElementById(id)?.textContent)
      .join(" ");
    expect(description).toContain("Required.");
    expect(description).toContain("Add a logo before publishing.");
  });
});
