import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Page, SuppressionOut } from "@/api/types";
import { SuppressionsPage } from "./Suppressions";

/**
 * `reason: "manual"` is the one this screen ever writes or lifts. The other three are reports
 * this screen only ever reads, and offering "Remove" for them would be a UI promise the server
 * refuses regardless — pinned here as much as the masked-address guarantee is on Contacts.
 */

const MANUAL: SuppressionOut = {
  id: "a5e4b6c8-5f7b-4ebf-ac5d-6b0f3a8e9c54",
  email: "s•••@vaayu-logistics.example.com",
  reason: "manual",
  created_at: "2026-09-19T10:05:19Z",
};

const MANUAL2: SuppressionOut = {
  id: "f4d3a5b7-9e6c-4dad-bb0e-7c1f2a9d8e43",
  email: "m•••@vaayu-logistics.example.com",
  reason: "manual",
  created_at: "2026-09-20T09:00:00Z",
};

const BOUNCED: SuppressionOut = {
  id: "e3c2f4a6-7d5f-4c9e-8a3b-4f8d1e6c7a32",
  email: "b•••@vaayu-logistics.example.com",
  reason: "bounced",
  created_at: "2026-09-05T11:30:00Z",
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
    <PermissionProvider grants={["engage.audience.suppression.write:ORG"]}>
      <MemoryRouter>
        <SuppressionsPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

describe("loading", () => {
  it("shows a skeleton while the read is in flight", () => {
    vi.spyOn(client, "listSuppressions").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("empty", () => {
  it("says what to do next rather than showing nothing", async () => {
    vi.spyOn(client, "listSuppressions").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("No suppressions yet")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Suppress an address" })).toBeTruthy();
  });
});

describe("error", () => {
  it("shows a real failure for anything other than a 402", async () => {
    vi.spyOn(client, "listSuppressions").mockRejectedValue(
      new ApiError(500, "Internal error", null),
    );
    renderPage();
    expect(await screen.findByText("Couldn't load your suppressions")).toBeTruthy();
  });

  it("shows Engage-not-active on a 402", async () => {
    vi.spyOn(client, "listSuppressions").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", null),
    );
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });

  it("keeps a 403 an error, not the not-active state", async () => {
    vi.spyOn(client, "listSuppressions").mockRejectedValue(
      new ApiError(403, "You do not have engage.audience.suppression.read", null),
    );
    renderPage();
    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load your suppressions");
    expect(screen.queryByText("Engage isn't active for this organization yet")).toBeNull();
  });
});

describe("the manual / automatic distinction", () => {
  it("offers Remove only for a manual suppression", async () => {
    vi.spyOn(client, "listSuppressions").mockResolvedValue(page([MANUAL, BOUNCED]));
    renderPage();

    await screen.findByText("Added by hand");
    const manualRow = screen.getByText("s•••@vaayu-logistics.example.com").closest("tr");
    const bouncedRow = screen.getByText("b•••@vaayu-logistics.example.com").closest("tr");
    if (manualRow === null || bouncedRow === null) throw new Error("row not found");

    expect(within(manualRow).getByRole("button", { name: /Remove/ })).toBeTruthy();
    expect(within(bouncedRow).queryByRole("button", { name: /Remove/ })).toBeNull();
    expect(within(bouncedRow).getByText("Reported automatically")).toBeTruthy();
  });
});

describe("filtering by reason", () => {
  it("re-reads with the chosen reason", async () => {
    const spy = vi.spyOn(client, "listSuppressions").mockResolvedValue(page([MANUAL]));
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Added by hand");

    await user.selectOptions(screen.getByLabelText("Filter by reason"), "bounced");

    await waitFor(() =>
      expect(spy).toHaveBeenLastCalledWith(
        expect.objectContaining({ reason: "bounced", page: 1 }),
      ),
    );
  });
});

describe("suppressing an address", () => {
  it("adds a manual suppression and shows it on the list", async () => {
    vi.spyOn(client, "listSuppressions")
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(page([MANUAL]));
    const spy = vi.spyOn(client, "addSuppression").mockResolvedValue(MANUAL);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Suppress an address" }));
    const dialog = screen.getByRole("dialog");
    await user.type(
      within(dialog).getByLabelText("Email"),
      "someone.real@vaayu-logistics.example.com",
    );
    await user.click(within(dialog).getByRole("button", { name: "Suppress" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith({ email: "someone.real@vaayu-logistics.example.com" }));
    // Never the typed plaintext, only the server's mask.
    expect(screen.queryByText(/someone\.real/)).toBeNull();
    expect(await screen.findByText("s•••@vaayu-logistics.example.com")).toBeTruthy();
  });
});

describe("permission gating", () => {
  it("hides Suppress and Remove for a viewer with no suppression.write grant", async () => {
    vi.spyOn(client, "listSuppressions").mockResolvedValue(page([MANUAL, BOUNCED]));
    render(
      <PermissionProvider grants={["engage.audience.suppression.read:ORG"]}>
        <MemoryRouter>
          <SuppressionsPage />
        </MemoryRouter>
      </PermissionProvider>,
    );

    await screen.findByText("Added by hand");
    expect(screen.queryByRole("button", { name: "Suppress an address" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Remove/ })).toBeNull();
    // Still readable — hiding a write control never hides the data itself.
    expect(screen.getByText("s•••@vaayu-logistics.example.com")).toBeTruthy();
    expect(screen.getByText("b•••@vaayu-logistics.example.com")).toBeTruthy();
  });
});

describe("pagination after a remove empties a page", () => {
  it("steps back a page instead of showing the first-run empty state", async () => {
    const spy = vi
      .spyOn(client, "listSuppressions")
      .mockResolvedValueOnce({ items: [MANUAL], total: 2, page: 1, size: 1, pages: 2 })
      .mockResolvedValueOnce({ items: [MANUAL2], total: 2, page: 2, size: 1, pages: 2 })
      .mockResolvedValueOnce({ items: [], total: 1, page: 2, size: 1, pages: 1 })
      .mockResolvedValueOnce({ items: [MANUAL], total: 1, page: 1, size: 1, pages: 1 });
    vi.spyOn(client, "removeSuppression").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("s•••@vaayu-logistics.example.com");
    await user.click(await screen.findByRole("button", { name: "Next page" }));
    await screen.findByText("m•••@vaayu-logistics.example.com");

    await user.click(screen.getByRole("button", { name: /Remove/ }));
    await user.click(screen.getByRole("button", { name: "Remove" }));

    await screen.findByText("s•••@vaayu-logistics.example.com");
    expect(screen.queryByText("No suppressions yet")).toBeNull();
    await waitFor(() =>
      expect(spy).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 })),
    );
  });
});

describe("removing a suppression", () => {
  it("lifts it once confirmed", async () => {
    vi.spyOn(client, "listSuppressions")
      .mockResolvedValueOnce(page([MANUAL]))
      .mockResolvedValueOnce(page([]));
    const spy = vi.spyOn(client, "removeSuppression").mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: /Remove/ }));
    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith(MANUAL.id));
    expect(await screen.findByText("No suppressions yet")).toBeTruthy();
  });
});
