/**
 * Paging is where a list quietly starts lying: the count line says one thing, the rows say
 * another, and a keyboard user who pressed Next is given no sign the table underneath them was
 * replaced rather than extended.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { Announcer } from "./Announcer";
import { Pagination } from "./Pagination";

function renderPaged(props: Partial<React.ComponentProps<typeof Pagination>> = {}) {
  const onPageChange = vi.fn();
  render(
    <Announcer>
      <Pagination
        page={1}
        pages={13}
        total={312}
        size={25}
        onPageChange={onPageChange}
        noun="widgets"
        {...props}
      />
    </Announcer>,
  );
  return { onPageChange };
}

describe("Pagination", () => {
  it("says which rows of how many are on screen", () => {
    renderPaged({ page: 3 });
    expect(screen.getByText("Showing 51–75 of 312 widgets")).toBeTruthy();
  });

  it("does not claim a full page at the end of the list", () => {
    renderPaged({ page: 13, total: 312 });
    expect(screen.getByText("Showing 301–312 of 312 widgets")).toBeTruthy();
  });

  it("says so plainly when a filter matched nothing", () => {
    renderPaged({ page: 1, pages: 0, total: 0 });
    expect(screen.getByText("No widgets")).toBeTruthy();
    expect(screen.queryByRole("navigation")).toBeNull();
  });

  it("marks the current page for a screen reader, not only with a colour", () => {
    renderPaged({ page: 3 });
    const current = screen.getByRole("button", { name: "Page 3" });
    expect(current.getAttribute("aria-current")).toBe("page");
    expect(screen.getByRole("button", { name: "Page 4" }).getAttribute("aria-current")).toBeNull();
  });

  it("keeps both ends of a long list one click away", () => {
    renderPaged({ page: 7 });
    expect(screen.getByRole("button", { name: "Page 1" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Page 13" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Page 4" })).toBeNull();
  });

  it("disables Previous on the first page and Next on the last", () => {
    const { unmount } = render(
      <Pagination page={1} pages={13} total={312} size={25} onPageChange={vi.fn()} noun="widgets" />,
    );
    expect(screen.getByRole("button", { name: "Previous page" })).toHaveProperty("disabled", true);
    expect(screen.getByRole("button", { name: "Next page" })).toHaveProperty("disabled", false);
    unmount();

    render(
      <Pagination page={13} pages={13} total={312} size={25} onPageChange={vi.fn()} noun="widgets" />,
    );
    expect(screen.getByRole("button", { name: "Next page" })).toHaveProperty("disabled", true);
  });

  it("asks for the page that was clicked", async () => {
    const user = userEvent.setup();
    const { onPageChange } = renderPaged({ page: 5 });

    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(onPageChange).toHaveBeenCalledWith(6);
    await user.click(screen.getByRole("button", { name: "Page 1" }));
    expect(onPageChange).toHaveBeenCalledWith(1);
  });

  it("announces the new page, because the table was replaced and nothing else says so", () => {
    const { rerender } = render(
      <Announcer>
        <Pagination page={1} pages={13} total={312} size={25} onPageChange={vi.fn()} noun="widgets" />
      </Announcer>,
    );
    rerender(
      <Announcer>
        <Pagination page={2} pages={13} total={312} size={25} onPageChange={vi.fn()} noun="widgets" />
      </Announcer>,
    );

    const spoken = Array.from(document.querySelectorAll('[aria-live="polite"]'))
      .map((node) => node.textContent ?? "")
      .join("");
    expect(spoken).toContain("Page 2 of 13");
    expect(spoken).toContain("Showing 26–50 of 312 widgets");
  });

  it("offers a page size only when the caller can change one", async () => {
    const user = userEvent.setup();
    const onSizeChange = vi.fn();
    const { unmount } = render(
      <Pagination page={1} pages={13} total={312} size={25} onPageChange={vi.fn()} noun="widgets" />,
    );
    expect(screen.queryByLabelText("Per page")).toBeNull();
    unmount();

    render(
      <Pagination
        page={1}
        pages={13}
        total={312}
        size={25}
        onPageChange={vi.fn()}
        onSizeChange={onSizeChange}
        noun="widgets"
      />,
    );
    await user.selectOptions(screen.getByLabelText("Per page"), "50");
    expect(onSizeChange).toHaveBeenCalledWith(50);
  });
});
