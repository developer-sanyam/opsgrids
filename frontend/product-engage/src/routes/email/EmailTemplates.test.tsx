import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";

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

const WRITE = "engage.email.template.write:ORG";

function CampaignStub() {
  const state = useLocation().state as { template: { subject: string; textBody: string } };
  return <p>{`New campaign: ${state.template.subject} / ${state.template.textBody}`}</p>;
}

function renderPage(grants: string[] = [WRITE]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={["/email/templates"]}>
        <Routes>
          <Route path="/email/templates" element={<EmailTemplatesPage />} />
          <Route path="/email/templates/new" element={<p>New template page</p>} />
          <Route path="/campaigns/new" element={<CampaignStub />} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loading", () => {
  it("says it is loading and shows placeholders rather than an empty list", () => {
    vi.spyOn(email, "listEmailTemplates").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(screen.getByText("Loading email templates")).toBeTruthy();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
    expect(screen.queryByRole("list")).toBeNull();
  });
});

describe("populated", () => {
  it("lists each template with its subject, variable count and a link to its editor", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED, WELCOME]));
    renderPage();

    const link = await screen.findByRole("link", { name: "order_shipped" });
    expect(link.getAttribute("href")).toBe("/email/templates/order_shipped");
    const row = link.closest("li");
    expect(row).not.toBeNull();
    expect(within(row as HTMLElement).getByText("Your order {{order_number}} has shipped")).toBeTruthy();
    expect(within(row as HTMLElement).getByText(/2 variables/)).toBeTruthy();
    expect(screen.getByText("Showing 2 of 2 templates")).toBeTruthy();
  });

  it("Use template opens a new campaign carrying the template's subject and body", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([WELCOME]));
    const user = userEvent.setup();
    renderPage(["engage.campaign.campaign.write:ORG"]);

    await user.click(await screen.findByRole("button", { name: "Use template welcome_email" }));
    expect(screen.getByText("New campaign: Welcome aboard / Glad you are here.")).toBeTruthy();
  });

  it("hides Use template from someone who may not draft campaigns, and still offers Edit", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([WELCOME]));
    renderPage([]);
    await screen.findByRole("button", { name: "Edit template welcome_email" });
    expect(screen.queryByRole("button", { name: /Use template/ })).toBeNull();
  });

  it("does not draw columns or tabs the service gives no data for", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED]));
    renderPage();
    await screen.findByRole("list", { name: "Email templates" });
    for (const absent of ["Used in", "Category", "Status", "Language"]) {
      expect(screen.queryByText(absent)).toBeNull();
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

  it("offers Create template to someone who may write templates", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED]));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Create template" }));
    expect(screen.getByText("New template page")).toBeTruthy();
  });

  it("hides Create template from someone who may not", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([SHIPPED]));
    renderPage(["engage.email.template.read:ORG"]);
    await screen.findByRole("link", { name: "order_shipped" });
    expect(screen.queryByRole("button", { name: "Create template" })).toBeNull();
  });
});

describe("empty", () => {
  it("says what a template is and offers to create one", async () => {
    vi.spyOn(email, "listEmailTemplates").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("No email templates yet")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Create template" }).length).toBe(2);
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

  it("shows the ordinary Engage not-active state, not an error, when Engage is not active", async () => {
    vi.spyOn(email, "listEmailTemplates").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", null),
    );
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});
