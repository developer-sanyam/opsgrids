import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Audience, Campaign, Page } from "@/api/types";
import { CampaignFormDialog } from "./CampaignFormDialog";

const AUDIENCE: Audience = {
  id: "aud-1",
  name: "Enterprise India",
  size: 4210,
  criteria: "1000+ employees",
  created_at: "2026-01-01",
};

const CAMPAIGN: Campaign = {
  id: "cmp-1",
  name: "Diwali Enterprise Push",
  channel: "search",
  status: "draft",
  currency: "INR",
  budget_minor: 3200000,
  spent_minor: 2840000,
  revenue_minor: 14200000,
  pipeline_minor: 32000000,
  leads: 640,
  conversions: 34,
  goal: "Enterprise pipeline",
  audience_id: "aud-1",
  start_date: "2026-06-15",
  end_date: null,
  owner: "Meghna Rao",
};

const EMPTY_CAMPAIGNS: Page<Campaign> = { items: [], total: 0, page: 1, size: 1, pages: 0 };

function mockAudiencePicker(items: Audience[] = [AUDIENCE]) {
  vi.spyOn(client, "listAudiencesForPicker").mockResolvedValue(items);
}

function mockCurrencyProbe(page: Page<Campaign> = EMPTY_CAMPAIGNS) {
  vi.spyOn(client, "getCampaigns").mockResolvedValue(page);
}

afterEach(() => {
  vi.restoreAllMocks();
});

function openCreate(onSaved = vi.fn(), onClose = vi.fn()) {
  render(<CampaignFormDialog campaign={null} onClose={onClose} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved, onClose };
}

async function fillRequired(user: ReturnType<typeof userEvent.setup>) {
  await user.type(await screen.findByLabelText("Name"), "Diwali Enterprise Push");
  await user.selectOptions(await screen.findByLabelText("Audience"), "aud-1");
  // The field defaults to today, not blank — clear it first, the way a person editing a
  // pre-filled date field would.
  const startField = screen.getByLabelText("Start date");
  await user.clear(startField);
  await user.type(startField, "2026-06-15");
}

