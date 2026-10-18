import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ContactImportOut, ContactOut, Page } from "@/api/types";
import { ContactsPage } from "./Contacts";

/**
 * The audience screen, from the side that matters most: **no unmasked address from any request
 * this screen makes is ever rendered.** `ContactOut.email` is always the mask the service sends,
 * this screen never derives a plaintext form of it, and a real-looking local part typed into a
 * form and then submitted must not reappear on the row that request produced.
 */

const MASKED: ContactOut = {
  id: "c1a0f2e4-9b3d-4a7c-8e1f-2d6b9c4a5e10",
  email: "p•••@vaayu-logistics.example.com",
  name: "Priya Raghavan",
  attributes: {},
  created_at: "2026-08-29T09:18:02Z",
  updated_at: "2026-08-29T09:18:02Z",
};

const SECOND: ContactOut = {
  id: "d2b1e3f5-8c4e-4b8d-9f2a-3e7c0d5b6f21",
  email: "a•••@vaayu-freight.example.com",
  name: "Arjun Mehta",
  attributes: {},
  created_at: "2026-09-10T14:02:11Z",
  updated_at: "2026-09-18T08:44:59Z",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

/**
 * Every test below acts as someone holding the write grant, matching how the rest of the suite
 * exercises write flows. The one exception is the "permission gating" block, which renders with
 * no grants at all to prove the controls disappear for a read-only caller.
 */
function renderPage() {
  return render(
    <PermissionProvider grants={["engage.audience.contact.write:ORG"]}>
      <MemoryRouter>
        <ContactsPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

describe("loading", () => {
  it("shows a skeleton while the read is in flight", () => {
    vi.spyOn(client, "listContacts").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("empty", () => {
  it("says what to do next rather than showing nothing", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("No contacts yet")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add a contact" })).toBeTruthy();
  });
});

describe("error", () => {
  it("shows a real failure for anything other than a 402", async () => {
    vi.spyOn(client, "listContacts").mockRejectedValue(new ApiError(500, "Internal error", null));
    renderPage();
    expect(await screen.findByText("Couldn't load your contacts")).toBeTruthy();
  });

  it("shows Engage-not-active on a 402, not a generic failure", async () => {
    vi.spyOn(client, "listContacts").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", null),
    );
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    expect(screen.queryByText("Couldn't load your contacts")).toBeNull();
  });

  it("keeps a 403 an error, not the not-active state", async () => {
    vi.spyOn(client, "listContacts").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.contact.read", null),
    );
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load your contacts");
    expect(screen.queryByText("Engage isn't active for this organization yet")).toBeNull();
  });
});

describe("populated", () => {
  it("renders every contact with its masked address, never a plaintext one", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([MASKED, SECOND]));
    renderPage();

    expect(await screen.findByText("Priya Raghavan")).toBeTruthy();
    expect(screen.getByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
    expect(screen.getByText("Arjun Mehta")).toBeTruthy();
    expect(screen.getByText("a•••@vaayu-freight.example.com")).toBeTruthy();
  });

  it("gives the search hint the real constraint: a name substring, or an exact email", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([MASKED]));
    renderPage();
    await screen.findByText("Priya Raghavan");
    expect(
      screen.getByPlaceholderText("Search by name, or paste an exact email address"),
    ).toBeTruthy();
  });
});

describe("adding a contact", () => {
  it("submits the typed address and never echoes it back — only the server's mask appears", async () => {
    vi.spyOn(client, "listContacts")
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(page([MASKED]));
    const spy = vi.spyOn(client, "addContact").mockResolvedValue(MASKED);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Add a contact" }));
    screen.getByRole("dialog");
    await user.type(
      screen.getByLabelText("Email"),
      "priya.real.address@vaayu-logistics.example.com",
    );
    await user.type(screen.getByLabelText("Name (optional)"), "Priya Raghavan");
    await user.click(screen.getByRole("button", { name: "Add contact" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith({
      email: "priya.real.address@vaayu-logistics.example.com",
      name: "Priya Raghavan",
    });
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    // The typed plaintext never appears anywhere on the page — only the mask the server sent.
    expect(screen.queryByText(/priya\.real\.address/)).toBeNull();
    expect(await screen.findByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
  });

  it("sends a marketing opt-in only when the operator says how the person agreed", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([]));
    const spy = vi.spyOn(client, "addContact").mockResolvedValue(MASKED);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Add a contact" }));
    expect(
      (screen.getByLabelText("How they agreed to marketing email") as HTMLSelectElement).value,
    ).toBe("");
    expect(screen.queryByLabelText("Note (optional)")).toBeNull();
    await user.type(screen.getByLabelText("Email"), "priya@vaayu-logistics.example.com");
    await user.selectOptions(screen.getByLabelText("How they agreed to marketing email"), "in_person");
    await user.type(screen.getByLabelText("Note (optional)"), "trade fair, stall 12");
    await user.click(screen.getByRole("button", { name: "Add contact" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith({
      email: "priya@vaayu-logistics.example.com",
      marketing_opt_in: { source: "in_person", evidence: "trade fair, stall 12" },
    });
  });

  it("keeps the typed values on a rejected submit", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([]));
    vi.spyOn(client, "addContact").mockRejectedValue(
      new ApiError(409, "A contact with this address already exists", null),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Add a contact" }));
    await user.type(screen.getByLabelText("Email"), "priya@vaayu-logistics.example.com");
    await user.click(screen.getByRole("button", { name: "Add contact" }));

    expect(await screen.findByText("A contact with this address already exists")).toBeTruthy();
    expect((screen.getByLabelText("Email") as HTMLInputElement).value).toBe(
      "priya@vaayu-logistics.example.com",
    );
  });
});

describe("editing a contact", () => {
  it("seeds the name field from the contact, and doesn't send it back when only the email changed", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([MASKED]));
    const spy = vi.spyOn(client, "editContact").mockResolvedValue(MASKED);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Edit Priya Raghavan" }));
    screen.getByRole("dialog");
    // The name is real, not a mask, so unlike email it starts filled in rather than blank.
    expect((screen.getByLabelText("Name (optional)") as HTMLInputElement).value).toBe(
      "Priya Raghavan",
    );
    expect((screen.getByLabelText("Email (optional)") as HTMLInputElement).value).toBe("");

    await user.type(
      screen.getByLabelText("Email (optional)"),
      "priya.new.address@vaayu-logistics.example.com",
    );
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    // Only the field that actually changed goes over the wire — the untouched name is never
    // resent, so the service's "a present field means set it" rule can't blank it.
    expect(spy).toHaveBeenCalledWith(MASKED.id, {
      email: "priya.new.address@vaayu-logistics.example.com",
    });
  });

  it("sends the new name only when it was actually edited", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([MASKED]));
    const spy = vi.spyOn(client, "editContact").mockResolvedValue(MASKED);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Edit Priya Raghavan" }));
    const nameField = screen.getByLabelText("Name (optional)");
    await user.clear(nameField);
    await user.type(nameField, "Priya R. Raghavan");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith(MASKED.id, { name: "Priya R. Raghavan" });
  });
});

describe("WhatsApp opt-in", () => {
  it("opens the contact's WhatsApp consent from the row, for a reader too", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([MASKED]));
    const history = vi
      .spyOn(client, "listWhatsAppConsent")
      .mockResolvedValue({ items: [], total: 0, page: 1, size: 25, pages: 0 });
    const user = userEvent.setup();
    render(
      <PermissionProvider grants={["engage.audience.contact.read:ORG"]}>
        <MemoryRouter>
          <ContactsPage />
        </MemoryRouter>
      </PermissionProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "WhatsApp opt-in Priya Raghavan" }));
    expect(await screen.findByText(/No WhatsApp opt-in on record/)).toBeTruthy();
    expect(history).toHaveBeenCalledWith(MASKED.id);
  });
});

