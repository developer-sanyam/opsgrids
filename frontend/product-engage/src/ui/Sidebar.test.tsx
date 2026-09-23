import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";
import { ThemeProvider } from "@opsgrids/ui";

import { Sidebar } from "./Sidebar";

/**
 * The rail offers Lists and Campaigns to anyone who may read them, hides them from a member known
 * not to, and never hides the setup and audience entries whose own screens explain a denial. It is
 * a courtesy: the routes carry their own guard and the service refuses every read behind them.
 */

const AUTH: AuthContextValue = {
  status: "authenticated",
  user: null,
  login: () => Promise.reject(new Error("not used")),
  verifyMfa: () => Promise.reject(new Error("not used")),
  selectOrg: () => Promise.reject(new Error("not used")),
  logout: () => Promise.resolve(),
};

function renderRail(grants: readonly string[]) {
  return render(
    <ThemeProvider storageKey="test.theme">
      <AuthContext value={AUTH}>
        <PermissionProvider grants={grants}>
          <MemoryRouter>
            <Sidebar />
          </MemoryRouter>
        </PermissionProvider>
      </AuthContext>
    </ThemeProvider>,
  );
}

describe("navigation", () => {
  it("lists Lists and Campaigns for a member who can read them", () => {
    renderRail(["engage.audience.list.read:ORG", "engage.campaign.campaign.read:ORG"]);
    expect(screen.getByRole("link", { name: "Lists" }).getAttribute("href")).toBe("/lists");
    expect(screen.getByRole("link", { name: "Campaigns" }).getAttribute("href")).toBe("/campaigns");
  });

  it("hides each from a member known not to hold its read permission, and only that one", () => {
    renderRail(["engage.audience.list.read:ORG"]);
    expect(screen.getByRole("link", { name: "Lists" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Campaigns" })).toBeNull();
  });

  it("drops the Campaigns group when its only entry is hidden, rather than leaving a bare heading", () => {
    renderRail([]);
    expect(screen.queryByRole("link", { name: "Campaigns" })).toBeNull();
    expect(screen.queryByText("Campaigns", { selector: "p, span, h2, h3, div" })).toBeNull();
  });

  it("never hides the entries whose own screens explain a denial", () => {
    renderRail([]);
    for (const name of ["Overview", "Inbox", "Connections", "Domains", "Senders", "Contacts", "Suppressions"]) {
      expect(screen.getByRole("link", { name }), name).toBeTruthy();
    }
  });
});
