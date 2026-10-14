/**
 * The header's alignment contract: `actions` is one row of controls, and a caption about them
 * (the period a picker resolved to) goes in `meta`, with the title — never stacked inside
 * `actions`, where it made one control's cell taller than the button beside it.
 */

import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";

import { Button } from "./Button";
import { PageHeader } from "./PageHeader";

describe("PageHeader", () => {
  it("renders meta with the title, outside the actions row", () => {
    render(
      <PageHeader
        title="Email"
        meta={<span role="status">Showing 15 Sep – 14 Oct</span>}
        actions={<Button>Create campaign</Button>}
      />,
    );
    const actions = screen.getByRole("button", { name: "Create campaign" }).parentElement;
    const meta = screen.getByRole("status");
    expect(actions?.contains(meta)).toBe(false);
    expect(screen.getByRole("heading", { level: 1 }).parentElement?.contains(meta)).toBe(true);
  });

  it("centres a row of actions on one line and lets it wrap", () => {
    render(<PageHeader title="Deals" actions={<Button>New deal</Button>} />);
    const row = screen.getByRole("button", { name: "New deal" }).parentElement;
    expect(row?.className).toContain("items-center");
    expect(row?.className).toContain("flex-wrap");
  });
});
