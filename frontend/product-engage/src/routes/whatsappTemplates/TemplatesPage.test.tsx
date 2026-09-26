import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { MockInstance } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as api from "@/api/whatsappTemplates";
import type { TemplateListQuery } from "@/api/whatsappTemplates";
import { syncSummary, tabFor } from "./filters";
import { TemplatesPage } from "./TemplatesPage";
import { APPROVED, DETAIL, PENDING, PREVIEW, READ_AND_WRITE, REJECTED, page } from "./test-fixtures";

afterEach(() => {
  vi.restoreAllMocks();
});

function stubPanel() {
  const get = vi.spyOn(api, "getTemplate").mockResolvedValue(DETAIL);
  const preview = vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
  return { get, preview };
}

function renderPage(grants = READ_AND_WRITE) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter>
        <TemplatesPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function lastQuery(spy: MockInstance<typeof api.listTemplates>): TemplateListQuery {
  return spy.mock.calls.at(-1)?.[0] ?? {};
}

describe("tabs and filters", () => {
  it("lists eight tabs and starts on All Templates", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    renderPage();
    const tabs = await screen.findAllByRole("tab");
    expect(tabs.map((tab) => tab.textContent)).toEqual([
      "All Templates",
      "Marketing",
      "Utility",
      "Authentication",
      "Draft",
      "Pending",
      "Approved",
      "Rejected",
    ]);
    expect(screen.getByRole("tab", { name: "All Templates" }).getAttribute("aria-selected")).toBe("true");
  });

  it("a category tab filters by category and a status tab by status, each clearing the other", async () => {
    const list = vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("table");

    await user.click(screen.getByRole("tab", { name: "Marketing" }));
    await waitFor(() => expect(lastQuery(list)).toMatchObject({ category: "marketing", page: 1 }));
    expect(lastQuery(list).status).toBeUndefined();
    expect(screen.getByRole("tab", { name: "Marketing" }).getAttribute("aria-selected")).toBe("true");

    await user.click(screen.getByRole("tab", { name: "Pending" }));
    await waitFor(() => expect(lastQuery(list)).toMatchObject({ status: "pending" }));
    expect(lastQuery(list).category).toBeUndefined();

    await user.click(screen.getByRole("tab", { name: "All Templates" }));
    await waitFor(() => {
      expect(lastQuery(list).category).toBeUndefined();
      expect(lastQuery(list).status).toBeUndefined();
    });
  });

  it("the selects combine both filters and leave no tab selected", async () => {
    const list = vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("table");

    await user.selectOptions(screen.getByLabelText("Category"), "marketing");
    await user.selectOptions(screen.getByLabelText("Status"), "approved");
    await waitFor(() =>
      expect(lastQuery(list)).toMatchObject({ category: "marketing", status: "approved" }),
    );
    for (const tab of screen.getAllByRole("tab")) {
      expect(tab.getAttribute("aria-selected")).toBe("false");
    }
  });

  it("tabFor names the tab a filter pair stands for", () => {
    expect(tabFor({ category: "", status: "" })).toBe("all");
    expect(tabFor({ category: "utility", status: "" })).toBe("utility");
    expect(tabFor({ category: "", status: "rejected" })).toBe("rejected");
    expect(tabFor({ category: "utility", status: "rejected" })).toBe("");
  });

  it("searches after typing pauses, from page 1", async () => {
    const list = vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("table");

    await user.type(screen.getByRole("searchbox", { name: "Search templates" }), "ship");
    await waitFor(() => expect(lastQuery(list)).toMatchObject({ q: "ship", page: 1 }), { timeout: 2000 });
  });

  it("says nothing matches, and clears the filters, when a filter finds nothing", async () => {
    const list = vi
      .spyOn(api, "listTemplates")
      .mockResolvedValueOnce(page([APPROVED]))
      .mockResolvedValueOnce(page([]))
      .mockResolvedValue(page([APPROVED]));
    stubPanel();
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("table");

    await user.click(screen.getByRole("tab", { name: "Rejected" }));
    expect(await screen.findByText("No templates match")).toBeTruthy();
    expect(screen.queryByText("No WhatsApp templates yet")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await screen.findByRole("table");
    expect(lastQuery(list).status).toBeUndefined();
    expect(screen.getByRole("tab", { name: "All Templates" }).getAttribute("aria-selected")).toBe("true");
  });

  it("asks for the next page when it is chosen", async () => {
    const list = vi
      .spyOn(api, "listTemplates")
      .mockResolvedValue(page([APPROVED], { total: 40, pages: 2, size: 20 }));
    stubPanel();
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "Next page" }));
    await waitFor(() => expect(lastQuery(list)).toMatchObject({ page: 2 }));
  });
});

