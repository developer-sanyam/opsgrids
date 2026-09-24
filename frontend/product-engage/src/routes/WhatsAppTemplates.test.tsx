import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Page, ProviderTemplateOut } from "@/api/types";
import { WhatsAppTemplatesPage } from "./WhatsAppTemplates";

const READ_AND_WRITE = ["engage.whatsapp.template.read:ORG", "engage.whatsapp.template.write:ORG"];

const APPROVED: ProviderTemplateOut = {
  id: "delivery_confirmed",
  channel: "whatsapp",
  provider_name: "meta",
  provider_template_id: "1187364502983471",
  language: "en",
  category: "utility",
  parameter_format: "positional",
  status: "approved",
  status_reason: null,
  status_checked_at: "2026-09-21T04:12:09Z",
  variables: [],
  preview_text: "Hi {{1}}, your shipment is out for delivery today.",
  sendable: true,
  created_at: "2026-08-30T09:14:22Z",
  updated_at: "2026-09-21T04:12:09Z",
};

const PENDING: ProviderTemplateOut = {
  ...APPROVED,
  id: "payment_reminder",
  provider_template_id: null,
  status: "pending",
  sendable: false,
  preview_text: "Reminder: {{1}} is due on your account.",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

function renderPage(grants = READ_AND_WRITE) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter>
        <WhatsAppTemplatesPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

describe("loading", () => {
  it("shows a skeleton while the read is in flight", () => {
    vi.spyOn(client, "listWhatsAppTemplates").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("empty", () => {
  it("says what to do next rather than showing nothing", async () => {
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("No WhatsApp templates yet")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Submit a template" })).toBeTruthy();
  });
});

describe("error", () => {
  it("shows a real failure for anything other than a 402", async () => {
    vi.spyOn(client, "listWhatsAppTemplates").mockRejectedValue(
      new ApiError(500, "Internal server error", null),
    );
    renderPage();
    expect(await screen.findByText("Couldn't load your WhatsApp templates")).toBeTruthy();
  });
});

describe("402 — Engage not active", () => {
  it("shows the ordinary Engage not-active state, now that this read is an Engage route", async () => {
    vi.spyOn(client, "listWhatsAppTemplates").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", null),
    );
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });
});

describe("403 — missing the engage permission", () => {
  it("shows the service's own sentence naming the engage permission, not the old notification one", async () => {
    vi.spyOn(client, "listWhatsAppTemplates").mockRejectedValue(
      new ApiError(403, "Missing permission: engage.whatsapp.template.read", null),
    );
    renderPage();
    expect(
      await screen.findByText("Missing permission: engage.whatsapp.template.read"),
    ).toBeTruthy();
  });
});

describe("permission gating", () => {
  it("hides submitting a template for a caller with only the read grant", async () => {
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(page([APPROVED]));
    renderPage(["engage.whatsapp.template.read:ORG"]);

    await screen.findByText("delivery_confirmed");
    expect(screen.queryByRole("button", { name: /Submit a template/ })).toBeNull();
  });
});

describe("populated", () => {
  it("renders the real status Meta gave each template, never a fabricated one", async () => {
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(page([APPROVED, PENDING]));
    renderPage();

    expect(await screen.findByText("delivery_confirmed")).toBeTruthy();
    expect(screen.getByText("Approved")).toBeTruthy();
    expect(screen.getByText("Can send")).toBeTruthy();

    expect(screen.getByText("payment_reminder")).toBeTruthy();
    expect(screen.getByText("Pending Meta review")).toBeTruthy();
    expect(screen.getByText("Cannot send yet")).toBeTruthy();
  });
});

describe("submitting a template", () => {
  it("stages a draft submission and never claims it was approved", async () => {
    const draft: ProviderTemplateOut = {
      ...APPROVED,
      id: "order_shipped",
      status: "draft",
      sendable: false,
      status_checked_at: null,
    };
    // First read is empty (nothing submitted yet); the screen re-reads the list after a
    // successful submit, and the second read is what should actually appear on it.
    vi.spyOn(client, "listWhatsAppTemplates")
      .mockResolvedValueOnce(page([]))
      .mockResolvedValueOnce(page([draft]));
    const spy = vi.spyOn(client, "submitWhatsAppTemplate").mockResolvedValue(draft);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Submit a template" }));
    await user.type(screen.getByLabelText("Template code"), "order_shipped");
    // `fireEvent.change` rather than `user.type`: curly braces are userEvent's special-key
    // syntax, and this template's whole point is to contain a literal `{{1}}`.
    fireEvent.change(screen.getByLabelText("Preview text"), {
      target: { value: "Your order {{1}} has shipped." },
    });
    await user.click(screen.getByRole("button", { name: "Submit for review" }));

    await waitFor(() => expect(spy).toHaveBeenCalled());
    expect(spy.mock.calls[0]?.[0]).toMatchObject({
      code: "order_shipped",
      channel: "whatsapp",
      category: "utility",
      preview_text: "Your order {{1}} has shipped.",
    });
    expect(await screen.findByText("Draft — not submitted")).toBeTruthy();
    expect(screen.queryByText("Approved")).toBeNull();
  });

  it("rejects a code the service would refuse before sending anything", async () => {
    vi.spyOn(client, "listWhatsAppTemplates").mockResolvedValue(page([]));
    const spy = vi.spyOn(client, "submitWhatsAppTemplate");
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Submit a template" }));
    await user.type(screen.getByLabelText("Template code"), "Not A Valid Code!");
    await user.click(screen.getByRole("button", { name: "Submit for review" }));

    expect(screen.getByText(/Lowercase letters, digits and underscores/)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });
});
