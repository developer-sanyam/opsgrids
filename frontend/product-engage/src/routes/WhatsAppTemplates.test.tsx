import { render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as api from "@/api/whatsappTemplates";
import { WhatsAppTemplatesPage } from "./WhatsAppTemplates";
import {
  APPROVED,
  DETAIL,
  PENDING,
  PREVIEW,
  READ_AND_WRITE,
  page,
} from "./whatsappTemplates/test-fixtures";

/**
 * The screen's original contract — loading, empty, error, the two Engage refusals, permission
 * gating, and that a status is always Meta's own — kept green across the move to the W2 shape.
 */

afterEach(() => {
  vi.restoreAllMocks();
});

function stubPanel() {
  vi.spyOn(api, "getTemplate").mockResolvedValue(DETAIL);
  vi.spyOn(api, "previewTemplate").mockResolvedValue(PREVIEW);
}

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
    vi.spyOn(api, "listTemplates").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
    expect(screen.getByText("Loading templates")).toBeTruthy();
  });
});

describe("empty", () => {
  it("says what to do next rather than showing nothing", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("No WhatsApp templates yet")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Create Template" }).length).toBeGreaterThan(0);
  });
});

describe("error", () => {
  it("shows a real failure, with a way to try again, for anything other than a 402", async () => {
    const list = vi
      .spyOn(api, "listTemplates")
      .mockRejectedValueOnce(new ApiError(500, "Internal server error", null))
      .mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("Couldn't load your WhatsApp templates")).toBeTruthy();
    expect(screen.getByText("Internal server error")).toBeTruthy();

    screen.getByRole("button", { name: "Try again" }).click();
    expect(await screen.findByText("No WhatsApp templates yet")).toBeTruthy();
    expect(list).toHaveBeenCalledTimes(2);
  });
});

describe("402: Engage not active", () => {
  it("shows the ordinary Engage not-active state", async () => {
    vi.spyOn(api, "listTemplates").mockRejectedValue(
      new ApiError(402, "engage is not active for this organization", null),
    );
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });
});

describe("403: missing the engage permission", () => {
  it("shows the service's own sentence naming the permission", async () => {
    vi.spyOn(api, "listTemplates").mockRejectedValue(
      new ApiError(403, "Missing permission: engage.whatsapp.template.read", null),
    );
    renderPage();
    expect(
      await screen.findByText("Missing permission: engage.whatsapp.template.read"),
    ).toBeTruthy();
  });
});

describe("permission gating", () => {
  it("hides creating and syncing for a caller with only the read grant", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    renderPage(["engage.whatsapp.template.read:ORG"]);

    await screen.findByRole("link", { name: "order_shipped" });
    expect(screen.queryByRole("button", { name: "Create Template" })).toBeNull();
    expect(screen.queryByRole("button", { name: /Sync from Meta/ })).toBeNull();
  });

  it("offers both to a caller with the write grant", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    renderPage();

    await screen.findByRole("link", { name: "order_shipped" });
    expect(screen.getByRole("button", { name: "Create Template" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Sync from Meta" })).toBeTruthy();
  });
});

describe("populated", () => {
  it("renders the status Meta gave each template, never a fabricated one", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED, PENDING]));
    stubPanel();
    renderPage();

    const table = await screen.findByRole("table");
    expect(within(table).getByRole("link", { name: "order_shipped" })).toBeTruthy();
    expect(within(table).getByText("Approved")).toBeTruthy();
    expect(within(table).getByRole("link", { name: "feedback_request" })).toBeTruthy();
    expect(within(table).getByText("Pending Meta review")).toBeTruthy();
    expect(within(table).queryByText("Draft — not submitted")).toBeNull();
    expect(within(table).getByText("Utility")).toBeTruthy();
    expect(within(table).getByText("Marketing")).toBeTruthy();
    expect(within(table).getAllByText("English (US)").length).toBe(2);
  });

  it("links each name to its own page", async () => {
    vi.spyOn(api, "listTemplates").mockResolvedValue(page([APPROVED]));
    stubPanel();
    renderPage();
    const link = await screen.findByRole("link", { name: "order_shipped" });
    expect(link.getAttribute("href")).toBe("/whatsapp/templates/order_shipped");
  });
});
