import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Company, Contact, Owner } from "@/api/types";
import { ContactFormDialog } from "./ContactFormDialog";

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

const EXISTING: Contact = {
  id: "cont-1",
  full_name: "Asha Rao",
  title: "VP Ops",
  email: "asha@northwind.co",
  phone: "+91 98••• ••210",
  company_id: "comp-1",
  status: "customer",
  owner_id: "own-1",
  last_activity_at: null,
  created_at: "2024-02-12T09:00:00Z",
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ContactFormDialog — create", () => {
  function open(onSaved = vi.fn()) {
    render(
      <ContactFormDialog
        contact={null}
        companies={[COMPANY]}
        owners={[OWNER]}
        onOwnerCreated={vi.fn()}
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );
    return { user: userEvent.setup(), onSaved };
  }

  it("requires a company before submitting", async () => {
    const spy = vi.spyOn(client, "createContact");
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Asha Rao");
    await user.type(screen.getByLabelText(/^email$/i), "asha@northwind.co");
    await user.click(screen.getByRole("button", { name: /create contact/i }));

    expect(await screen.findByText(/choose a company\./i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("sends raw_phone only when the caller typed one, never a phone field", async () => {
    vi.spyOn(client, "createContact").mockResolvedValue(EXISTING);
    const { user, onSaved } = open();

    await user.type(screen.getByLabelText(/full name/i), "Asha Rao");
    await user.type(screen.getByLabelText(/^email$/i), "asha@northwind.co");
    await user.selectOptions(screen.getByLabelText(/^company$/i), COMPANY.id);
    await user.selectOptions(screen.getByLabelText(/^owner$/i), OWNER.id);
    await user.click(screen.getByRole("button", { name: /create contact/i }));

    await waitFor(() => expect(onSaved).toHaveBeenCalledWith(EXISTING));
    const sent = vi.mocked(client.createContact).mock.calls[0]?.[0];
    expect(sent).toMatchObject({ full_name: "Asha Rao", email: "asha@northwind.co" });
    expect(sent).not.toHaveProperty("phone");
    expect(sent).not.toHaveProperty("raw_phone");
  });

  it("disables submit while pending and blocks a second submission", async () => {
    let resolve!: (value: Contact) => void;
    vi.spyOn(client, "createContact").mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Asha Rao");
    await user.type(screen.getByLabelText(/^email$/i), "asha@northwind.co");
    await user.selectOptions(screen.getByLabelText(/^company$/i), COMPANY.id);
    await user.selectOptions(screen.getByLabelText(/^owner$/i), OWNER.id);
    const submit = screen.getByRole("button", { name: /create contact/i });
    await user.click(submit);

    const pendingButton = await screen.findByRole("button", { name: /creating/i });
    expect(pendingButton).toHaveProperty("disabled", true);
    await user.click(pendingButton);

    resolve(EXISTING);
    await waitFor(() => expect(client.createContact).toHaveBeenCalledTimes(1));
  });

  it("maps a 422 onto the field it names", async () => {
    vi.spyOn(client, "createContact").mockRejectedValue(
      new ApiError(422, "Validation failed", {
        title: "Unprocessable",
        status: 422,
        detail: "Validation failed",
        errors: [{ field: "email", code: "value_error", message: "That email looks wrong." }],
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Asha Rao");
    await user.type(screen.getByLabelText(/^email$/i), "asha@northwind.co");
    await user.selectOptions(screen.getByLabelText(/^company$/i), COMPANY.id);
    await user.selectOptions(screen.getByLabelText(/^owner$/i), OWNER.id);
    await user.click(screen.getByRole("button", { name: /create contact/i }));

    expect(await screen.findByText(/that email looks wrong/i)).toBeTruthy();
    // Preserved, not cleared, on failure.
    expect((screen.getByLabelText(/full name/i) as HTMLInputElement).value).toBe("Asha Rao");
  });

  it("shows the server's 403 message rather than a generic one", async () => {
    vi.spyOn(client, "createContact").mockRejectedValue(
      new ApiError(403, "You can't create a contact for another rep.", {
        title: "Forbidden",
        status: 403,
        detail: "You can't create a contact for another rep.",
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/full name/i), "Asha Rao");
    await user.type(screen.getByLabelText(/^email$/i), "asha@northwind.co");
    await user.selectOptions(screen.getByLabelText(/^company$/i), COMPANY.id);
    await user.selectOptions(screen.getByLabelText(/^owner$/i), OWNER.id);
    await user.click(screen.getByRole("button", { name: /create contact/i }));

    expect(await screen.findByText(/you can't create a contact for another rep/i)).toBeTruthy();
  });
});

describe("ContactFormDialog — edit", () => {
  it("never prefills the phone input from the mask, and only replaces it when typed", async () => {
    vi.spyOn(client, "updateContact").mockResolvedValue(EXISTING);
    const setPhone = vi.spyOn(client, "setContactPhone");
    const onSaved = vi.fn();
    render(
      <ContactFormDialog
        contact={EXISTING}
        companies={[COMPANY]}
        owners={[OWNER]}
        onOwnerCreated={vi.fn()}
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );
    const user = userEvent.setup();

    // The mask is shown as text, never as an input value.
    expect(screen.getByText(EXISTING.phone)).toBeTruthy();
    expect(screen.queryByDisplayValue(EXISTING.phone)).toBeNull();

    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() => expect(client.updateContact).toHaveBeenCalled());
    expect(setPhone).not.toHaveBeenCalled();
  });

  it("calls setContactPhone only when a replacement number was typed", async () => {
    vi.spyOn(client, "updateContact").mockResolvedValue(EXISTING);
    vi.spyOn(client, "setContactPhone").mockResolvedValue({ ...EXISTING, phone: "+91 99••• ••999" });
    const onSaved = vi.fn();
    render(
      <ContactFormDialog
        contact={EXISTING}
        companies={[COMPANY]}
        owners={[OWNER]}
        onOwnerCreated={vi.fn()}
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );
    const user = userEvent.setup();

    await user.type(screen.getByLabelText(/replace phone number/i), "+919999999999");
    await user.click(screen.getByRole("button", { name: /save changes/i }));

    await waitFor(() =>
      expect(client.setContactPhone).toHaveBeenCalledWith(EXISTING.id, { raw_phone: "+919999999999" }),
    );
    expect(onSaved).toHaveBeenCalled();
  });
});