describe("permission gating", () => {
  it("hides Import, Add, Edit and Delete for a viewer with no contact.write grant", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([MASKED]));
    render(
      <PermissionProvider grants={["engage.audience.contact.read:ORG"]}>
        <MemoryRouter>
          <ContactsPage />
        </MemoryRouter>
      </PermissionProvider>,
    );

    await screen.findByText("Priya Raghavan");
    expect(screen.queryByRole("button", { name: "Import" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Add a contact" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();
    // Still readable — hiding a write control never hides the data itself.
    expect(screen.getByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
  });
});

describe("a contact copied from the CRM", () => {
  const FROM_CRM: ContactOut = {
    ...MASKED,
    email: "",
    phone: "+91 ••••• ••3446",
    origin: "crm",
    crm_contact_id: "7e1d4c2b-5a9f-4d3e-8b6c-1f2a3b4c5d6e",
  };

  it("shows the number for a phone-only contact and offers no edit or delete", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([FROM_CRM]));
    renderPage();

    await screen.findByText("Priya Raghavan");
    expect(screen.getByText("+91 ••••• ••3446")).toBeTruthy();
    expect(screen.getByText("From the CRM")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /^Edit/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();
  });
});

describe("a contact copied from a CRM lead", () => {
  it("is labelled, and (unlike a CRM contact copy) may be edited and deleted", async () => {
    const lead: ContactOut = { ...MASKED, origin: "crm_lead" };
    vi.spyOn(client, "listContacts").mockResolvedValue(page([lead]));
    renderPage();

    await screen.findByText("Priya Raghavan");
    expect(screen.getByText("From a CRM lead")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: /^Delete/ }).length).toBeGreaterThan(0);
  });
});

