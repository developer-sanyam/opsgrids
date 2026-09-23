import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT } from "@/api/fixtures";
import type { CampaignOut, Page } from "@/api/types";
import { CampaignPage } from "./CampaignEditor";

/**
 * Two things that happen after the screen is already up: a caller turns out not to be allowed to
 * change the draft they have started typing into, and — a fixed part of the page — the heading that
 * focus is sent to. And the one sentence in the preview that tells the truth about what it cannot
 * block.
 */

const DRAFT: CampaignOut = { ...CAMPAIGN_DRAFT, audience: "all_sendable", list_id: null };
const READ = "engage.campaign.campaign.read:ORG";
const WRITE = "engage.campaign.campaign.write:ORG";

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function mockReads(campaign: CampaignOut = DRAFT) {
  vi.spyOn(client, "getCampaign").mockResolvedValue(campaign);
  vi.spyOn(client, "listSenders").mockResolvedValue(page([]));
  vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
  vi.spyOn(client, "listLists").mockResolvedValue(page([]));
}

function tree(grants: readonly string[]) {
  return (
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[`/campaigns/${DRAFT.id}`]}>
        <Routes>
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>
  );
}

describe("a caller who turns out not to be allowed to edit, after starting to", () => {
  it("drops what they typed, and shows the draft as the service holds it", async () => {
    mockReads();
    const user = userEvent.setup();
    const view = render(tree([READ, WRITE]));

    const subject = (await screen.findByLabelText("Subject")) as HTMLInputElement;
    await user.type(subject, " — typed before the grants resolved");
    expect(subject.value).toContain("typed before");
    expect(screen.getByText("You have unsaved changes.")).toBeTruthy();

    // The grants resolve, and they do not include the write grant.
    view.rerender(tree([READ]));

    await waitFor(() =>
      expect((screen.getByLabelText("Subject") as HTMLInputElement).readOnly).toBe(true),
    );
    expect((screen.getByLabelText("Subject") as HTMLInputElement).value).toBe(DRAFT.subject);
    // Nothing is left that claims to be an unsaved edit no one can save.
    expect(screen.queryByText("You have unsaved changes.")).toBeNull();
    expect(screen.getByText("You can read this draft, not change it")).toBeTruthy();
  });

  it("does not touch what someone typed who is allowed to", async () => {
    mockReads();
    const user = userEvent.setup();
    const view = render(tree([READ, WRITE]));

    const subject = (await screen.findByLabelText("Subject")) as HTMLInputElement;
    await user.type(subject, "!");
    view.rerender(tree([READ, WRITE, "engage.campaign.campaign.send:ORG"]));

    expect((screen.getByLabelText("Subject") as HTMLInputElement).value).toBe(`${DRAFT.subject}!`);
    expect(screen.getByText("You have unsaved changes.")).toBeTruthy();
  });

  it("drops a validation error along with the text it was about", async () => {
    mockReads();
    const user = userEvent.setup();
    const view = render(tree([READ, WRITE]));

    await user.clear(await screen.findByLabelText("Campaign name"));
    await user.click(screen.getByRole("button", { name: "Save draft" }));
    expect(screen.getByText("Give the campaign a name so you can find it later.")).toBeTruthy();

    view.rerender(tree([READ]));

    await waitFor(() =>
      expect(screen.queryByText("Give the campaign a name so you can find it later.")).toBeNull(),
    );
    expect((screen.getByLabelText("Campaign name") as HTMLInputElement).value).toBe(DRAFT.name);
  });
});

describe("the page heading focus is sent to", () => {
  it("is the h1 itself, focusable by script and not part of the tab order", async () => {
    mockReads();
    render(tree([READ, WRITE]));

    const heading = await screen.findByRole("heading", { level: 1 });
    expect(heading.tagName).toBe("H1");
    expect(heading.getAttribute("tabindex")).toBe("-1");
    heading.focus();
    // The h1, not a span inside it: focusing it reads "heading level 1, <title>".
    expect(document.activeElement).toBe(heading);
    expect(heading.children.length).toBe(0);
  });
});

describe("the preview's honesty about what it cannot block", () => {
  it("says prefetch, preconnect and DNS-prefetch hints are not something it can block", async () => {
    mockReads({ ...DRAFT, html_body: '<link rel="dns-prefetch" href="//track.example.com"><p>Hi</p>' });
    render(tree([READ, WRITE]));

    await screen.findByTitle("HTML preview of the campaign body");
    expect(
      screen.getByText(/Prefetch, preconnect and DNS-prefetch hints in\s+the markup are not something this preview can block/),
    ).toBeTruthy();
    expect(screen.getByText(/may still\s+contact those hosts/)).toBeTruthy();
  });
});
