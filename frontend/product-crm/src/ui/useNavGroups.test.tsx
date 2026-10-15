import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const listDeals = vi.hoisted(() => vi.fn());

vi.mock("@/api/client", () => ({
  listDeals: (...args: unknown[]) => listDeals(...args),
}));

import { announceDealsChanged } from "@/data/dealsChanged";
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

function renderProbe() {
  return render(
    <MemoryRouter>
      <NavProbe />
    </MemoryRouter>,
  );
}

function totalOf(total: number) {
  return Promise.resolve({ items: [], total, page: 1, size: 1, pages: total });
}

describe("useNavGroups", () => {
  beforeEach(() => {
    listDeals.mockReset();
    listDeals.mockImplementation(() => totalOf(12));
  });

  it("lists the seven screens that exist, in three groups, and pins nothing", async () => {
    renderProbe();
    expect(screen.getByTestId("groups").textContent).toBe("Pipeline,Records,Workspace");
    expect(screen.getByTestId("pinned").textContent).toBe("0");
    const items = screen.getAllByRole("listitem").map((li) => li.textContent?.split("|")[0]);
    expect(items).toEqual(["/", "/leads", "/deals", "/activities", "/companies", "/contacts", "/team"]);
  });

  it("shows the real number of deals beside Deals once it has loaded, and no other count", async () => {
    renderProbe();
    await waitFor(() => expect(screen.getByText("/deals|Deals|12")).toBeTruthy());
    expect(screen.getByText("/activities|Activities|")).toBeTruthy();
  });

  it("asks the server again when a screen says the number of deals changed", async () => {
    renderProbe();
    await waitFor(() => expect(screen.getByText("/deals|Deals|12")).toBeTruthy());

    listDeals.mockImplementation(() => totalOf(13));
    act(() => announceDealsChanged());

    await waitFor(() => expect(screen.getByText("/deals|Deals|13")).toBeTruthy());
  });
});
