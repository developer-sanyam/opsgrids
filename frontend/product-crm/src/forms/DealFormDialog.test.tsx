import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Company, Deal, Owner } from "@/api/types";
import { DealFormDialog } from "./DealFormDialog";

const OWNER: Owner = { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE", is_active: true };
const COMPANY: Company = {
  id: "comp-1",
  name: "Northwind Logistics",
  domain: "northwind.co",
  industry: "Logistics",
  size_band: "501-1000",
  city: "Mumbai",
  country: "India",
  status: "customer",
  owner_id: "own-1",
  annual_revenue_minor: 0,
  currency: "INR",
  created_at: "2024-02-11T09:00:00Z",
};

const CREATED: Deal = {
  id: "deal-new",
  name: "Fleet telematics rollout",
  company_id: "comp-1",
  primary_contact_id: null,
  value_minor: 3_200_000_000,
  currency: "INR",
  stage: "lead",
  owner_id: "own-1",
  probability: 20,
  close_date: "2026-10-01",
  created_at: "2026-08-01T09:00:00Z",
  last_activity_at: null,
  source: "",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DealFormDialog — create", () => {
  function open(onSaved = vi.fn()) {
    render(
      <DealFormDialog
        deal={null}
        companies={[COMPANY]}
        contacts={[]}
        owners={[OWNER]}
        onOwnerCreated={vi.fn()}
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );
    return { user: userEvent.setup(), onSaved };
  }

  it("requires a name before submitting", async () => {
    const spy = vi.spyOn(client, "createDeal");
    const { user } = open();

    await user.click(screen.getByRole("button", { name: /create deal/i }));

    expect(await screen.findByText(/give the deal a name/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("parses the major-unit value into integer minor units — never parseFloat * 100", async () => {
    vi.spyOn(client, "createDeal").mockResolvedValue(CREATED);
    const { user, onSaved } = open();

    await user.type(screen.getByLabelText(/deal name/i), "Fleet telematics rollout");
    await user.selectOptions(screen.getByLabelText(/^company$/i), COMPANY.id);
    await user.selectOptions(screen.getByLabelText(/^owner$/i), OWNER.id);
    const valueInput = screen.getByLabelText(/^value$/i);
    await user.clear(valueInput);
    await user.type(valueInput, "32000000");
    const closeDate = screen.getByLabelText(/close date/i);
    await user.type(closeDate, "2026-10-01");
    await user.click(screen.getByRole("button", { name: /create deal/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED));
    const sent = vi.mocked(client.createDeal).mock.calls[0]?.[0];
    expect(sent?.value_minor).toBe(3_200_000_000);
    expect(Number.isInteger(sent?.value_minor)).toBe(true);
  });

  it("rejects an amount with more than two decimal places before submitting", async () => {
    const spy = vi.spyOn(client, "createDeal");
    const { user } = open();

    await user.type(screen.getByLabelText(/deal name/i), "Fleet telematics rollout");
    await user.selectOptions(screen.getByLabelText(/^company$/i), COMPANY.id);
    await user.selectOptions(screen.getByLabelText(/^owner$/i), OWNER.id);
    const valueInput = screen.getByLabelText(/^value$/i);
    await user.clear(valueInput);
    await user.type(valueInput, "320.999");
    await user.type(screen.getByLabelText(/close date/i), "2026-10-01");
    await user.click(screen.getByRole("button", { name: /create deal/i }));

    expect(await screen.findByText(/no more than two decimal places/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("offers a Stage field on create", () => {
    open();
    expect(screen.getByLabelText(/^stage$/i)).toBeTruthy();
  });

  it("maps a 409 duplicate-name failure to the dialog banner", async () => {
    vi.spyOn(client, "createDeal").mockRejectedValue(
      new ApiError(409, "A deal with that name already exists.", {
        title: "Conflict",
        status: 409,
        detail: "A deal with that name already exists.",
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/deal name/i), "Fleet telematics rollout");
    await user.selectOptions(screen.getByLabelText(/^company$/i), COMPANY.id);
    await user.selectOptions(screen.getByLabelText(/^owner$/i), OWNER.id);
    await user.type(screen.getByLabelText(/^value$/i), "1000");
    await user.type(screen.getByLabelText(/close date/i), "2026-10-01");
    await user.click(screen.getByRole("button", { name: /create deal/i }));

    expect(await screen.findByText(/a deal with that name already exists/i)).toBeTruthy();
  });
});

describe("DealFormDialog — edit", () => {
  it("never offers a Stage field — a move only ever goes through the stage endpoint", () => {
    vi.spyOn(client, "updateDeal").mockResolvedValue(CREATED);
    render(
      <DealFormDialog
        deal={CREATED}
        companies={[COMPANY]}
        contacts={[]}
        owners={[OWNER]}
        onOwnerCreated={vi.fn()}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    expect(screen.queryByLabelText(/^stage$/i)).toBeNull();
  });
});
