import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ContactOut, Page } from "@/api/types";
import { ContactsPage } from "./Contacts";

const ALICE: ContactOut = {
  id: "8d3f0a11-6b2e-4c7a-9d51-2f8e6a0c4b17",
  email: "alice@example.com",
  name: "Alice",
  attributes: {},
  created_at: "2026-09-01T09:00:00Z",
  updated_at: "2026-09-01T09:00:00Z",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

async function mount(contacts: ContactOut[]) {
  vi.spyOn(client, "listContacts").mockResolvedValue(page(contacts));
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <ContactsPage />
    </MemoryRouter>,
  );
  if (contacts.length > 0) {
    await screen.findByRole("table");
  } else {
    await screen.findByText("No contacts yet");
  }
  return user;
}

describe("loading and empty states", () => {
  it("shows an empty state with a way to add the first contact", async () => {
    await mount([]);
    expect(screen.getByText("No contacts yet")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Add a contact" })).toBeTruthy();
  });

  it("renders a table once contacts exist", async () => {
    await mount([ALICE]);
    expect(screen.getByRole("rowheader", { name: /alice@example\.com/ })).toBeTruthy();
  });

  it("shows the real failure message on a load error, not a blank screen", async () => {
    vi.spyOn(client, "listContacts").mockRejectedValue(new ApiError(500, "boom", null));
    render(
      <MemoryRouter>
        <ContactsPage />
      </MemoryRouter>,
    );
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load your contacts");
    expect(alert.textContent).toContain("boom");
  });
});

describe("adding a contact", () => {
  it("adds one and reloads the list", async () => {
    const user = await mount([]);
    vi.spyOn(client, "addContact").mockResolvedValue(ALICE);
    vi.spyOn(client, "listContacts").mockResolvedValue(page([ALICE]));

    await user.click(screen.getByRole("button", { name: "Add a contact" }));
    const dialog = screen.getByRole("dialog");
    await user.type(await within(dialog).findByLabelText("Email"), "alice@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Add contact" }));

    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(await screen.findByRole("rowheader", { name: /alice@example\.com/ })).toBeTruthy();
  });

  it("keeps the typed values and shows the service's refusal on a duplicate", async () => {
    const user = await mount([ALICE]);
    vi.spyOn(client, "addContact").mockRejectedValue(
      new ApiError(409, "A contact already exists for alice@example.com", null),
    );

    await user.click(screen.getByRole("button", { name: "Add a contact" }));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Email"), "alice@example.com");
    await user.click(within(dialog).getByRole("button", { name: "Add contact" }));

    expect(
      await screen.findByText("A contact already exists for alice@example.com"),
    ).toBeTruthy();
    expect((within(dialog).getByLabelText("Email") as HTMLInputElement).value).toBe(
      "alice@example.com",
    );
  });
});

describe("removing a contact", () => {
  it("asks for confirmation and removes on confirm", async () => {
    const user = await mount([ALICE]);
    const spy = vi.spyOn(client, "deleteContact").mockResolvedValue(undefined);
    vi.spyOn(client, "listContacts").mockResolvedValue(page([]));

    await user.click(screen.getByRole("button", { name: /Remove/ }));
    expect(screen.getByText("Remove this contact?")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(ALICE.id));
    expect(await screen.findByText("No contacts yet")).toBeTruthy();
  });
});

describe("bulk import", () => {
  it("shows the per-row report after importing", async () => {
    const user = await mount([]);
    vi.spyOn(client, "importContacts").mockResolvedValue({
      total: 2,
      created: 1,
      updated: 0,
      errors: 1,
      rows: [
        { row: 0, email: "ok@example.com", status: "created", error: null },
        { row: 1, email: "bad", status: "error", error: "invalid email" },
      ],
    });

    await user.click(screen.getByRole("button", { name: "Import" }));
    const dialog = screen.getByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Contacts"),
      "ok@example.com\nbad",
    );
    await user.click(within(dialog).getByRole("button", { name: /Import 2 contacts/ }));

    expect(await within(dialog).findByText("1 created")).toBeTruthy();
    expect(within(dialog).getByText("1 failed")).toBeTruthy();
    expect(within(dialog).getByText("invalid email")).toBeTruthy();
  });
});