describe("sync from Meta", () => {
  it("syncs, raises a result toast and re-reads the list", async () => {
    const list = vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    const sync = vi
      .spyOn(api, "syncTemplates")
      .mockResolvedValue({ synced: 12, created: 3, updated: 8, removed: 1 });
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "Sync from Meta" }));

    await waitFor(() => expect(sync).toHaveBeenCalledTimes(1));
    expect((await screen.findAllByText("12 templates synced from Meta")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("3 new, 8 updated, 1 removed.").length).toBeGreaterThan(0);
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
  });

  it("disables the button while the sync runs, so it cannot be started twice", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    let finish: (value: api.TemplateSyncResult) => void = () => {};
    const sync = vi.spyOn(api, "syncTemplates").mockReturnValue(
      new Promise((resolve) => {
        finish = resolve;
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "Sync from Meta" }));
    const busy = await screen.findByRole("button", { name: "Syncing…" });
    expect((busy as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(busy);
    expect(sync).toHaveBeenCalledTimes(1);

    finish({ synced: 1, created: 0, updated: 1, removed: 0 });
    expect((await screen.findAllByText("1 template synced from Meta")).length).toBeGreaterThan(0);
  });

  it("says why it failed, and leaves the list as it was", async () => {
    const list = vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    vi.spyOn(api, "syncTemplates").mockRejectedValue(
      new ApiError(502, "Meta refused the request: token expired", null),
    );
    const user = userEvent.setup();
    renderPage();
    await screen.findByRole("table");

    await user.click(screen.getByRole("button", { name: "Sync from Meta" }));

    expect((await screen.findAllByText("Couldn't sync from Meta")).length).toBeGreaterThan(0);
    expect(screen.getAllByText("Meta refused the request: token expired").length).toBeGreaterThan(0);
    expect(list).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("link", { name: "order_shipped" })).toBeTruthy();
  });

  it("summarises a result in the singular and plural", () => {
    expect(syncSummary({ synced: 1, created: 1, updated: 0, removed: 0 }).title).toBe(
      "1 template synced from Meta",
    );
    expect(syncSummary({ synced: 0, created: 0, updated: 0, removed: 0 }).title).toBe(
      "0 templates synced from Meta",
    );
  });
});

describe("preview panel", () => {
  it("previews the first template with its variables and approval history", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED, PENDING]));
    const { get, preview } = stubPanel();
    renderPage();

    expect(await screen.findByText("Approval history")).toBeTruthy();
    expect(get).toHaveBeenCalledWith("order_shipped");
    expect(preview).toHaveBeenCalledWith("order_shipped", { "1": "Rahul", "2": "#ORD-2938" });

    expect(screen.getByText("Hello Rahul, your order #ORD-2938 has shipped.")).toBeTruthy();
    expect(screen.getByText("Track order")).toBeTruthy();
    expect(screen.getByText("customer_name")).toBeTruthy();
    expect(screen.getByText("order_number")).toBeTruthy();
    expect(screen.getByText(/Example: Rahul/)).toBeTruthy();
    expect(screen.getByText("Submitted to Meta")).toBeTruthy();
    expect(screen.getByText("Approved by Meta")).toBeTruthy();
  });

  it("switches to another row when its Preview is chosen", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED, PENDING]));
    const { get } = stubPanel();
    const user = userEvent.setup();
    renderPage();
    await screen.findByText("Approval history");

    await user.click(screen.getByRole("button", { name: "Preview feedback_request" }));
    await waitFor(() => expect(get).toHaveBeenLastCalledWith("feedback_request"));
    expect(screen.getByRole("button", { name: "Preview feedback_request" }).getAttribute("aria-pressed")).toBe("true");
  });

  it("shows Meta's reason when a template was rejected", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([REJECTED]));
    vi.spyOn(api, "getTemplate").mockResolvedValue({ ...DETAIL, ...REJECTED, history: [] });
    vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
    renderPage();

    expect(await screen.findByText("Meta rejected this template")).toBeTruthy();
    expect(screen.getByText(/asks for payment details/)).toBeTruthy();
    expect(screen.getByText("No status changes recorded yet.")).toBeTruthy();
  });

  it("falls back to the row's own text when only the render fails", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    vi.spyOn(api, "getTemplate").mockResolvedValue(DETAIL);
    vi.spyOn(api, "previewTemplate").mockRejectedValue(new ApiError(500, "boom", null));
    renderPage();

    expect(await screen.findByText("Hello Rahul, your order #ORD-2938 has shipped.")).toBeTruthy();
  });

  it("offers a retry when the template itself cannot be read", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    const get = vi
      .spyOn(api, "getTemplate")
      .mockRejectedValueOnce(new ApiError(500, "Internal server error", null))
      .mockResolvedValue(DETAIL);
    vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Couldn't load this template")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("Approval history")).toBeTruthy();
    expect(get).toHaveBeenCalledTimes(2);
  });
});
