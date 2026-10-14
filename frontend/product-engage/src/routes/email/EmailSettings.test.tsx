import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { EmailAccount } from "@/api/email";
import { CONNECTION, DOMAINS } from "@/api/fixtures";
import type { DnsRecordOut, DomainOut, Page } from "@/api/types";
import { EmailSettingsPage } from "./EmailSettings";

/**
 * The email account screen: the account and its tabs, the sending-domain authentication card read
 * from the domains API, and the inbox-sync row that only exists when the payload carries it.
 */

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

function record(kind: DnsRecordOut["kind"], status: DnsRecordOut["status"], instruction: string): DnsRecordOut {
  return {
    kind,
    type: "TXT",
    fqdn: `${kind}.example.com`,
    registrar_name: kind,
    value: "v",
    suggested_value: null,
    required: kind !== "dmarc",
    status,
    instruction,
  };
}

const DOMAIN: DomainOut = {
  ...(DOMAINS[0] as DomainOut),
  domain: "vaayu-logistics.example.com",
  records: [
    record("spf", "verified", "Sender policy record found"),
    record("dkim", "verified", "Signing key published"),
    record("dmarc", "missing", "No policy record. Mail may be filtered."),
  ],
};

const ACCOUNT: EmailAccount = { ...CONNECTION };

function serve(account: EmailAccount | null = ACCOUNT, domains: DomainOut[] = [DOMAIN]) {
  vi.spyOn(client, "getConnection").mockResolvedValue(page(account === null ? [] : [account]));
  vi.spyOn(client, "listDomains").mockResolvedValue(page(domains));
}

