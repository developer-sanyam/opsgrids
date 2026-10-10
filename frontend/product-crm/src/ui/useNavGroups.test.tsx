import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("@/api/client", () => ({
  listDeals: () => Promise.resolve({ items: [], total: 12, page: 1, size: 1, pages: 12 }),
}));

import { useNavGroups } from "./useNavGroups";

/**
 * The CRM's navigation is seven entries that each lead to a screen that exists: the placeholder
 * routes (Reports, Integrations, Settings, Help) are gone, and nothing is pinned to the rail's
 * foot. The Deals count is the real total from the deals list.
 */

function NavProbe() {
  const { groups, pinned } = useNavGroups();
  const entries = groups.flatMap((group) => group.items);
  return (
    <div>
      <ul aria-label="entries">
        {entries.map((entry) => (
          <li key={entry.to}>
            {entry.to}|{entry.label}|{entry.count ?? ""}
          </li>
        ))}
      </ul>
      <p data-testid="pinned">{pinned.length}</p>
      <p data-testid="groups">{groups.map((group) => group.title).join(",")}</p>
    </div>
  );
}

describe("useNavGroups", () => {
  it("lists the seven screens that exist, in three groups, and pins nothing", async () => {
    render(<NavProbe />);
    expect(screen.getByTestId("groups").textContent).toBe("Pipeline,Records,Workspace");
    expect(screen.getByTestId("pinned").textContent).toBe("0");
    const items = screen.getAllByRole("listitem").map((li) => li.textContent?.split("|")[0]);
    expect(items).toEqual(["/", "/leads", "/deals", "/activities", "/companies", "/contacts", "/team"]);
  });

  it("shows the real number of deals beside Deals once it has loaded, and no other count", async () => {
    render(<NavProbe />);
    await waitFor(() => expect(screen.getByText("/deals|Deals|12")).toBeTruthy());
    expect(screen.getByText("/activities|Activities|")).toBeTruthy();
  });
});
