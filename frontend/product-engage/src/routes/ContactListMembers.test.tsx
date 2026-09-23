import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ContactImportOut, ContactListOut, ContactOut, Page } from "@/api/types";
import { ContactListMembersPage } from "./ContactListMembers";

/**
 * One list's members. The rule that matters most is the one `Contacts.test.tsx` pins for the whole
 * audience: every address on screen is the service's mask — the members read, the picker's
 * results and the import's outcome never surface a plaintext one.
 */

const LIST: ContactListOut = {
  id: "9a1b2c3d-4e5f-4061-8a7b-1c2d3e4f5061",
  name: "Enterprise accounts",
  description: "Every enterprise-tier contact.",
  member_count: 2,
  created_at: "2026-08-20T09:00:00Z",
  updated_at: "2026-09-18T10:12:00Z",
};

const PRIYA: ContactOut = {
  id: "c1a0f2e4-9b3d-4a7c-8e1f-2d6b9c4a5e10",
  email: "p•••@vaayu-logistics.example.com",
  name: "Priya Raghavan",
  attributes: {},
  created_at: "2026-08-29T09:18:02Z",
  updated_at: "2026-08-29T09:18:02Z",
};

const ARJUN: ContactOut = {
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

const ALL_GRANTS = [
  "engage.audience.list.read:ORG",
  "engage.audience.list.write:ORG",
  "engage.audience.contact.write:ORG",
];

function renderPage(grants: readonly string[] = ALL_GRANTS) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[`/lists/${LIST.id}`]}>
        <Routes>
          <Route path="/lists/:listId" element={<ContactListMembersPage />} />
          <Route path="/lists" element={<p>All lists page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function mockRead(members: ContactOut[], list: ContactListOut = LIST) {
  vi.spyOn(client, "getList").mockResolvedValue(list);
  return vi.spyOn(client, "listListMembers").mockResolvedValue(page(members));
}

describe("loading", () => {
  it("shows a skeleton while the reads are in flight", () => {
    vi.spyOn(client, "getList").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "listListMembers").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("empty", () => {
  it("says how to add the first members", async () => {
    mockRead([], { ...LIST, member_count: 0 });
    renderPage();
    expect(await screen.findByText("No members yet")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Add contacts" }).length).toBeGreaterThan(0);
    expect(screen.getByRole("button", { name: "Import" })).toBeTruthy();
  });
});

describe("error", () => {
  it("shows a real failure for a 500", async () => {
    vi.spyOn(client, "getList").mockRejectedValue(new ApiError(500, "Internal error", null));
    vi.spyOn(client, "listListMembers").mockResolvedValue(page([]));
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load this list");
  });

  it("shows the service's 404 for a list that is not this organization's", async () => {
    vi.spyOn(client, "getList").mockRejectedValue(new ApiError(404, "No such list", null));
    vi.spyOn(client, "listListMembers").mockRejectedValue(new ApiError(404, "No such list", null));
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No such list");
  });

  it("shows Engage-not-active on a 402", async () => {
    const notActive = new ApiError(402, "engage is not active for this organization", null);
    vi.spyOn(client, "getList").mockRejectedValue(notActive);
    vi.spyOn(client, "listListMembers").mockRejectedValue(notActive);
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });
});

describe("populated", () => {
  it("names the list, counts its members and shows only masked addresses", async () => {
    mockRead([PRIYA, ARJUN]);
    renderPage();

    expect(await screen.findByRole("heading", { level: 1, name: "Enterprise accounts" })).toBeTruthy();
    expect(screen.getByText("Every enterprise-tier contact. · 2 members.")).toBeTruthy();
    expect(screen.getByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
    expect(screen.getByText("a•••@vaayu-freight.example.com")).toBeTruthy();
    expect(screen.getByRole("link", { name: "All lists" }).getAttribute("href")).toBe("/lists");
  });

  it("hides every write control for a caller with only the read grant", async () => {
    mockRead([PRIYA]);
    renderPage(["engage.audience.list.read:ORG"]);

    await screen.findByText("p•••@vaayu-logistics.example.com");
    expect(screen.queryByRole("button", { name: "Add contacts" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Import" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Remove/ })).toBeNull();
  });
});

describe("removing a member", () => {
  it("confirms on the page, says the contact stays, then removes only that one from this list", async () => {
    vi.spyOn(client, "getList").mockResolvedValue(LIST);
    vi.spyOn(client, "listListMembers")
      .mockResolvedValueOnce(page([PRIYA, ARJUN]))
      .mockResolvedValueOnce(page([ARJUN]));
    const spy = vi.spyOn(client, "removeListMembers").mockResolvedValue({ changed: 1, unknown: [] });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /^Remove Priya Raghavan/ }));
    const dialog = screen.getByRole("dialog");
    expect(dialog.textContent).toContain("stays a contact");
    expect(spy).not.toHaveBeenCalled();

    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(spy).toHaveBeenCalledWith(LIST.id, { contact_ids: [PRIYA.id] }));
    await waitFor(() => expect(screen.queryByText("p•••@vaayu-logistics.example.com")).toBeNull());
    expect(screen.getByText("a•••@vaayu-freight.example.com")).toBeTruthy();
  });

  it("keeps the dialog open with the service's sentence when the removal is refused", async () => {
    mockRead([PRIYA]);
    vi.spyOn(client, "removeListMembers").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.list.write", null),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /^Remove Priya Raghavan/ }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(await within(dialog).findByText("You do not have engage.audience.list.write")).toBeTruthy();
    expect(screen.getByText("p•••@vaayu-logistics.example.com")).toBeTruthy();
  });
});

describe("adding contacts from the picker", () => {
  it("searches the service, stages the choice, and adds everything staged in one call", async () => {
    mockRead([]);
    const search = vi.spyOn(client, "listContacts").mockResolvedValue(page([PRIYA, ARJUN]));
    const add = vi.spyOn(client, "addListMembers").mockResolvedValue({ changed: 2, unknown: [] });
    const user = userEvent.setup();
    renderPage();

    await user.click((await screen.findAllByRole("button", { name: "Add contacts" }))[0] as HTMLElement);
    const dialog = screen.getByRole("dialog");
    const submit = within(dialog).getByRole("button", { name: "Add contacts" });
    expect((submit as HTMLButtonElement).disabled).toBe(true);

    await user.type(within(dialog).getByRole("combobox", { name: "Find a contact" }), "ar");
    await user.click(await within(dialog).findByRole("option", { name: /Arjun Mehta/ }));

    // The search is the service's: the query went to `listContacts`, nothing was filtered here.
    expect(search).toHaveBeenCalledWith({ search: "ar", size: 20 });
    // The choice is staged, not submitted, and the dialog is still open.
    expect(within(dialog).getByText("Ready to add (1)")).toBeTruthy();
    expect(add).not.toHaveBeenCalled();

    await user.type(within(dialog).getByRole("combobox", { name: "Find a contact" }), "pr");
    await user.click(await within(dialog).findByRole("option", { name: /Priya Raghavan/ }));
    expect(within(dialog).getByText("Ready to add (2)")).toBeTruthy();

    await user.click(within(dialog).getByRole("button", { name: "Add 2 contacts" }));
    await waitFor(() =>
      expect(add).toHaveBeenCalledWith(LIST.id, { contact_ids: [ARJUN.id, PRIYA.id] }),
    );
  });

  it("lets a staged contact be taken back out before submitting", async () => {
    mockRead([]);
    vi.spyOn(client, "listContacts").mockResolvedValue(page([ARJUN]));
    const user = userEvent.setup();
    renderPage();

    await user.click((await screen.findAllByRole("button", { name: "Add contacts" }))[0] as HTMLElement);
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByRole("combobox", { name: "Find a contact" }), "ar");
    await user.click(await within(dialog).findByRole("option", { name: /Arjun Mehta/ }));

    await user.click(
      within(dialog).getByRole("button", { name: "Remove Arjun Mehta from what's staged to add" }),
    );
    expect(within(dialog).queryByText(/Ready to add/)).toBeNull();
  });

  it("keeps what was staged and shows the service's sentence when the add is refused", async () => {
    mockRead([]);
    vi.spyOn(client, "listContacts").mockResolvedValue(page([ARJUN]));
    vi.spyOn(client, "addListMembers").mockRejectedValue(new ApiError(500, "Internal error", null));
    const user = userEvent.setup();
    renderPage();

    await user.click((await screen.findAllByRole("button", { name: "Add contacts" }))[0] as HTMLElement);
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByRole("combobox", { name: "Find a contact" }), "ar");
    await user.click(await within(dialog).findByRole("option", { name: /Arjun Mehta/ }));
    await user.click(within(dialog).getByRole("button", { name: "Add 1 contact" }));

    expect(await within(dialog).findByText("Internal error")).toBeTruthy();
    expect(within(dialog).getByText("Ready to add (1)")).toBeTruthy();
  });
});

describe("importing into the list", () => {
  it("imports every row, then adds only the rows that imported to this list", async () => {
    mockRead([]);
    const result: ContactImportOut = {
      created: 1,
      updated: 1,
      invalid: 1,
      results: [
        { index: 0, status: "created", id: PRIYA.id, error: null },
        { index: 1, status: "updated", id: ARJUN.id, error: null },
        { index: 2, status: "invalid", id: null, error: "Not a valid email address" },
      ],
    };
    const imported = vi.spyOn(client, "importContacts").mockResolvedValue(result);
    const add = vi.spyOn(client, "addListMembers").mockResolvedValue({ changed: 2, unknown: [] });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = screen.getByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Contacts to import"),
      "priya@vaayu-logistics.example.com,Priya Raghavan\narjun@vaayu-freight.example.com,Arjun Mehta\nnot-an-email",
    );
    await user.click(within(dialog).getByRole("button", { name: "Import and add" }));

    await waitFor(() => expect(imported).toHaveBeenCalled());
    // The invalid row has no contact to add, so its null id never reaches the list.
    await waitFor(() => expect(add).toHaveBeenCalledWith(LIST.id, { contact_ids: [PRIYA.id, ARJUN.id] }));

    expect(await within(dialog).findByText("2 added to this list")).toBeTruthy();
    expect(within(dialog).getByText("1 created")).toBeTruthy();
    expect(within(dialog).getByText("1 invalid")).toBeTruthy();
    expect(within(dialog).getByText("Not a valid email address")).toBeTruthy();
    // Results are reported against the pasted line, never by echoing an address.
    expect(dialog.textContent).not.toContain("priya@vaayu-logistics");
  });

  it("does not call the list at all when nothing imported", async () => {
    mockRead([]);
    vi.spyOn(client, "importContacts").mockResolvedValue({
      created: 0,
      updated: 0,
      invalid: 1,
      results: [{ index: 0, status: "invalid", id: null, error: "Not a valid email address" }],
    });
    const add = vi.spyOn(client, "addListMembers");
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Contacts to import"), "not-an-email");
    await user.click(within(dialog).getByRole("button", { name: "Import and add" }));

    expect(await within(dialog).findByText("0 added to this list")).toBeTruthy();
    expect(add).not.toHaveBeenCalled();
  });

  it("keeps the pasted text and shows the service's sentence when the import is refused", async () => {
    mockRead([]);
    vi.spyOn(client, "importContacts").mockRejectedValue(new ApiError(500, "Internal error", null));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Contacts to import"), "priya@vaayu-logistics.example.com");
    await user.click(within(dialog).getByRole("button", { name: "Import and add" }));

    expect(await within(dialog).findByText("Internal error")).toBeTruthy();
    expect((within(dialog).getByLabelText("Contacts to import") as HTMLTextAreaElement).value).toBe(
      "priya@vaayu-logistics.example.com",
    );
  });
});
