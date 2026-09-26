import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as email from "@/api/email";
import type { EmailTemplateOut } from "@/api/email";
import type { Page } from "@/api/types";
import { EmailTemplatesPage } from "./EmailTemplates";

/**
 * The email template list: every state, and search over what the service returned. There is no
 * category or status filter to test because the template payload has neither.
 */

const SHIPPED: EmailTemplateOut = {
  id: "order_shipped",
  channel: "email",
  subject: "Your order {{order_number}} has shipped",
  body: "Hi {{customer_name}}, it is on its way.",
  description: "Sent when a parcel leaves the warehouse",
  placeholders: ["customer_name", "order_number"],
  created_at: "2026-09-01T06:00:00Z",
  updated_at: "2026-09-24T06:00:00Z",
};
const WELCOME: EmailTemplateOut = {
  id: "welcome_email",
  channel: "email",
  subject: "Welcome aboard",
  body: "Glad you are here.",
  description: "",
  placeholders: [],
  created_at: "2026-09-02T06:00:00Z",
  updated_at: "2026-09-22T06:00:00Z",
};

function page(items: EmailTemplateOut[]): Page<EmailTemplateOut> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

const WRITE = "notification.delivery.template.write:ORG";

function renderPage(grants: string[] = [WRITE]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={["/email/templates"]}>
        <Routes>
          <Route path="/email/templates" element={<EmailTemplatesPage />} />
          <Route path="/email/templates/new" element={<p>New template page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loading", () => {
  it("says it is loading and shows placeholders rather than an empty table", () => {
    vi.spyOn(email, "listEmailTemplates").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(screen.getByText("Loading email templates")).toBeTruthy();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
    expect(screen.queryByRole("table")).toBeNull();
  });
});

describe("populated", () => {
  it("lists each template with its subject, variable count and a link to its editor", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED, WELCOME]));
    renderPage();

    const link = await screen.findByRole("link", { name: "order_shipped" });
    expect(link.getAttribute("href")).toBe("/email/templates/order_shipped");
    const row = link.closest("tr");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Your order {{order_number}} has shipped")).toBeTruthy();
    expect(within(row as HTMLElement).getByText("2")).toBeTruthy();
    expect(screen.getByText("Showing 2 of 2 templates")).toBeTruthy();
  });

  it("does not draw columns or tabs the service gives no data for", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED]));
    renderPage();
    await screen.findByRole("table");
    for (const absent of ["Used in", "Category", "Status", "Language"]) {
      expect(screen.queryByRole("columnheader", { name: absent })).toBeNull();
    }
    expect(screen.queryByRole("tablist")).toBeNull();
  });

  it("filters by name, subject or description as you type", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED, WELCOME]));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: "welcome_email" });
    await user.type(screen.getByRole("searchbox", { name: "Search templates" }), "warehouse");

    expect(screen.getByRole("link", { name: "order_shipped" })).toBeTruthy();
    expect(screen.queryByRole("link", { name: "welcome_email" })).toBeNull();
    expect(screen.getByText("Showing 1 of 2 templates")).toBeTruthy();
  });

  it("says nothing matched, and clears the search on request", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED]));
    const user = userEvent.setup();
    renderPage();

    await screen.findByRole("link", { name: "order_shipped" });
    await user.type(screen.getByRole("searchbox", { name: "Search templates" }), "zzz");
    expect(screen.getByText("No templates match that search")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Clear search" }));
    expect(screen.getByRole("link", { name: "order_shipped" })).toBeTruthy();
  });

  it("offers Create Template to someone who may write templates", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED]));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Create Template" }));
    expect(screen.getByText("New template page")).toBeTruthy();
  });

  it("hides Create Template from someone who may not", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED]));
    renderPage(["notification.delivery.template.read:ORG"]);
    await screen.findByRole("link", { name: "order_shipped" });
    expect(screen.queryByRole("button", { name: "Create Template" })).toBeNull();
  });
});

describe("empty", () => {
  it("says what a template is and offers to create one", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("No email templates yet")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Create Template" }).length).toBe(2);
  });
});

describe("error", () => {
  it("names the failure and reads again on retry", async () => {
    const read = vi
      .spyOn(email, "listEmailTemplates")
      .mockRejectedValueOnce(new ApiError(503, "Templates are warming up", null))
      .mockResolvedValueOnce(page([SHIPPED]));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Couldn't load your email templates")).toBeTruthy();
    expect(screen.getByText("Templates are warming up")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("link", { name: "order_shipped" })).toBeTruthy();
    expect(read).toHaveBeenCalledTimes(2);
  });

  it("shows the plan-gated state, not an error, when the product is not active", async () => {
    vi.spyOn(email, "listEmailTemplates").mockRejectedValue(new ApiError(402, "Not entitled", null));
    renderPage();
    expect(
      await screen.findByText("Email templates aren't part of this organization's plan yet"),
    ).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
