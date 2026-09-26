import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Page, WhatsAppConnectionOut } from "@/api/types";
import * as api from "@/api/whatsappSignup";
import {
  HEALTH_DISCONNECTED,
  HEALTH_OK,
  MAIN_NUMBER,
  PENDING_NUMBER,
  SUPPORT_NUMBER,
} from "./testFixtures";
import { WhatsAppSettingsPage } from "./WhatsAppSettings";

const READ = "engage.setup.whatsapp.read:ORG";
const WRITE = "engage.setup.whatsapp.write:ORG";

const CONNECTION: WhatsAppConnectionOut = {
  waba_id: "102938475617890",
  phone_number_id: MAIN_NUMBER.phone_number_id,
  display_number: "+91 98765 43210",
  status: "connected",
};

function renderPage(grants: string[] = [READ, WRITE]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter>
        <WhatsAppSettingsPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
}

function mockConnected(numbers = [MAIN_NUMBER, SUPPORT_NUMBER]) {
  vi.spyOn(api, "getWhatsAppHealth").mockResolvedValue(HEALTH_OK);
  vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: numbers });
}

beforeEach(() => {
  const connection: Page<WhatsAppConnectionOut> = { items: [CONNECTION], total: 1, page: 1, size: 200, pages: 1 };
  vi.spyOn(client, "getWhatsAppConnection").mockResolvedValue(connection);
});

describe("loading", () => {
  it("reserves the screen's space with skeletons while the reads are in flight", () => {
    vi.spyOn(api, "getWhatsAppHealth").mockReturnValue(new Promise(() => {}));
    vi.spyOn(api, "listWhatsAppNumbers").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(screen.getByRole("heading", { level: 1, name: "WhatsApp Settings" })).toBeTruthy();
    expect(container.querySelectorAll('[aria-hidden="true"]').length).toBeGreaterThan(0);
    expect(screen.queryByRole("region", { name: "Phone numbers" })).toBeNull();
  });
});

describe("error", () => {
  it("says what failed and retries", async () => {
    const health = vi
      .spyOn(api, "getWhatsAppHealth")
      .mockRejectedValueOnce(new ApiError(500, "Internal server error", null))
      .mockResolvedValue(HEALTH_OK);
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Couldn't load your WhatsApp settings")).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("region", { name: "Business account" })).toBeTruthy();
    expect(health).toHaveBeenCalledTimes(2);
  });

  it("offers the dashboard's Products page for a 402", async () => {
    vi.spyOn(api, "getWhatsAppHealth").mockRejectedValue(new ApiError(402, "Engage is not active", null));
    vi.spyOn(api, "listWhatsAppNumbers").mockRejectedValue(new ApiError(402, "Engage is not active", null));
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });
});

describe("empty", () => {
  it("offers to connect when nothing is connected", async () => {
    vi.spyOn(api, "getWhatsAppHealth").mockResolvedValue(HEALTH_DISCONNECTED);
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [] });
    renderPage();

    expect(await screen.findByText("WhatsApp isn't connected")).toBeTruthy();
    expect(screen.getByText("Disconnected")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Connect WhatsApp" }).getAttribute("href")).toBe("/whatsapp/connect");
    expect(screen.queryByRole("region", { name: "Connection health" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
  });

  it("says an Owner or Admin can connect it, without a control, for a caller who may only read", async () => {
    vi.spyOn(api, "getWhatsAppHealth").mockResolvedValue(HEALTH_DISCONNECTED);
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [] });
    renderPage([READ]);
    expect(await screen.findByText("Someone who is an Owner or Admin in your organization can connect it.")).toBeTruthy();
    expect(screen.queryByRole("link", { name: "Connect WhatsApp" })).toBeNull();
  });
});