describe("CampaignFormDialog — create", () => {
  it("refuses an empty name, client-side, before any request", async () => {
    mockAudiencePicker();
    mockCurrencyProbe();
    const spy = vi.spyOn(client, "createCampaign");
    const { user } = openCreate();

    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    expect(await screen.findByText("Give the campaign a name, 200 characters or fewer.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("leads a zero-audience organization to create one first, instead of a dead end", async () => {
    mockAudiencePicker([]);
    mockCurrencyProbe();
    openCreate();

    expect(
      await screen.findByText("Every campaign needs an audience, and this organization has none yet."),
    ).toBeTruthy();
    expect(screen.queryByLabelText("Audience")).toBeNull();
  });

  it("refuses an end date before the start date, client-side", async () => {
    mockAudiencePicker();
    mockCurrencyProbe();
    const spy = vi.spyOn(client, "createCampaign");
    const { user } = openCreate();

    await fillRequired(user);
    await user.type(screen.getByLabelText(/End date/), "2026-05-01");
    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    expect(await screen.findByText("The end date can't be before the start date.")).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("disables submit and blocks a double submit while pending", async () => {
    mockAudiencePicker();
    mockCurrencyProbe();
    let resolveCreate: (value: Campaign) => void = () => undefined;
    vi.spyOn(client, "createCampaign").mockReturnValue(
      new Promise((resolve) => {
        resolveCreate = resolve;
      }),
    );
    const { user } = openCreate();

    await fillRequired(user);
    const submit = screen.getByRole("button", { name: "Create campaign" });
    await user.click(submit);
    await user.click(submit);

    expect(screen.getByRole("button", { name: "Creating…" })).toBeTruthy();
    expect(client.createCampaign).toHaveBeenCalledTimes(1);
    resolveCreate(CAMPAIGN);
  });

  it("parses a major-unit budget into integer minor units and posts audience_id, closing on success", async () => {
    mockAudiencePicker();
    mockCurrencyProbe();
    vi.spyOn(client, "createCampaign").mockResolvedValue(CAMPAIGN);
    const { user, onSaved } = openCreate();

    await fillRequired(user);
    const budget = screen.getByLabelText("Budget");
    await user.clear(budget);
    await user.type(budget, "1250.50");
    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    await waitFor(() =>
      expect(client.createCampaign).toHaveBeenCalledWith(
        expect.objectContaining({ name: "Diwali Enterprise Push", audience_id: "aud-1", budget_minor: 125050 }),
      ),
    );
    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CAMPAIGN));
  });

  it("a 422 from the server lands on its field", async () => {
    mockAudiencePicker();
    mockCurrencyProbe();
    const problem = {
      status: 422,
      title: "Validation failed",
      detail: "Request payload failed validation",
      errors: [{ field: "goal", code: "string_too_long", message: "String should have at most 200 characters" }],
    };
    vi.spyOn(client, "createCampaign").mockRejectedValue(new ApiError(422, "Validation failed", problem));
    const { user } = openCreate();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    expect(await screen.findByText("String should have at most 200 characters")).toBeTruthy();
  });

  it("a 404 (stale audience) lands on the audience field, not the top banner", async () => {
    mockAudiencePicker();
    mockCurrencyProbe();
    const problem = { status: 404, title: "Not found", detail: "That audience is not part of this organization" };
    vi.spyOn(client, "createCampaign").mockRejectedValue(new ApiError(404, "Not found", problem));
    const { user } = openCreate();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    const matches = await screen.findAllByText("That audience is not part of this organization");
    expect(matches.length).toBeGreaterThan(0);
  });

  it("a 403 (no write grant) shows the role message", async () => {
    mockAudiencePicker();
    mockCurrencyProbe();
    const problem = { status: 403, title: "Not permitted", detail: "You need the Marketing administrator role to do this." };
    vi.spyOn(client, "createCampaign").mockRejectedValue(new ApiError(403, "Not permitted", problem));
    const { user } = openCreate();

    await fillRequired(user);
    await user.click(screen.getByRole("button", { name: "Create campaign" }));

    expect(await screen.findByText("You need the Marketing administrator role to do this.")).toBeTruthy();
  });
});

describe("CampaignFormDialog — edit", () => {
  it("pre-fills from the campaign, shows a Results group, and never a status field", async () => {
    mockAudiencePicker();
    render(<CampaignFormDialog campaign={CAMPAIGN} onClose={vi.fn()} onSaved={vi.fn()} />);

    expect(await screen.findByLabelText("Name")).toHaveProperty("value", "Diwali Enterprise Push");
    expect(screen.getByLabelText("Budget")).toHaveProperty("value", "32000.00");
    expect(screen.getByLabelText("Spent")).toHaveProperty("value", "28400.00");
    expect(screen.queryByLabelText("Status")).toBeNull();
    expect(screen.getByRole("button", { name: "Save changes" })).toBeTruthy();
  });

  it("submits only a patch, and never sends currency", async () => {
    mockAudiencePicker();
    vi.spyOn(client, "updateCampaign").mockResolvedValue({ ...CAMPAIGN, name: "Renamed" });
    const user = userEvent.setup();
    const onSaved = vi.fn();
    render(<CampaignFormDialog campaign={CAMPAIGN} onClose={vi.fn()} onSaved={onSaved} />);

    const nameField = await screen.findByLabelText("Name");
    await user.clear(nameField);
    await user.type(nameField, "Renamed");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(client.updateCampaign).toHaveBeenCalledTimes(1));
    const [id, patch] = vi.mocked(client.updateCampaign).mock.calls[0] as [string, Record<string, unknown>];
    expect(id).toBe("cmp-1");
    expect(patch.name).toBe("Renamed");
    expect(patch).not.toHaveProperty("currency");
    expect(patch).not.toHaveProperty("status");
  });
});
