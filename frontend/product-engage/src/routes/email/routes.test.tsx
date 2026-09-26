import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createMemoryRouter, RouterProvider } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import * as email from "@/api/email";
import { routes } from "./index";

/**
 * The route list the orchestrator wires: the paths the contract names, each behind the read
 * permission of the API it reads, with a denial rather than an empty screen for a member without it.
 */

afterEach(() => {
  vi.restoreAllMocks();
});

function mount(path: string, grants: string[]) {
  const router = createMemoryRouter(
    routes.map(({ path: routePath, element }) => ({ path: routePath, element })),
    { initialEntries: [path] },
  );
  return render(
    <PermissionProvider grants={grants}>
      <RouterProvider router={router} />
    </PermissionProvider>,
  );
}

describe("the email routes", () => {
  it("lists the contract's paths once each, every one titled", () => {
    const paths = routes.map((r) => r.path);
    expect(paths).toEqual([
      "/email/templates",
      "/email/templates/new",
      "/email/templates/:id",
      "/email/campaigns",
      "/email/settings",
    ]);
    expect(new Set(paths).size).toBe(paths.length);
    for (const route of routes) expect(route.title).not.toBe("");
  });

  it("puts only the list screens in navigation", () => {
    const labelled = routes.filter((r) => r.nav !== undefined).map((r) => r.path);
    expect(labelled).toEqual(["/email/templates", "/email/campaigns", "/email/settings"]);
    for (const route of routes) {
      if (route.nav !== undefined) expect(route.nav.group).toBe("Email");
    }
  });

  it("renders the template list for a member who may read templates", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue({
      items: [],
      total: 0,
      page: 1,
      size: 200,
      pages: 0,
    });
    mount("/email/templates", ["notification.delivery.template.read:ORG"]);
    expect(await screen.findByText("No email templates yet")).toBeTruthy();
  });

  it("shows the denial, and reads nothing, for a member who may not", async () => {
    const read = vi.spyOn(email, "listEmailTemplates");
    mount("/email/templates", ["engage.campaign.campaign.read:ORG"]);
    expect(await screen.findByText("You can't open email templates right now")).toBeTruthy();
    expect(read).not.toHaveBeenCalled();
  });

  it("gates the editor route the same way", async () => {
    const read = vi.spyOn(email, "listEmailTemplates");
    mount("/email/templates/new", []);
    expect(await screen.findByText("You can't open email templates right now")).toBeTruthy();
    expect(read).not.toHaveBeenCalled();
  });
});