describe("deleting a contact", () => {
  it("removes the row once the service confirms, and says so", async () => {
    vi.spyOn(client, "listContacts")
      .mockResolvedValueOnce(page([MASKED]))
      .mockResolvedValueOnce(page([]));
    const spy = vi.spyOn(client, "deleteContact").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /Delete Priya Raghavan/ }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(MASKED.id));
    expect(await screen.findByText("No contacts yet")).toBeTruthy();
  });
});

describe("pagination after a delete empties a page", () => {
  it("steps back a page instead of showing the first-run empty state", async () => {
    const spy = vi
      .spyOn(client, "listContacts")
      // Initial load: page 1 of 2, one contact per page.
      .mockResolvedValueOnce({ items: [MASKED], total: 2, page: 1, size: 1, pages: 2 })
      // Navigate to page 2.
      .mockResolvedValueOnce({ items: [SECOND], total: 2, page: 2, size: 1, pages: 2 })
      // Delete the only contact on page 2 — the reload for page 2 now comes back empty.
      .mockResolvedValueOnce({ items: [], total: 1, page: 2, size: 1, pages: 1 })
      // Stepping back to page 1 finds the remaining contact.
      .mockResolvedValueOnce({ items: [MASKED], total: 1, page: 1, size: 1, pages: 1 });
    vi.spyOn(client, "deleteContact").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("Priya Raghavan");
    await user.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByText("Arjun Mehta");

    await user.click(screen.getByRole("button", { name: /Delete Arjun Mehta/ }));
    await user.click(screen.getByRole("button", { name: "Delete" }));

    // Lands back on page 1's contact rather than staying on an empty page 2.
    await screen.findByText("Priya Raghavan");
    expect(screen.queryByText("No contacts yet")).toBeNull();
    await waitFor(() => expect(spy).toHaveBeenLastCalledWith({ search: "", page: 1, size: 25 }));
  });
});

describe("bulk import", () => {
  it("renders every row's own result, and reports the pasted rows without leaking an address", async () => {
    vi.spyOn(client, "listContacts")
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(page([MASKED, SECOND]));
    const result: ContactImportOut = {
      created: 1,
      updated: 1,
      invalid: 1,
      results: [
        { index: 0, status: "created", id: MASKED.id, error: null },
        { index: 1, status: "updated", id: SECOND.id, error: null },
        { index: 2, status: "invalid", id: null, error: "Not a valid email address" },
      ],
    };
    const spy = vi.spyOn(client, "importContacts").mockResolvedValue(result);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = screen.getByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Contacts to import"),
      "priya@vaayu-logistics.example.com,Priya Raghavan\narjun@vaayu-freight.example.com,Arjun Mehta\nnot-an-email",
    );
    await user.click(within(dialog).getByRole("button", { name: "Import" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy).toHaveBeenCalledWith([
      { email: "priya@vaayu-logistics.example.com", name: "Priya Raghavan" },
      { email: "arjun@vaayu-freight.example.com", name: "Arjun Mehta" },
      { email: "not-an-email", name: "" },
    ]);

    expect(await screen.findByText("1 created")).toBeTruthy();
    expect(screen.getByText("1 updated")).toBeTruthy();
    expect(screen.getByText("1 invalid")).toBeTruthy();
    expect(screen.getByText("Not a valid email address")).toBeTruthy();
    // The result table shows row numbers, never the addresses that were pasted.
    expect(screen.queryByText(/priya@vaayu-logistics/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "Done" }));
    expect(await screen.findByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
  });

  it("refuses more than the import cap before sending anything", async () => {
    vi.spyOn(client, "listContacts").mockResolvedValue(page([]));
    const spy = vi.spyOn(client, "importContacts");
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = screen.getByRole("dialog");
    const rows = Array.from({ length: 1001 }, (_, i) => `person${i}@example.com`).join("\n");
    await user.click(within(dialog).getByLabelText("Contacts to import"));
    await user.paste(rows);
    await user.click(within(dialog).getByRole("button", { name: "Import" }));

    expect(await screen.findByText(/split the paste into batches of 1000/)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });
});
