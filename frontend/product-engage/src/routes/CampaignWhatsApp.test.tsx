import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import { CAMPAIGN_DRAFT, WHATSAPP_TEMPLATES } from "@/api/fixtures";
import type { CampaignOut, Page, ProviderTemplateOut, SegmentOut } from "@/api/types";
import { CampaignPage, NewCampaignPage } from "./CampaignEditor";

/**
 * A WhatsApp broadcast to a segment: the channel and segment are picked on the form, only approved
 * templates are offered, and the recipient count is shown before anything is sent.
 */

const APPROVED: ProviderTemplateOut = WHATSAPP_TEMPLATES[0]!;
const PENDING: ProviderTemplateOut = { ...APPROVED, id: "tpl-pending", provider_name: "draft_one", sendable: false };
const SEGMENT: SegmentOut = {
  id: "seg-1",
  name: "Repeat buyers",
  list_id: null,
  created_within_days: 90,
  active_within_days: null,
  created_at: "2026-10-01T00:00:00Z",
  updated_at: "2026-10-01T00:00:00Z",
};
const WA_DRAFT: CampaignOut = {
  ...CAMPAIGN_DRAFT,
  channel: "whatsapp",
  sender_id: null,
  subject: "",
  html_body: "",
  text_body: "",
  provider_template_id: APPROVED.id,
  template_values: { customer_name: "friend", tracking_url: "x" },
  audience: "segment",
  list_id: null,
  segment_id: SEGMENT.id,
};
const GRANTS = [
  "engage.campaign.campaign.read:ORG",
  "engage.campaign.campaign.write:ORG",
  "engage.campaign.campaign.send:ORG",
  "engage.audience.list.write:ORG",
];

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function mockReads() {
  vi.spyOn(client, "listSenders").mockResolvedValue(page([]));
  vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
  vi.spyOn(client, "listLists").mockResolvedValue(page([]));
  vi.spyOn(client, "listSegments").mockResolvedValue(page([SEGMENT]));
  vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(page([APPROVED, PENDING]));
  vi.spyOn(client, "listCampaignRecipients").mockResolvedValue(page([]));
  return vi.spyOn(client, "countSegment").mockResolvedValue({ channel: "whatsapp", recipients: 42 });
}

function renderAt(path: string) {
  return render(
    <PermissionProvider grants={GRANTS}>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/campaigns/new" element={<NewCampaignPage />} />
          <Route path="/campaigns/:campaignId" element={<CampaignPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

describe("a new WhatsApp campaign", () => {
  it("offers approved templates only, counts the segment, and drafts the template and segment", async () => {
    const count = mockReads();
    const create = vi.spyOn(client, "createCampaign").mockResolvedValue(WA_DRAFT);
    vi.spyOn(client, "getCampaign").mockResolvedValue(WA_DRAFT);
    const user = userEvent.setup();
    renderAt("/campaigns/new");

    await user.type(await screen.findByLabelText("Campaign name"), "Diwali");
    await user.click(screen.getByRole("radio", { name: /WhatsApp/ }));
    expect(screen.queryByLabelText("Send as")).toBeNull();
    expect(screen.queryByLabelText("Subject")).toBeNull();

    const template = screen.getByLabelText("Template") as HTMLSelectElement;
    const offered = Array.from(template.options).map((o) => o.value);
    expect(offered).toContain(APPROVED.id);
    expect(offered).not.toContain(PENDING.id);
    await user.selectOptions(template, APPROVED.id);
    await user.type(screen.getByLabelText(/customer_name/), "friend");

    await user.click(screen.getByRole("radio", { name: /A segment/ }));
    await user.selectOptions(await screen.findByLabelText("Segment"), SEGMENT.id);
    expect(await screen.findByText("42 contacts would receive this right now.")).toBeTruthy();
    expect(count).toHaveBeenCalledWith(SEGMENT.id, "whatsapp");

    await user.click(screen.getByRole("button", { name: "Create draft" }));
    await waitFor(() =>
      expect(create).toHaveBeenCalledWith({
        name: "Diwali",
        channel: "whatsapp",
        provider_template_id: APPROVED.id,
        template_values: { customer_name: "friend" },
        audience: "segment",
        segment_id: SEGMENT.id,
      }),
    );
  });

  it("asks for a template and a segment before drafting", async () => {
    mockReads();
    const create = vi.spyOn(client, "createCampaign");
    const user = userEvent.setup();
    renderAt("/campaigns/new");

    await user.type(await screen.findByLabelText("Campaign name"), "Diwali");
    await user.click(screen.getByRole("radio", { name: /WhatsApp/ }));
    await user.click(screen.getByRole("radio", { name: /A segment/ }));
    await user.click(screen.getByRole("button", { name: "Create draft" }));

    expect(screen.getByText("Choose the approved template this campaign sends.")).toBeTruthy();
    expect(screen.getByText("Choose which segment this campaign goes to.")).toBeTruthy();
    expect(create).not.toHaveBeenCalled();
  });
});

describe("a WhatsApp draft's send panel", () => {
  it("says how many would receive it before sending, and offers no email preview", async () => {
    mockReads();
    vi.spyOn(client, "getCampaign").mockResolvedValue(WA_DRAFT);
    renderAt(`/campaigns/${WA_DRAFT.id}`);

    expect(await screen.findByText(/42 contacts in the segment chosen for this campaign who opted in to WhatsApp marketing/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Send a preview" })).toBeNull();
    expect(screen.getByRole("button", { name: "Send campaign" })).toBeTruthy();
  });
});
