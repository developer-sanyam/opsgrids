import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { ContactListOut, Page } from "@/api/types";
import { ContactListsPage } from "./ContactLists";

/**
 * Contact lists: every state the screen can be in, and the two writes with a consequence — a
 * delete, which is confirmed on the page itself rather than by `window.confirm`, and which the
 * service refuses with a `409` while a campaign is addressed to the list.
 */

const ENTERPRISE: ContactListOut = {
  id: "9a1b2c3d-4e5f-4061-8a7b-1c2d3e4f5061",
  name: "Enterprise accounts",
  description: "Every enterprise-tier contact.",
  member_count: 1_240,
  created_at: "2026-08-20T09:00:00Z",
  updated_at: "2026-09-18T10:12:00Z",
};

const TRIAL: ContactListOut = {
  id: "8b2c3d4e-5f60-4172-9b8c-2d3e4f506172",
  name: "Growth trial",
  description: "",
  member_count: 1,
  created_at: "2026-09-21T13:30:00Z",
  updated_at: "2026-09-21T13:30:00Z",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 25, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPage(grants = ["engage.audience.list.read:ORG", "engage.audience.list.write:ORG"]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter>
        <ContactListsPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

/** The header's action and the empty state's are the same control twice; either will do. */
async function firstButton(name: string): Promise<HTMLElement> {
  const [first] = await screen.findAllByRole("button", { name });
  if (first === undefined) throw new Error(`No button named ${name}`);
  return first;
}

describe("loading", () => {
  it("shows a skeleton while the read is in flight", () => {
    vi.spyOn(client, "listLists").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("empty", () => {
  it("says what a list is for and offers to create one", async () => {
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("No lists yet")).toBeTruthy();
    // The header action and the empty state's own action are the same control twice.
    expect(screen.getAllByRole("button", { name: "Create a list" }).length).toBe(2);
  });
});

describe("error", () => {
  it("shows a real failure for a 500", async () => {
    vi.spyOn(client, "listLists").mockRejectedValue(new ApiError(500, "Internal error", null));
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load your lists");
    expect(alert.textContent).toContain("Internal error");
  });

  it("shows Engage-not-active on a 402 rather than a failure", async () => {
    vi.spyOn(client, "listLists").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", null),
    );
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("keeps a 403 an error carrying the service's sentence", async () => {
    vi.spyOn(client, "listLists").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.list.read", null),
    );
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("You do not have engage.audience.list.read");
    expect(screen.queryByText("Engage isn't active for this organization yet")).toBeNull();
  });
});

describe("populated", () => {
  it("links each list to its members and shows the folded member count", async () => {
    vi.spyOn(client, "listLists").mockResolvedValue(page([ENTERPRISE, TRIAL]));
    renderPage();

    const link = await screen.findByRole("link", { name: "Enterprise accounts" });
    expect(link.getAttribute("href")).toBe(`/lists/${ENTERPRISE.id}`);
    expect(screen.getByText("1,240")).toBeTruthy();
    expect(screen.getByText("Every enterprise-tier contact.")).toBeTruthy();
    expect(screen.getByRole("columnheader", { name: "Members" })).toBeTruthy();
  });
});

describe("permission gating", () => {
  it("hides create, rename and delete for a caller with only the read grant", async () => {
    vi.spyOn(client, "listLists").mockResolvedValue(page([ENTERPRISE]));
    renderPage(["engage.audience.list.read:ORG"]);

    await screen.findByRole("link", { name: "Enterprise accounts" });
    expect(screen.queryByRole("button", { name: "Create a list" })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Rename/ })).toBeNull();
    expect(screen.queryByRole("button", { name: /^Delete/ })).toBeNull();
  });
});

describe("creating a list", () => {
  it("refuses an empty name inline, then submits the trimmed name and announces the result", async () => {
    vi.spyOn(client, "listLists")
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(page([ENTERPRISE]));
    const spy = vi.spyOn(client, "createList").mockResolvedValue(ENTERPRISE);
    const user = userEvent.setup();
    renderPage();

    await user.click(await firstButton("Create a list"));
    const dialog = screen.getByRole("dialog");

    await user.click(within(dialog).getByRole("button", { name: "Create list" }));
    expect(spy).not.toHaveBeenCalled();
    expect(within(dialog).getByText("Every list needs a name to pick it out by.")).toBeTruthy();

    await user.type(within(dialog).getByLabelText("Name"), "  Enterprise accounts  ");
    await user.click(within(dialog).getByRole("button", { name: "Create list" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith({ name: "Enterprise accounts", description: "" }));
    expect(await screen.findByRole("link", { name: "Enterprise accounts" })).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("keeps what was typed and shows the service's sentence when the create is refused", async () => {
    vi.spyOn(client, "listLists").mockResolvedValue(page([]));
    vi.spyOn(client, "createList").mockRejectedValue(
      new ApiError(409, "A list named Enterprise accounts already exists", null),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await firstButton("Create a list"));
    const dialog = screen.getByRole("dialog");
    await user.type(within(dialog).getByLabelText("Name"), "Enterprise accounts");
    await user.click(within(dialog).getByRole("button", { name: "Create list" }));

    expect(await within(dialog).findByText("A list named Enterprise accounts already exists")).toBeTruthy();
    expect((within(dialog).getByLabelText("Name") as HTMLInputElement).value).toBe("Enterprise accounts");
  });
});

describe("renaming a list", () => {
  it("seeds the form from the list and sends only what changed", async () => {
    vi.spyOn(client, "listLists").mockResolvedValue(page([ENTERPRISE]));
    const spy = vi.spyOn(client, "editList").mockResolvedValue({ ...ENTERPRISE, name: "Enterprise" });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Rename Enterprise accounts" }));
    const dialog = screen.getByRole("dialog");
    const name = within(dialog).getByLabelText("Name") as HTMLInputElement;
    expect(name.value).toBe("Enterprise accounts");

    await user.clear(name);
    await user.type(name, "Enterprise");
    await user.click(within(dialog).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(ENTERPRISE.id, { name: "Enterprise" }));
  });
});

describe("deleting a list", () => {
  it("asks on the page, never through window.confirm, and states what stays", async () => {
    const confirm = vi.spyOn(window, "confirm");
    vi.spyOn(client, "listLists").mockResolvedValue(page([ENTERPRISE]));
    const spy = vi.spyOn(client, "deleteList").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Delete Enterprise accounts" }));
    const dialog = screen.getByRole("dialog");
    expect(within(dialog).getByText("Delete this list?")).toBeTruthy();
    expect(dialog.textContent).toContain("1,240 contacts stay in your audience");
    expect(spy).not.toHaveBeenCalled();
    // Focus lands on the safe choice.
    expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Cancel" }));
    expect(confirm).not.toHaveBeenCalled();
  });

  it("deletes on confirm and reloads", async () => {
    vi.spyOn(client, "listLists")
      .mockResolvedValueOnce(page([ENTERPRISE]))
      .mockResolvedValueOnce(page([]));
    const spy = vi.spyOn(client, "deleteList").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Delete Enterprise accounts" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(ENTERPRISE.id));
    expect(await screen.findByText("No lists yet")).toBeTruthy();
  });

  it("shows the service's refusal, verbatim, when a campaign is addressed to the list", async () => {
    vi.spyOn(client, "listLists").mockResolvedValue(page([ENTERPRISE]));
    vi.spyOn(client, "deleteList").mockRejectedValue(
      new ApiError(409, "A campaign is addressed to this list; delete or re-target it first", null),
    );
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Delete Enterprise accounts" }));
    const dialog = screen.getByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(
      await within(dialog).findByText("A campaign is addressed to this list; delete or re-target it first"),
    ).toBeTruthy();
    // The list is still there: nothing was removed.
    expect(screen.getByRole("link", { name: "Enterprise accounts" })).toBeTruthy();
  });
});
