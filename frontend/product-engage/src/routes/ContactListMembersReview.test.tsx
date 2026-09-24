import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ContactImportOut, ContactListOut, ContactOut, Page } from "@/api/types";
import { ContactListsPage } from "./ContactLists";
import { ContactListMembersPage } from "./ContactListMembers";

/**
 * What the list screens do when half of a two-step action fails, and where a keyboard user is left
 * when the control they used disappears. The import is two facts — contacts created, then added —
 * and the screen must not let a refused second one read as a refused first.
 */

const LIST: ContactListOut = {
  id: "9a1b2c3d-4e5f-4061-8a7b-1c2d3e4f5061",
  name: "Enterprise accounts",
  description: "",
  member_count: 2,
  created_at: "2026-08-20T09:00:00Z",
  updated_at: "2026-09-18T10:12:00Z",
};

function contact(n: number, name = `Contact ${n}`): ContactOut {
  return {
    id: `c${n}`,
    email: `c•••${n}@vaayu-logistics.example.com`,
    name,
    attributes: {},
    created_at: "2026-08-29T09:18:02Z",
    updated_at: "2026-08-29T09:18:02Z",
  };
}

const A = contact(1, "Priya Raghavan");
const B = contact(2, "Arjun Mehta");
const C = contact(3, "Meera Nair");

const LIST_WRITE = ["engage.audience.list.read:ORG", "engage.audience.list.write:ORG"];
const ALL = [...LIST_WRITE, "engage.audience.contact.write:ORG"];

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function renderMembers(grants: readonly string[] = ALL) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={[`/lists/${LIST.id}`]}>
        <Routes>
          <Route path="/lists/:listId" element={<ContactListMembersPage />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function mockRead(members: ContactOut[]) {
  vi.spyOn(client, "getList").mockResolvedValue(LIST);
  vi.spyOn(client, "listListMembers").mockResolvedValue(page(members));
}

const IMPORTED: ContactImportOut = {
  created: 2,
  updated: 0,
  invalid: 0,
  results: [
    { index: 0, status: "created", id: "c1", error: null },
    { index: 1, status: "created", id: "c2", error: null },
  ],
};

describe("an import whose add to the list is refused", () => {
  async function runImport() {
    const user = userEvent.setup();
    renderMembers();
    await user.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = screen.getByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Contacts to import"),
      "priya@vaayu-logistics.example.com,Priya\narjun@vaayu-freight.example.com,Arjun",
    );
    await user.click(within(dialog).getByRole("button", { name: "Import and add" }));
    return dialog;
  }

  it("shows the import's outcome and says the rows were not added, with the service's reason", async () => {
    mockRead([]);
    const imported = vi.spyOn(client, "importContacts").mockResolvedValue(IMPORTED);
    vi.spyOn(client, "addListMembers").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.list.write", null),
    );
    const dialog = await runImport();

    // Not "That import didn't run": it did.
    expect(await within(dialog).findByText("The rows imported, but they were not added to this list")).toBeTruthy();
    expect(within(dialog).queryByText("That import didn't run")).toBeNull();
    expect(within(dialog).getByText(/You do not have engage.audience.list.write/)).toBeTruthy();
    expect(within(dialog).getByText("2 created")).toBeTruthy();
    expect(within(dialog).getByText("0 added to this list")).toBeTruthy();
    expect(imported).toHaveBeenCalledTimes(1);
  });

  it("announces that the contacts were not added, rather than a count that says they were", async () => {
    mockRead([]);
    vi.spyOn(client, "importContacts").mockResolvedValue(IMPORTED);
    vi.spyOn(client, "addListMembers").mockRejectedValue(new ApiError(500, "Internal error", null));
    await runImport();

    await waitFor(() => {
      const live = Array.from(document.querySelectorAll('[aria-live="polite"]')).map((n) => n.textContent);
      expect(live).toContain(
        "Import finished: 2 created, 0 updated, 0 invalid. The contacts were not added to this list.",
      );
    });
  });

  it("still calls a refused import a refused import, and keeps the paste", async () => {
    mockRead([]);
    vi.spyOn(client, "importContacts").mockRejectedValue(new ApiError(500, "Internal error", null));
    const add = vi.spyOn(client, "addListMembers");
    const dialog = await runImport();

    expect(await within(dialog).findByText("That import didn't run")).toBeTruthy();
    expect(add).not.toHaveBeenCalled();
    expect((within(dialog).getByLabelText("Contacts to import") as HTMLTextAreaElement).value).toContain("priya@");
  });
});

