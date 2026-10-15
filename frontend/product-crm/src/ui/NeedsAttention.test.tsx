import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/data/directoryContext", () => ({
  useDirectory: () => ({ companies: [], owners: [], contacts: [], reload: () => undefined }),
}));

import { NeedsAttention } from "./NeedsAttention";

function renderList(openDealCount: number) {
  return render(
    <MemoryRouter>
      <NeedsAttention items={[]} openDealCount={openDealCount} />
    </MemoryRouter>,
  );
}

describe("NeedsAttention with nothing to list", () => {
  it("invites the first deal instead of calling an empty pipeline healthy", () => {
    renderList(0);
    expect(screen.getByText("No open deals yet")).toBeTruthy();
    expect(screen.queryByText(/healthy/i)).toBeNull();
  });

  it("says nothing needs a nudge only when there are open deals to judge", () => {
    renderList(3);
    expect(screen.getByText("Nothing needs a nudge")).toBeTruthy();
    expect(screen.queryByText(/healthy/i)).toBeNull();
  });
});
