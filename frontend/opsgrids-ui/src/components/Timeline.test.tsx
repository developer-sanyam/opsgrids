/**
 * The history is the evidence an approver decides against, so its order is in the markup and its
 * collapse never hides the recent end. Expanding moves focus to what appeared — a button that
 * reveals content and leaves focus behind is a dead end for anyone not looking at the screen.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Timeline } from "./Timeline";
import type { TimelineEntry } from "./Timeline";

const entries: TimelineEntry[] = Array.from({ length: 8 }, (_, i) => ({
  id: String(i),
  content: `Event ${i}`,
  timestamp: "15 Sep, 6:40 pm IST",
  dateTime: "2026-09-15T13:10:00.000Z",
}));

describe("Timeline", () => {
  it("is an ordered list, so the order survives without the styling", () => {
    render(<Timeline entries={entries.slice(0, 3)} />);
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });

  it("carries the UTC instant in the attribute and the location's zone in the text", () => {
    render(<Timeline entries={entries.slice(0, 1)} />);
    const time = screen.getByText("15 Sep, 6:40 pm IST");
    expect(time.getAttribute("datetime")).toBe("2026-09-15T13:10:00.000Z");
  });

  it("keeps the most recent entries visible when it collapses", () => {
    render(<Timeline entries={entries} collapseAfter={6} />);
    expect(screen.queryByText("Event 0")).toBeNull();
    expect(screen.getByText("Event 7")).toBeDefined();
    expect(screen.getByRole("button", { name: "Show all 8 events" })).toBeDefined();
  });

  it("expands in place and moves focus to the first entry that appeared", async () => {
    const user = userEvent.setup();
    render(<Timeline entries={entries} collapseAfter={6} />);
    await user.click(screen.getByRole("button", { name: "Show all 8 events" }));
    expect(screen.getByText("Event 0")).toBeDefined();
    expect(document.activeElement?.textContent).toContain("Event 0");
  });

  it("does not collapse a history that fits", () => {
    render(<Timeline entries={entries.slice(0, 4)} collapseAfter={6} />);
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("marks the disclosure button collapsed, and expanded once revealed", async () => {
    const user = userEvent.setup();
    render(<Timeline entries={entries} collapseAfter={6} />);
    const toggle = screen.getByRole("button", { name: "Show all 8 events" });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
    await user.click(toggle);
    // The button reveals everything and then leaves the page — there is nothing left to collapse.
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("treats collapseAfter={0} as not collapsing, rather than hiding the whole history", () => {
    render(<Timeline entries={entries} collapseAfter={0} />);
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.getByText("Event 0")).toBeDefined();
    expect(screen.getByText("Event 7")).toBeDefined();
  });

  it("says nothing has happened rather than rendering a blank card", () => {
    render(<Timeline entries={[]} />);
    expect(screen.getByText("Nothing has happened yet.")).toBeDefined();
  });

  it("quotes a decider's comment verbatim, beneath the sentence", () => {
    render(
      <Timeline
        entries={[
          {
            id: "1",
            content: "Priya Nair rejected this.",
            tone: "negative",
            quote: "You already took this as leave.",
          },
        ]}
      />,
    );
    expect(screen.getByText("You already took this as leave.")).toBeDefined();
  });

  it("desc_order_collapse_keeps_the_head: newest first keeps the first entries and reveals the rest below", async () => {
    const user = userEvent.setup();
    const newestFirst = [...entries].reverse();
    render(<Timeline entries={newestFirst} order="desc" collapseAfter={2} />);

    expect(screen.getByText("Event 7")).toBeDefined();
    expect(screen.getByText("Event 6")).toBeDefined();
    expect(screen.queryByText("Event 5")).toBeNull();
    const button = screen.getByRole("button", { name: "Show all 8 events" });
    // The control sits after the list, at the end that is hidden.
    expect(screen.getByRole("list").compareDocumentPosition(button) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();

    await user.click(button);
    expect(screen.getByText("Event 0")).toBeDefined();
    expect(document.activeElement?.textContent).toContain("Event 5");
  });

  it("renders a footer under the entries, for a Load more", () => {
    render(<Timeline entries={entries.slice(0, 2)} footer={<button type="button">Load more</button>} />);
    const footer = screen.getByRole("button", { name: "Load more" });
    expect(screen.getByRole("list").compareDocumentPosition(footer) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });
});