describe("populated", () => {
  it("shows the business account, every number with its quality, limit and default, and the connection health", async () => {
    mockConnected([MAIN_NUMBER, SUPPORT_NUMBER, PENDING_NUMBER]);
    renderPage();

    const account = await screen.findByRole("region", { name: "Business account" });
    expect(within(account).getByText("Connected")).toBeTruthy();
    expect(within(account).getByText("Acme Corp")).toBeTruthy();
    expect(within(account).getByText("WhatsApp Business Account: ID ••••7890")).toBeTruthy();
    expect(within(account).getByRole("link", { name: "Reconnect" }).getAttribute("href")).toBe("/whatsapp/connect");
    expect(within(account).getByRole("button", { name: "Disconnect" })).toBeTruthy();

    const table = screen.getByRole("table", { name: "Phone numbers on your WhatsApp Business Account" });
    const rows = within(table).getAllByRole("row");
    expect(rows).toHaveLength(4);
    const main = rows[1] as HTMLElement;
    expect(within(main).getByText("+91 ••••• ••210")).toBeTruthy();
    expect(within(main).getByText("Active")).toBeTruthy();
    expect(within(main).getByText("Default")).toBeTruthy();
    expect(within(main).getByText("High")).toBeTruthy();
    expect(within(main).getByText("1,000 customers / 24 hours")).toBeTruthy();
    const support = rows[2] as HTMLElement;
    expect(within(support).getByText("Display name in review")).toBeTruthy();
    expect(within(support).getByText("Not rated yet")).toBeTruthy();
    expect(within(support).queryByText("Default")).toBeNull();
    const pending = rows[3] as HTMLElement;
    expect(within(pending).getByText("Needs verification")).toBeTruthy();
    expect(within(pending).getByText("Not available")).toBeTruthy();

    const health = screen.getByRole("region", { name: "Connection health" });
    expect(within(health).getByText(/Valid\. Refreshed automatically and stored encrypted\./)).toBeTruthy();
    expect(within(health).getByText(/Receiving events\. Last event/)).toBeTruthy();
    expect(within(health).getByText(/24 templates synced\./)).toBeTruthy();
    expect(within(health).getByRole("button", { name: "Sync now" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /Add Phone Number/ }).getAttribute("href")).toBe("/whatsapp/connect");
  });

  it("says in words when the token or the webhook needs attention", async () => {
    vi.spyOn(api, "getWhatsAppHealth").mockResolvedValue({
      ...HEALTH_OK,
      token: { state: "invalid", checked_at: null },
      webhook: { state: "silent", last_event_at: null },
    });
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    renderPage();

    const health = await screen.findByRole("region", { name: "Connection health" });
    expect(within(health).getByText(/No longer valid\. Choose Reconnect above/)).toBeTruthy();
    expect(within(health).getByText(/No events have arrived recently/)).toBeTruthy();
  });

  it("shows a numberless connected account as an empty numbers state", async () => {
    mockConnected([]);
    renderPage();
    expect(await screen.findByText("No phone numbers yet")).toBeTruthy();
  });
});

describe("disconnecting", () => {
  it("asks first, names the consequence, focuses Cancel, and does nothing until confirmed", async () => {
    mockConnected();
    const disconnect = vi.spyOn(api, "disconnectWhatsApp").mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("dialog", { name: "Disconnect WhatsApp?" });
    expect(within(dialog).getByText(/delete the saved access token/)).toBeTruthy();
    expect(within(dialog).getByText(/templates, automations and conversation history are kept/)).toBeTruthy();
    await waitFor(() => expect(document.activeElement).toBe(within(dialog).getByRole("button", { name: "Cancel" })));

    await user.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(disconnect).not.toHaveBeenCalled();
  });

  it("disconnects once on confirm, then shows the disconnected state and keeps the page's own heading focused", async () => {
    vi.spyOn(api, "getWhatsAppHealth").mockResolvedValueOnce(HEALTH_OK).mockResolvedValue(HEALTH_DISCONNECTED);
    vi.spyOn(api, "listWhatsAppNumbers").mockResolvedValue({ items: [MAIN_NUMBER] });
    const disconnect = vi.spyOn(api, "disconnectWhatsApp").mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("dialog", { name: "Disconnect WhatsApp?" });
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    expect(await screen.findByText("WhatsApp isn't connected")).toBeTruthy();
    expect(disconnect).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    await waitFor(() =>
      expect(document.activeElement).toBe(screen.getByRole("heading", { level: 1, name: "WhatsApp Settings" })),
    );
  });

  it("keeps the dialog open and shows the server's sentence when the disconnect fails", async () => {
    mockConnected();
    vi.spyOn(api, "disconnectWhatsApp").mockRejectedValue(new ApiError(403, "Only an Owner or Admin can disconnect WhatsApp.", null));
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Disconnect" }));
    const dialog = await screen.findByRole("dialog", { name: "Disconnect WhatsApp?" });
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    expect(await within(dialog).findByText("Only an Owner or Admin can disconnect WhatsApp.")).toBeTruthy();
    expect(within(dialog).getByRole("button", { name: "Try again" })).toBeTruthy();
    expect(screen.getByRole("dialog", { name: "Disconnect WhatsApp?" })).toBeTruthy();
  });
});

