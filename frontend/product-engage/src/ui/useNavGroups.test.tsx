import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";

import { useNavGroups } from "./useNavGroups";

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

function NavProbe() {
  const { groups } = useNavGroups();
  return (
    <nav>
      {groups.map((group) => (
        <div key={group.title}>
          <p>{group.title}</p>
          {group.items.map((item) => (
            <a key={item.to} href={item.to}>
              {item.label}
            </a>
          ))}
        </div>
      ))}
    </nav>
  );
}

function renderNav(grants: readonly string[]) {
  return render(
    <AuthContext value={AUTH}>
      <PermissionProvider grants={grants}>
        <MemoryRouter>
          <NavProbe />
        </MemoryRouter>
      </PermissionProvider>
    </AuthContext>,
  );
}

describe("useNavGroups", () => {
  it("lists Lists and Campaigns for a member who can read them", () => {
    renderNav(["engage.audience.list.read:ORG", "engage.campaign.campaign.read:ORG"]);
    expect(screen.getByRole("link", { name: "Lists" }).getAttribute("href")).toBe("/lists");
    expect(screen.getByRole("link", { name: "Campaigns" }).getAttribute("href")).toBe("/campaigns");
  });

  it("hides each from a member known not to hold its read permission, and only that one", () => {
    renderNav(["engage.audience.list.read:ORG"]);
    expect(screen.getByRole("link", { name: "Lists" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Campaigns" })).toBeNull();
  });

  it("drops the Campaigns group when its only entry is hidden, rather than leaving a bare heading", () => {
    renderNav([]);
    expect(screen.queryByRole("link", { name: "Campaigns" })).toBeNull();
    expect(screen.queryByText("Campaigns", { selector: "p, span, h2, h3, div" })).toBeNull();
  });

  it("offers the communication hub, the automations and the get-started checklist at their own paths", () => {
    renderNav([]);
    const hrefs: Record<string, string> = {
      Overview: "/",
      WhatsApp: "/whatsapp",
      Email: "/email",
      Automations: "/automations",
      "Get started": "/setup",
    };
    for (const [name, href] of Object.entries(hrefs)) {
      expect(screen.getByRole("link", { name }).getAttribute("href"), name).toBe(href);
    }
  });

  it("never hides the entries whose own screens explain a denial", () => {
    renderNav([]);
    for (const name of ["Overview", "Inbox", "Connections", "Domains", "Senders", "Contacts", "Suppressions"]) {
      expect(screen.getByRole("link", { name }), name).toBeTruthy();
    }
  });
});