function renderPage(grants = ["engage.setup.connection.write:ORG"]) {
  return render(
    <PermissionProvider grants={grants}>
      <MemoryRouter initialEntries={["/email/settings"]}>
        <Routes>
          <Route path="/email/settings" element={<EmailSettingsPage />} />
          <Route path="/connection" element={<p>Connection page</p>} />
          <Route path="/domains" element={<p>Domains page</p>} />
        </Routes>
      </MemoryRouter>
    </PermissionProvider>,
  );
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("loading", () => {
  it("says it is loading and shows placeholders", () => {
    vi.spyOn(client, "getConnection").mockReturnValue(new Promise(() => {}));
    vi.spyOn(client, "listDomains").mockReturnValue(new Promise(() => {}));
    const { container } = renderPage();
    expect(screen.getByText("Loading email accounts")).toBeTruthy();
    expect(container.querySelectorAll('[class*="animate-pulse"]').length).toBeGreaterThan(0);
  });
});

describe("populated", () => {
  it("lists the account and shows its configuration", async () => {
    serve();
    renderPage();

    expect(await screen.findByText("All accounts (1)")).toBeTruthy();
    const details = screen.getByRole("region", { name: "Account details" });
    expect(within(details).getByText("Account details")).toBeTruthy();
    expect(within(details).getByText(CONNECTION.host)).toBeTruthy();
    expect(within(details).getByText(String(CONNECTION.port))).toBeTruthy();
    expect(within(screen.getByRole("region", { name: "Accounts" })).getByText("Connected")).toBeTruthy();
  });

  it("shows the daily limit under Sending limits, and only the tabs it has data for", async () => {
    serve();
    const user = userEvent.setup();
    renderPage();

    await screen.findByText("All accounts (1)");
    expect(screen.queryByRole("tab", { name: "Tracking" })).toBeNull();
    expect(screen.queryByRole("tab", { name: "Webhooks" })).toBeNull();
    await user.click(screen.getByRole("tab", { name: "Sending limits" }));

    expect(screen.getByText(`${new Intl.NumberFormat().format(CONNECTION.daily_cap)} emails`)).toBeTruthy();
  });

  it("sends Edit to the connection screen", async () => {
    serve();
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    expect(screen.getByText("Connection page")).toBeTruthy();
  });

  it("hides Edit from someone who may not change the connection", async () => {
    serve();
    renderPage(["engage.setup.connection.read:ORG"]);
    await screen.findByText("All accounts (1)");
    expect(screen.queryByRole("button", { name: "Edit" })).toBeNull();
  });
});

describe("inbox sync", () => {
  it("shows nothing about IMAP while the payload does not carry it", async () => {
    serve();
    renderPage();
    await screen.findByText("All accounts (1)");
    expect(screen.queryByText("Inbox sync (IMAP)")).toBeNull();
  });

  it("shows it, read-only, when the payload carries it", async () => {
    serve({ ...ACCOUNT, imap_enabled: true });
    renderPage();
    await screen.findByText("All accounts (1)");
    expect(screen.getByText("Inbox sync (IMAP)")).toBeTruthy();
    expect(screen.getByText("On")).toBeTruthy();
    expect(screen.queryByRole("switch")).toBeNull();
  });

  it("says Off when it is off", async () => {
    serve({ ...ACCOUNT, imap_enabled: false });
    renderPage();
    expect(await screen.findByText("Off")).toBeTruthy();
  });
});

describe("sending domain authentication", () => {
  it("shows each record's status in words, and offers Add record only for the ones not published", async () => {
    serve();
    renderPage();

    const card = (await screen.findByRole("heading", { name: "Sending domain authentication" })).closest(
      "section",
    ) as HTMLElement;
    expect(await within(card).findByText("vaayu-logistics.example.com")).toBeTruthy();
    expect(within(card).getAllByText("Verified").length).toBe(2);
    expect(within(card).getByText("Not set")).toBeTruthy();
    const add = within(card).getAllByRole("link", { name: "Add record" });
    expect(add.length).toBe(1);
    expect(add[0]?.getAttribute("href")).toBe("/domains");
  });

  it("points at the Domains screen when there is no domain yet", async () => {
    serve(ACCOUNT, []);
    renderPage();
    const link = await screen.findByRole("link", { name: "Add a domain" });
    expect(link.getAttribute("href")).toBe("/domains");
  });

  it("says there is nothing to publish yet for a domain with no records", async () => {
    serve(ACCOUNT, [{ ...DOMAIN, records: [] }]);
    renderPage();
    expect(await screen.findByText(/No records to publish yet/)).toBeTruthy();
  });

  it("fails on its own, with a retry, without taking the account down", async () => {
    vi.spyOn(client, "getConnection").mockResolvedValue(page([ACCOUNT]));
    const domains = vi
      .spyOn(client, "listDomains")
      .mockRejectedValueOnce(new ApiError(503, "DNS is warming up", null))
      .mockResolvedValueOnce(page([DOMAIN]));
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("Couldn't load your sending domains")).toBeTruthy();
    expect(screen.getByText("All accounts (1)")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByText("vaayu-logistics.example.com")).toBeTruthy();
    expect(domains).toHaveBeenCalledTimes(2);
  });
});

describe("empty", () => {
  it("says no account is connected and offers to connect one", async () => {
    serve(null);
    const user = userEvent.setup();
    renderPage();

    expect(await screen.findByText("No email account connected")).toBeTruthy();
    await user.click(screen.getAllByRole("button", { name: "Connect account" })[0] as HTMLElement);
    expect(screen.getByText("Connection page")).toBeTruthy();
  });

  it("offers no Connect account to someone who may not connect one", async () => {
    serve(null);
    renderPage(["engage.setup.connection.read:ORG"]);
    await screen.findByText("No email account connected");
    expect(screen.queryByRole("button", { name: "Connect account" })).toBeNull();
  });
});

describe("error", () => {
  it("names the failure", async () => {
    vi.spyOn(client, "getConnection").mockRejectedValue(new ApiError(500, "Connection service down", null));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("Couldn't load your email accounts")).toBeTruthy();
    expect(screen.getByText("Connection service down")).toBeTruthy();
  });

  it("shows the not-active state for a 402", async () => {
    vi.spyOn(client, "getConnection").mockRejectedValue(new ApiError(402, "Not entitled", null));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([]));
    renderPage();
    expect(await screen.findByText("Engage isn't active for this organization yet")).toBeTruthy();
  });
});
