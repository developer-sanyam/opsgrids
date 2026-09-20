/**
 * The field that a browser-zone-naive implementation gets wrong without ever looking wrong: an
 * approver in one office sets a delegation to start at 9:00 am, and the instant that reaches the
 * server is 9:00 am somewhere else. These tests fix a zone that is not the test runner's, so a
 * regression to `new Date(value)` fails here rather than in a customer's timezone.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

import { DateRangeField } from "./DateRangeField";
import { DateTimeField } from "./DateTimeField";

function describedText(element: Element): string {
  const ids = (element.getAttribute("aria-describedby") ?? "").split(" ").filter(Boolean);
  return ids.map((id) => document.getElementById(id)?.textContent ?? "").join(" ");
}

describe("DateTimeField", () => {
  it("shows the instant as the wall clock of the zone it was given", () => {
    render(
      <DateTimeField
        label="Starts"
        value="2026-09-20T03:30:00.000Z"
        onChange={vi.fn()}
        zone="Asia/Kolkata"
      />,
    );
    expect((screen.getByLabelText("Starts") as HTMLInputElement).value).toBe("2026-09-20T09:00");
  });

  it("converts a typed wall clock using that zone, never the browser's", () => {
    const onChange = vi.fn();
    render(
      <DateTimeField label="Starts" value={null} onChange={onChange} zone="Asia/Kolkata" />,
    );
    fireEvent.change(screen.getByLabelText("Starts"), { target: { value: "2026-09-20T09:00" } });
    expect(onChange).toHaveBeenCalledWith("2026-09-20T03:30:00.000Z");
  });

  it("reports null when the field is cleared", () => {
    const onChange = vi.fn();
    render(
      <DateTimeField
        label="Ends"
        value="2026-09-20T03:30:00.000Z"
        onChange={onChange}
        zone="Asia/Kolkata"
      />,
    );
    fireEvent.change(screen.getByLabelText("Ends"), { target: { value: "" } });
    expect(onChange).toHaveBeenCalledWith(null);
  });

  it("is empty, not broken, before anything is chosen", () => {
    render(<DateTimeField label="Starts" value={null} onChange={vi.fn()} zone="Asia/Kolkata" />);
    expect((screen.getByLabelText("Starts") as HTMLInputElement).value).toBe("");
  });
});

describe("DateRangeField", () => {
  function Harness() {
    const [start, setStart] = useState<string | null>("2026-09-20T03:30:00.000Z");
    const [end, setEnd] = useState<string | null>(null);
    return (
      <DateRangeField
        legend="When it runs"
        start={start}
        end={end}
        onStartChange={setStart}
        onEndChange={setEnd}
        zone="Asia/Kolkata"
        zoneNote="Times are in Bengaluru time (IST)."
        endError="End must be after the start."
      />
    );
  }

  it("groups the pair as one question", () => {
    render(<Harness />);
    expect(screen.getByRole("group", { name: "When it runs" })).toBeDefined();
  });

  it("reads the timezone note with both fields, not once after them", () => {
    render(<Harness />);
    expect(describedText(screen.getByLabelText("Starts"))).toContain(
      "Times are in Bengaluru time (IST).",
    );
    expect(describedText(screen.getByLabelText("Ends"))).toContain(
      "Times are in Bengaluru time (IST).",
    );
  });

  it("keeps the timezone note when a field is in error", () => {
    render(<Harness />);
    const ends = screen.getByLabelText("Ends");
    expect(describedText(ends)).toContain("End must be after the start.");
    expect(describedText(ends)).toContain("Times are in Bengaluru time (IST).");
  });

  it("floors the end field at the start, so the range cannot invert in the picker", () => {
    render(<Harness />);
    expect(screen.getByLabelText("Ends").getAttribute("min")).toBe("2026-09-20T09:00");
  });
});
