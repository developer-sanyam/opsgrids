import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DataTable } from "./DataTable";

const rows = [
  { id: "a", name: "Acme" },
  { id: "b", name: "Beta" },
];
const columns = [
  {
    key: "name",
    header: "Name",
    render: (r: { id: string; name: string }) => <a href={`#${r.id}`}>{r.name}</a>,
  },
];

/**
 * The whole-row click is a mouse convenience. Every caller that sets `onRowClick` (Accounts,
 * Subscriptions) renders a real link to the same place in the row, and that link is the keyboard
 * path. Turning the row into a `role="button"` as well would nest the link inside a button
 * (axe `nested-interactive`, 10 nodes on the accounts page) and give each row two tab stops.
 */
describe("DataTable rows with a row click", () => {
  it("reach each row's destination from the keyboard through the row's own link", async () => {
    const user = userEvent.setup();
    const onRowClick = vi.fn();
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} onRowClick={onRowClick} />);

    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "Acme" }));
    await user.tab();
    expect(document.activeElement).toBe(screen.getByRole("link", { name: "Beta" }));
  });

  it("never wrap that link in a second interactive control", () => {
    render(<DataTable columns={columns} rows={rows} rowKey={(r) => r.id} onRowClick={vi.fn()} />);
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    for (const row of screen.getAllByRole("row")) expect(row.hasAttribute("tabindex")).toBe(false);
  });
});
