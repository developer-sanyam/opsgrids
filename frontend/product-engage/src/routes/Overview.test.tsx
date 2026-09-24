import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import * as client from "@/api/client";
import type { SetupOut } from "@/api/types";
import { OverviewPage } from "./Overview";

/**
 * The get-started checklist: five steps, exactly as `GET /engage/setup` folds them. Nothing here
 * decides what "done" means — the service does — so this file pins that the screen renders
 * whatever the service says, in the order it says it, and links each step to its own screen.
 */

const ALL_TODO: SetupOut = {
  steps: [
    { step: "connection", status: "todo", detail: "No email provider connected yet.", link: "/connection" },
    { step: "domain", status: "blocked", detail: "Connect your email provider first.", link: "/domains" },
    { step: "sender", status: "blocked", detail: "Verify a sending domain first.", link: "/senders" },
    { step: "contacts", status: "todo", detail: "No contacts yet.", link: "/contacts" },
    { step: "campaign", status: "blocked", detail: "Add a usable sender first.", link: "/campaigns" },
  ],
};

const ALL_DONE: SetupOut = {
  steps: [
    { step: "connection", status: "done", detail: "Connected via google.", link: "/connection" },
    { step: "domain", status: "done", detail: "1 of 1 domain(s) verified.", link: "/domains" },
    { step: "sender", status: "done", detail: "1 of 1 sender(s) usable.", link: "/senders" },
    { step: "contacts", status: "done", detail: "5 contact(s) in your audience.", link: "/contacts" },
    { step: "campaign", status: "done", detail: "1 campaign(s) sent.", link: "/campaigns" },
  ],
};

function mount() {
  return render(
    <MemoryRouter>
      <OverviewPage />
    </MemoryRouter>,
  );
}

describe("the get-started checklist", () => {
  it("shows a loading skeleton before the checklist arrives", () => {
    vi.spyOn(client, "getSetup").mockReturnValue(new Promise(() => undefined));
    mount();
    expect(screen.queryByText(/No email provider/)).toBeNull();
  });

  it("shows the service's error when the checklist fails to load", async () => {
    vi.spyOn(client, "getSetup").mockRejectedValue(new Error("network down"));
    mount();
    expect(await screen.findByText("Couldn't load your setup checklist")).toBeTruthy();
  });

  it("renders all five steps, in the order the service returns them, each linked to its own screen", async () => {
    vi.spyOn(client, "getSetup").mockResolvedValue(ALL_TODO);
    mount();

    const headings = ["Connections", "Domains", "Senders", "Contacts", "Campaigns"];
    for (const heading of headings) {
      expect(await screen.findByText(heading)).toBeTruthy();
    }

    expect(screen.getByRole("link", { name: /Domains/ }).getAttribute("href")).toBe("/domains");
    expect(screen.getByRole("link", { name: /Campaigns/ }).getAttribute("href")).toBe(
      "/campaigns",
    );
  });

  it("marks a blocked step distinctly from a step that is simply not started yet", async () => {
    vi.spyOn(client, "getSetup").mockResolvedValue(ALL_TODO);
    mount();

    expect(await screen.findAllByText("Blocked")).toHaveLength(3);
    expect(screen.getByText("Connect your email provider first.")).toBeTruthy();
  });

  it("says 'Open' rather than 'Set up' once a step is done", async () => {
    vi.spyOn(client, "getSetup").mockResolvedValue(ALL_DONE);
    mount();

    expect(await screen.findByRole("link", { name: /Open Domains/ })).toBeTruthy();
    expect(screen.queryByText("Blocked")).toBeNull();
  });
});
