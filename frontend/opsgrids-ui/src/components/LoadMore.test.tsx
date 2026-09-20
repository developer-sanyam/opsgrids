/**
 * "Load more" is the one control on a list that changes the page without moving the user, so the
 * announcement is the whole feature: without it a screen-reader user presses a button and is told
 * nothing happened.
 */

import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Announcer } from "./Announcer";
import { LoadMore } from "./LoadMore";

function Harness({ total = 312, pageSize = 25 }: { total?: number; pageSize?: number }) {
  const [loaded, setLoaded] = useState(25);
  return (
    <Announcer>
      <LoadMore
        loaded={loaded}
        total={total}
        noun="requests"
        onLoadMore={() => setLoaded((n) => Math.min(n + pageSize, total))}
      />
    </Announcer>
  );
}

describe("LoadMore", () => {
  it("says how many are left, not how many are loaded", () => {
    render(<Harness />);
    expect(screen.getByRole("button", { name: "Load more · 287 left" })).toBeDefined();
  });

  it("announces what it appended and where that leaves the user", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Load more · 287 left" }));
    await waitFor(() =>
      expect(screen.getByText("Loaded 25 more. Showing 50 of 312 requests.")).toBeDefined(),
    );
  });

  it("stays announceable on a second press, which identical live text would swallow", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    await user.click(screen.getByRole("button", { name: "Load more · 287 left" }));
    await waitFor(() =>
      expect(screen.getByText("Loaded 25 more. Showing 50 of 312 requests.")).toBeDefined(),
    );
    await user.click(screen.getByRole("button", { name: "Load more · 262 left" }));
    await waitFor(() =>
      expect(screen.getByText("Loaded 25 more. Showing 75 of 312 requests.")).toBeDefined(),
    );
  });

  it("lets the approver know they reached the end of the queue", () => {
    render(
      <Announcer>
        <LoadMore loaded={3} total={3} noun="requests" onLoadMore={vi.fn()} />
      </Announcer>,
    );
    expect(screen.getByText("Showing all 3 requests")).toBeDefined();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("moves focus to the closing line when the last page replaces the button", async () => {
    const user = userEvent.setup();
    render(<Harness total={30} />);
    await user.click(screen.getByRole("button", { name: "Load more · 5 left" }));
    await waitFor(() =>
      expect(document.activeElement?.textContent).toBe("Showing all 30 requests"),
    );
  });

  it("says nothing when a filter change resets the list rather than appending to it", async () => {
    const { rerender } = render(
      <Announcer>
        <LoadMore loaded={25} total={312} noun="requests" onLoadMore={vi.fn()} />
      </Announcer>,
    );
    rerender(
      <Announcer>
        <LoadMore loaded={50} total={312} noun="requests" onLoadMore={vi.fn()} />
      </Announcer>,
    );
    expect(screen.queryByText(/Loaded 25 more/)).toBeNull();
  });
});