describe("who is offered the import", () => {
  it("needs both grants: an import creates contacts and then adds them", async () => {
    mockRead([A]);
    renderMembers(LIST_WRITE);

    await screen.findByText("c•••1@vaayu-logistics.example.com");
    expect(screen.queryByRole("button", { name: "Import" })).toBeNull();
    // Adding existing contacts needs only the list grant.
    expect(screen.getByRole("button", { name: "Add contacts" })).toBeTruthy();
  });

  it("is offered to someone who holds both", async () => {
    mockRead([A]);
    renderMembers(ALL);
    expect(await screen.findByRole("button", { name: "Import" })).toBeTruthy();
  });
});

describe("where focus is while the import dialog is up", () => {
  /** Runs an import to its results screen and hands back the dialog and the user. */
  async function importToResults(addRefused: boolean) {
    mockRead([]);
    vi.spyOn(client, "importContacts").mockResolvedValue(IMPORTED);
    if (addRefused) {
      vi.spyOn(client, "addListMembers").mockRejectedValue(
        new ApiError(403, "You do not have engage.audience.list.write", null),
      );
    } else {
      vi.spyOn(client, "addListMembers").mockResolvedValue({ changed: 2, unknown: [] });
    }
    const user = userEvent.setup();
    renderMembers();
    await user.click(await screen.findByRole("button", { name: "Import" }));
    const dialog = screen.getByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Contacts to import"),
      "priya@vaayu-logistics.example.com,Priya\narjun@vaayu-freight.example.com,Arjun",
    );
    await user.click(within(dialog).getByRole("button", { name: "Import and add" }));
    await within(dialog).findByText("2 created");
    return { user, dialog };
  }

  it.each([
    ["the rows were added", false],
    ["the add to the list was refused", true],
  ])("stays inside the dialog, on its results, when %s", async (_case, addRefused) => {
    const { dialog } = await importToResults(addRefused);

    // The page behind it re-read itself, and did not take focus with it: the heading behind an
    // aria-modal dialog is focus nobody can see.
    await waitFor(() => expect(document.activeElement?.closest('[role="dialog"]')).not.toBeNull());
    expect(document.activeElement).toBe(within(dialog).getByRole("heading", { name: "Import results" }));
    expect(screen.getByRole("heading", { level: 1 }).contains(document.activeElement)).toBe(false);
  });

  it("hands focus back to the Import button that opened it once Done closes the dialog", async () => {
    const { user, dialog } = await importToResults(false);

    await user.click(within(dialog).getByRole("button", { name: "Done" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(document.activeElement).toBe(screen.getByRole("button", { name: "Import" }));
  });

  it("still moves focus to the page heading for an action that has no dialog left open", async () => {
    vi.spyOn(client, "getList").mockResolvedValue(LIST);
    vi.spyOn(client, "listListMembers").mockResolvedValueOnce(page([A])).mockResolvedValue(page([]));
    vi.spyOn(client, "removeListMembers").mockResolvedValue({ changed: 1, unknown: [] });
    const user = userEvent.setup();
    renderMembers();

    await user.click(await screen.findByRole("button", { name: /^Remove Priya Raghavan/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1 })));
  });
});

describe("taking a staged contact back out of the picker", () => {
  async function stage(names: string[]) {
    const user = userEvent.setup();
    mockRead([]);
    vi.spyOn(client, "listContacts").mockResolvedValue(page([A, B, C]));
    renderMembers();
    await user.click((await screen.findAllByRole("button", { name: "Add contacts" }))[0] as HTMLElement);
    const dialog = screen.getByRole("dialog");
    for (const name of names) {
      await user.type(within(dialog).getByRole("combobox", { name: "Find a contact" }), name.slice(0, 2));
      await user.click(await within(dialog).findByRole("option", { name: new RegExp(name) }));
    }
    return { user, dialog };
  }

  it("moves focus to the next chip's remove button", async () => {
    const { user, dialog } = await stage(["Priya Raghavan", "Arjun Mehta", "Meera Nair"]);

    await user.click(within(dialog).getByRole("button", { name: "Remove Priya Raghavan from what's staged to add" }));

    await waitFor(() =>
      expect(document.activeElement).toBe(
        within(dialog).getByRole("button", { name: "Remove Arjun Mehta from what's staged to add" }),
      ),
    );
  });

  it("moves focus to the previous chip when the last one is removed", async () => {
    const { user, dialog } = await stage(["Priya Raghavan", "Arjun Mehta"]);

    await user.click(within(dialog).getByRole("button", { name: "Remove Arjun Mehta from what's staged to add" }));

    await waitFor(() =>
      expect(document.activeElement).toBe(
        within(dialog).getByRole("button", { name: "Remove Priya Raghavan from what's staged to add" }),
      ),
    );
  });

  it("returns focus to the search box when nothing is left, so the dialog's trap is not abandoned", async () => {
    const { user, dialog } = await stage(["Priya Raghavan"]);

    await user.click(within(dialog).getByRole("button", { name: "Remove Priya Raghavan from what's staged to add" }));

    await waitFor(() =>
      expect(document.activeElement).toBe(within(dialog).getByRole("combobox", { name: "Find a contact" })),
    );
    expect(dialog.contains(document.activeElement)).toBe(true);
  });

  it("lets a long name shrink inside its chip instead of pushing the dialog wider than a phone", async () => {
    const long = contact(9, "An extraordinarily long display name that would otherwise overflow a phone");
    const user = userEvent.setup();
    mockRead([]);
    vi.spyOn(client, "listContacts").mockResolvedValue(page([long]));
    renderMembers();
    await user.click((await screen.findAllByRole("button", { name: "Add contacts" }))[0] as HTMLElement);
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByRole("combobox", { name: "Find a contact" }), "An");
    await user.click(await within(dialog).findByRole("option", { name: /extraordinarily long/ }));

    const label = within(dialog).getByText(/extraordinarily long display name/, { selector: "span.truncate" });
    const chip = label.parentElement as HTMLElement;
    expect(label.className).toContain("min-w-0");
    expect(chip.className).toContain("max-w-full");
    expect(chip.closest("li")?.className).toContain("max-w-full");
  });
});

