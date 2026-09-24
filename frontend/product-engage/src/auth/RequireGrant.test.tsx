import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { PermissionProvider } from "@opsgrids/client";

import { RequireGrant } from "./RequireGrant";

/**
 * The route guard: a screen behind a read permission renders the denial, not the screen, for a
 * caller known not to hold it. Presentation only — the endpoints behind every screen refuse on
 * their own — so what is pinned is that the explanation is honest about both ways to be here.
 */

function renderGuarded(grants: readonly string[]) {
  return render(
    <PermissionProvider grants={grants}>
      <RequireGrant permission="engage.campaign.campaign.read" what="campaigns">
        <p>The campaigns screen</p>
      </RequireGrant>
    </PermissionProvider>,
  );
}

describe("RequireGrant", () => {
  it("renders the screen for a caller who holds the permission", () => {
    renderGuarded(["engage.campaign.campaign.read:ORG"]);
    expect(screen.getByText("The campaigns screen")).toBeTruthy();
    expect(screen.queryByText(/You can't open campaigns/)).toBeNull();
  });

  it("renders the denial, and not the screen, for a caller who does not", () => {
    renderGuarded(["engage.audience.list.read:ORG"]);
    expect(screen.queryByText("The campaigns screen")).toBeNull();
    expect(screen.getByText("You can't open campaigns right now")).toBeTruthy();
    // It stands in for a whole page, so it carries the page's one h1 — a screen-reader user's
    // heading navigation has somewhere to land.
    expect(screen.getAllByRole("heading", { level: 1 }).map((h) => h.textContent)).toEqual(["Campaigns"]);
    // Honest about both causes: a role gap, or a check that could not be made.
    expect(screen.getByText(/your role doesn't include seeing campaigns, or your access couldn't be checked/)).toBeTruthy();
  });

  it("does not grant a write to a caller who holds only the wrong scope of a read", () => {
    renderGuarded(["engage.campaign.campaign.read:OWN"]);
    // Every Engage grant is floored at the organization; an OWN read is refused, not narrowed.
    expect(screen.queryByText("The campaigns screen")).toBeNull();
  });

  it("renders nothing at all while the grants are still loading, so the denial never flashes", () => {
    const { container } = render(
      <PermissionProvider>
        <RequireGrant permission="engage.campaign.campaign.read" what="campaigns">
          <p>The campaigns screen</p>
        </RequireGrant>
      </PermissionProvider>,
    );
    expect(container.textContent).toBe("");
  });
});