describe("numbers and templates", () => {
  it("makes another number the default from its own row's menu, then re-reads", async () => {
    mockConnected();
    const setDefault = vi.spyOn(api, "setDefaultNumber").mockResolvedValue({ ok: true });
    const user = userEvent.setup();
    renderPage();

    // The default number has no action to offer, so only the other row has a menu.
    const trigger = await screen.findByRole("button", { name: "More actions for +91 ••••• ••789" });
    expect(screen.queryByRole("button", { name: "More actions for +91 ••••• ••210" })).toBeNull();
    await user.click(trigger);
    await user.click(await screen.findByRole("menuitem", { name: "Make default number" }));

    await waitFor(() => expect(setDefault).toHaveBeenCalledWith(SUPPORT_NUMBER.id));
    await waitFor(() => expect(api.listWhatsAppNumbers).toHaveBeenCalledTimes(2));
  });

  it("syncs templates on request and says when it fails", async () => {
    mockConnected();
    const sync = vi
      .spyOn(api, "syncWhatsAppTemplates")
      .mockRejectedValueOnce(new ApiError(502, "Meta didn't answer.", null))
      .mockResolvedValue({ synced: 24, created: 1, updated: 2, removed: 0 });
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Sync now" }));
    expect(await screen.findByText("Meta didn't answer.")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Sync now" }));
    await waitFor(() => expect(sync).toHaveBeenCalledTimes(2));
    await waitFor(() => expect(screen.queryByText("Meta didn't answer.")).toBeNull());
  });
});

describe("permissions", () => {
  it("refuses a caller without the read permission", async () => {
    renderPage([]);
    expect(await screen.findByText("You can't open WhatsApp settings right now")).toBeTruthy();
    expect(screen.queryByRole("region", { name: "Business account" })).toBeNull();
  });

  it("shows a read-only caller the facts but none of the controls", async () => {
    mockConnected();
    renderPage([READ]);

    expect(await screen.findByRole("region", { name: "Business account" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
    expect(screen.queryByRole("link", { name: "Reconnect" })).toBeNull();
    expect(screen.queryByRole("link", { name: /Add Phone Number/ })).toBeNull();
    expect(screen.queryByRole("button", { name: "Sync now" })).toBeNull();
    expect(screen.queryByRole("button", { name: /More actions for/ })).toBeNull();
  });
});

describe("accessibility", () => {
  it("has one h1, named regions, and column headers tied to their cells", async () => {
    mockConnected();
    renderPage();

    await screen.findByRole("region", { name: "Phone numbers" });
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    for (const name of ["Business account", "Phone numbers", "Connection health"]) {
      expect(screen.getByRole("region", { name })).toBeTruthy();
    }
    const table = screen.getByRole("table");
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual([
      "Number",
      "Display name",
      "Quality",
      "Messaging limit",
      "Actions",
    ]);
    expect(within(table).getAllByRole("rowheader")).toHaveLength(2);
  });
});
