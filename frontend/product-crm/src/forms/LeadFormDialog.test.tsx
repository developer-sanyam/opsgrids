import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Lead, Owner } from "@/api/types";
import { LeadFormDialog } from "./LeadFormDialog";

const OWNERS: Owner[] = [
  { id: "own-1", full_name: "Sofia Marchetti", email: "sofia@example.com", role: "AE", is_active: true },
  { id: "own-2", full_name: "Rahul Deshpande", email: "rahul@example.com", role: "AM", is_active: false },
];

const CREATED: Lead = {
  id: "lead-new",
  full_name: "Meera Iyer",
  company: "Solstice Energy",
  email: "m•••@s•••",
  phone: null,
  source: "manual",
  status: "new",
  next_statuses: [],
  owner_id: null,
  notes: "",
  converted_company_id: null,
  converted_contact_id: null,
  converted_deal_id: null,
  converted_at: null,
  created_at: "2026-08-20T09:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

function open(onSaved = vi.fn()) {
  render(<LeadFormDialog owners={OWNERS} onClose={vi.fn()} onSaved={onSaved} />);
  return { user: userEvent.setup(), onSaved };
}

describe("LeadFormDialog", () => {
  it("shows a validation message and never calls the API when the name is blank", async () => {
    const spy = vi.spyOn(client, "createLead");
    const { user } = open();

    await user.click(screen.getByRole("button", { name: /add lead/i }));

    expect(await screen.findByText(/enter their full name/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("only offers active reps in the owner picker", () => {
    open();
    expect(screen.queryByRole("option", { name: "Rahul Deshpande" })).toBeNull();
    expect(screen.getByRole("option", { name: "Sofia Marchetti" })).toBeTruthy();
  });

  it("omits owner_id entirely when left on auto-assign", async () => {
    vi.spyOn(client, "createLead").mockResolvedValue(CREATED);
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    await waitFor(() => expect(client.createLead).toHaveBeenCalled());
    const call = vi.mocked(client.createLead).mock.calls[0]?.[0];
    expect(call).not.toHaveProperty("owner_id");
  });

  it("naming an owner bypasses the rotation on purpose", async () => {
    vi.spyOn(client, "createLead").mockResolvedValue(CREATED);
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.selectOptions(screen.getByLabelText(/owner/i), "own-1");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    await waitFor(() =>
      expect(client.createLead).toHaveBeenCalledWith(
        expect.objectContaining({ owner_id: "own-1" }),
      ),
    );
  });

  it("sends null for empty optional fields, never empty strings", async () => {
    vi.spyOn(client, "createLead").mockResolvedValue(CREATED);
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    await waitFor(() => expect(client.createLead).toHaveBeenCalled());
    expect(client.createLead).toHaveBeenCalledWith(
      expect.objectContaining({ company: null, email: null, phone: null, notes: "" }),
    );
  });

  it("on success calls onSaved with the created lead", async () => {
    vi.spyOn(client, "createLead").mockResolvedValue(CREATED);
    const { user, onSaved } = open();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(CREATED));
  });

  it("maps a 409 duplicate-email failure onto the dialog, keeping the typed data", async () => {
    vi.spyOn(client, "createLead").mockRejectedValue(
      new ApiError(409, "A lead with that email already exists.", {
        title: "Conflict",
        status: 409,
        detail: "A lead with that email already exists.",
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.type(screen.getByLabelText(/^email/i), "meera@solstice.example");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    expect(await screen.findByText(/a lead with that email already exists/i)).toBeTruthy();
    expect((screen.getByLabelText(/full name/i) as HTMLInputElement).value).toBe("Meera Iyer");
  });

  // 2026-10-15 customer retest: "Auto-assign" left the lead Unassigned in a one-person organization,
  // and an unassigned lead cannot be converted.
  it("on auto-assign, puts the signed-in login on the roster and names them as the fallback owner", async () => {
    vi.spyOn(client, "createLead").mockResolvedValue(CREATED);
    const ensureMyRep = vi.fn(() => Promise.resolve(OWNERS[0]));
    render(<LeadFormDialog owners={OWNERS} onClose={vi.fn()} onSaved={vi.fn()} ensureMyRep={ensureMyRep} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    await waitFor(() => expect(client.createLead).toHaveBeenCalled());
    expect(ensureMyRep).toHaveBeenCalledTimes(1);
    const call = vi.mocked(client.createLead).mock.calls[0]?.[0];
    expect(call).not.toHaveProperty("owner_id");
    expect(call).toMatchObject({ fallback_owner_id: "own-1" });
  });

  it("does not ask for the login's rep when an owner is named", async () => {
    vi.spyOn(client, "createLead").mockResolvedValue(CREATED);
    const ensureMyRep = vi.fn(() => Promise.resolve(OWNERS[0]));
    render(<LeadFormDialog owners={OWNERS} onClose={vi.fn()} onSaved={vi.fn()} ensureMyRep={ensureMyRep} />);
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.selectOptions(screen.getByLabelText(/owner/i), "own-1");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    await waitFor(() => expect(client.createLead).toHaveBeenCalled());
    expect(ensureMyRep).not.toHaveBeenCalled();
    expect(vi.mocked(client.createLead).mock.calls[0]?.[0]).not.toHaveProperty("fallback_owner_id");
  });

  it("sends a bare Indian mobile as +91 E.164", async () => {
    vi.spyOn(client, "createLead").mockResolvedValue(CREATED);
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.type(screen.getByLabelText(/^phone/i), "90000 00031");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    await waitFor(() => expect(client.createLead).toHaveBeenCalled());
    expect(client.createLead).toHaveBeenCalledWith(expect.objectContaining({ phone: "+919000000031" }));
  });

  it("refuses a number it cannot read, in words, and sends nothing", async () => {
    const spy = vi.spyOn(client, "createLead");
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Meera Iyer");
    await user.type(screen.getByLabelText(/^phone/i), "12345");
    await user.click(screen.getByRole("button", { name: /add lead/i }));

    expect(await screen.findByText(/^Enter a 10-digit mobile/)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });
});
