import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { matchRoutes, MemoryRouter, Route, Routes } from "react-router-dom";

import * as analytics from "@/api/analytics";
import * as client from "@/api/client";
import { CAMPAIGN_SENT } from "@/api/fixtures";
import { areaRoutes, toRouteObjects } from "../appRoutes";
import { shellRoutes } from "../shellRoutes";
import { routes } from "./index";
import { SectionTabs } from "./SectionTabs";
import type { Section } from "./SectionTabs";
import { CAMPAIGN, EMAIL, HUB, WHATSAPP } from "./testData";

/**
 * Every link and tab a dashboard renders leads to a screen the router serves. The registry is the
 * one the app mounts (`areaRoutes` and `shellRoutes`, without the catch-all 404), so a target that
 * only lands on the NotFound page counts as a miss.
 */

const REGISTERED = [...toRouteObjects(areaRoutes), ...shellRoutes].filter((route) => route.path !== "*");

function internalTargets(container: HTMLElement): string[] {
  return [...container.querySelectorAll("a[href]")]
    .map((anchor) => anchor.getAttribute("href") ?? "")
    .filter((href) => href.startsWith("/"))
    .map((href) => new URL(href, "http://localhost").pathname);
}

function unregistered(targets: string[]): string[] {
  return targets.filter((path) => matchRoutes(REGISTERED, path) === null);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the registry the check runs against", () => {
  it("serves the inbox at /inbox and nothing at the paths the dashboards used to link to", () => {
    expect(unregistered(["/inbox", "/whatsapp", "/email/campaigns", "/campaigns/c-1"])).toEqual([]);
    expect(unregistered(["/whatsapp/inbox", "/email/inbox"])).toEqual(["/whatsapp/inbox", "/email/inbox"]);
  });
});

describe("section tabs", () => {
  it.each<Section>(["hub", "whatsapp", "email"])("every %s tab leads to a registered screen", (section) => {
    const { container } = render(
      <MemoryRouter>
        <SectionTabs section={section} />
      </MemoryRouter>,
    );
    const targets = internalTargets(container);
    expect(targets.length).toBeGreaterThan(0);
    expect(unregistered(targets)).toEqual([]);
  });

  it("points WhatsApp Conversations at the inbox and offers no Email inbox tab", () => {
    const whatsapp = render(
      <MemoryRouter>
        <SectionTabs section="whatsapp" />
      </MemoryRouter>,
    );
    expect(screen.getByRole("link", { name: "Conversations" }).getAttribute("href")).toBe("/inbox");
    whatsapp.unmount();

    render(
      <MemoryRouter>
        <SectionTabs section="email" />
      </MemoryRouter>,
    );
    expect(screen.queryByRole("link", { name: "Inbox" })).toBeNull();
    expect(screen.getByRole("link", { name: "Campaigns" })).toBeTruthy();
  });
});

describe("dashboard screens", () => {
  const DISCONNECTED = {
    ...HUB,
    channels: HUB.channels.map((channel) => ({ ...channel, connected: false, detail: null })),
  };

  it.each([
    ["/", "Communication"],
    ["/whatsapp", "WhatsApp"],
    ["/whatsapp/analytics", "WhatsApp analytics"],
    ["/email", "Email"],
    ["/email/campaigns/c-1/analytics", CAMPAIGN_SENT.name],
  ])("%s links only to registered screens", async (url, heading) => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(HUB);
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL);
    vi.spyOn(analytics, "getCampaignAnalytics").mockResolvedValue(CAMPAIGN);
    vi.spyOn(client, "getCampaign").mockResolvedValue(CAMPAIGN_SENT);

    const { container } = render(
      <MemoryRouter initialEntries={[url]}>
        <Routes>
          {routes.map((route) => (
            <Route key={route.path} path={route.path} element={route.element} />
          ))}
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("heading", { level: 1, name: heading });
    await waitFor(() => {
      expect(screen.getAllByRole("status").some((el) => /loaded$/.test(el.textContent ?? ""))).toBe(true);
    });

    const targets = internalTargets(container);
    expect(targets.length).toBeGreaterThan(0);
    expect(unregistered(targets)).toEqual([]);
  });

  it("the hub's Connect links lead somewhere too", async () => {
    vi.spyOn(analytics, "getHubOverview").mockResolvedValue(DISCONNECTED);

    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <Routes>
          {routes.map((route) => (
            <Route key={route.path} path={route.path} element={route.element} />
          ))}
        </Routes>
      </MemoryRouter>,
    );
    await screen.findByRole("link", { name: /Connect WhatsApp/ });

    const targets = internalTargets(container);
    expect(targets).toContain("/whatsapp/connect");
    expect(unregistered(targets)).toEqual([]);
  });

  it("the WhatsApp feed's View all opens the inbox; the Email feed has no such link", async () => {
    vi.spyOn(analytics, "getWhatsAppOverview").mockResolvedValue(WHATSAPP);
    vi.spyOn(analytics, "getEmailOverview").mockResolvedValue(EMAIL);

    for (const [url, heading] of [
      ["/whatsapp", "WhatsApp"],
      ["/email", "Email"],
    ] as const) {
      const view = render(
        <MemoryRouter initialEntries={[url]}>
          <Routes>
            {routes.map((route) => (
              <Route key={route.path} path={route.path} element={route.element} />
            ))}
          </Routes>
        </MemoryRouter>,
      );
      await screen.findByRole("heading", { level: 1, name: heading });
      if (url === "/whatsapp") {
        expect((await screen.findByRole("link", { name: "View all" })).getAttribute("href")).toBe("/inbox");
      } else {
        await screen.findByRole("heading", { level: 2, name: "Recent activity" });
        expect(screen.queryByRole("link", { name: "View all" })).toBeNull();
      }
      view.unmount();
    }
  });
});
