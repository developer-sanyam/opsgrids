import { useState } from "react";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ScanInput } from "./ScanInput";

function Harness({ onSubmit = () => undefined }: { onSubmit?: () => void }) {
  const [value, setValue] = useState("");
  return <ScanInput label="Bin label" value={value} onChange={setValue} onSubmit={onSubmit} />;
}

describe("ScanInput", () => {
  it("does not raise the on-screen keyboard by default", () => {
    render(<Harness />);
    expect(screen.getByLabelText("Bin label").getAttribute("inputmode")).toBe("none");
  });

  it("Type instead brings the keyboard back, and Use scanner puts it away again", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const field = screen.getByLabelText("Bin label");

    await user.click(screen.getByRole("button", { name: "Type instead" }));
    expect(field.getAttribute("inputmode")).toBe("text");
    expect(document.activeElement).toBe(field);

    await user.click(screen.getByRole("button", { name: "Use scanner" }));
    expect(field.getAttribute("inputmode")).toBe("none");
  });

  it("still takes what a keyboard-wedge scanner types, and submits on its Enter", async () => {
    const user = userEvent.setup();
    const onSubmit = vi.fn();
    render(<Harness onSubmit={onSubmit} />);

    await user.click(screen.getByLabelText("Bin label"));
    await user.keyboard("A-01-03{Enter}");

    expect((screen.getByLabelText("Bin label") as HTMLInputElement).value).toBe("A-01-03");
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("keeps the toggle a 48px target", () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: "Type instead" }).className).toContain("min-h-12");
  });
});