describe("where focus goes when the row just used is replaced", () => {
  it("moves to the page heading after removing a member", async () => {
    vi.spyOn(client, "getList").mockResolvedValue(LIST);
    vi.spyOn(client, "listListMembers").mockResolvedValueOnce(page([A, B])).mockResolvedValue(page([B]));
    vi.spyOn(client, "removeListMembers").mockResolvedValue({ changed: 1, unknown: [] });
    const user = userEvent.setup();
    renderMembers();

    await user.click(await screen.findByRole("button", { name: /^Remove Priya Raghavan/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Remove" }));

    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 }).contains(document.activeElement)).toBe(true),
    );
  });

  it("moves to the page heading after deleting a list", async () => {
    vi.spyOn(client, "listLists")
      .mockResolvedValueOnce(page([LIST]))
      .mockResolvedValue(page([]));
    vi.spyOn(client, "deleteList").mockResolvedValue(undefined);
    const user = userEvent.setup();
    render(
      <PermissionProvider grants={LIST_WRITE}>
        <MemoryRouter>
          <ContactListsPage />
        </MemoryRouter>
      </PermissionProvider>,
    );

    await user.click(await screen.findByRole("button", { name: "Delete Enterprise accounts" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));

    await screen.findByText("No lists yet");
    await waitFor(() =>
      expect(screen.getByRole("heading", { level: 1 }).contains(document.activeElement)).toBe(true),
    );
  });
});
