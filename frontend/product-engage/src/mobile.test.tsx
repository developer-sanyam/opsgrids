/**
 * The phone fixes in this app, one behaviour each: the wide tables are cards below 768px, a
 * pasted menu path reads as one, a new automation is not scolded before it is touched, and the
 * inbox opens on the thread with the reply box pinned.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";

import * as automationsApi from "@/api/automations";
import * as client from "@/api/client";
import type { CampaignOut, DnsRecordOut, DomainOut, Page, SenderOut } from "@/api/types";
import { readableInstruction } from "@/lib/records";
import { DomainRecordsTable } from "@/routes/DomainRecords";
import { BuilderPage } from "@/routes/automations/Builder";
import { Customer360Panel } from "@/routes/inbox/Customer360Panel";
import { SendersPage } from "@/routes/Senders";

function stubPhone(): void {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

describe("readableInstruction", () => {
  it("turns a typed arrow in a menu path into the arrow character", () => {
    expect(readableInstruction("go to Apps -> Google Workspace -> Gmail")).toBe(
      "go to Apps → Google Workspace → Gmail",
    );
  });

  it("leaves a hyphen or a comparison alone", () => {
    expect(readableInstruction("use a CNAME - not a TXT, and 3->4 stays")).toBe("use a CNAME - not a TXT, and 3->4 stays");
  });
});

const RECORD: DnsRecordOut = {
  kind: "dkim",
  type: "TXT",
  registrar_name: "google._domainkey",
  fqdn: "google._domainkey.vaayu.example.com",
  value: "v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC",
  instruction: "In the Google Admin console, go to Apps -> Google Workspace -> Gmail -> Authenticate email.",
  required: true,
  status: "missing",
  suggested_value: null,
};

describe("DomainRecordsTable", () => {
  it("is a table on a wide screen", () => {
    render(<DomainRecordsTable records={[RECORD]} domainName="vaayu.example.com" />);
    expect(screen.getByRole("table")).toBeTruthy();
  });

  it("is a card per record on a phone, with the whole value and the arrow fixed", () => {
    stubPhone();
    render(<DomainRecordsTable records={[RECORD]} domainName="vaayu.example.com" />);
    expect(screen.queryByRole("table")).toBeNull();
    const list = screen.getByRole("list", { name: /DNS records to publish for vaayu.example.com/ });
    expect(within(list).getByText(RECORD.value)).toBeTruthy();
    expect(within(list).getByText(/Apps → Google Workspace → Gmail → Authenticate email\./)).toBeTruthy();
    expect(within(list).getByRole("button", { name: /^Copy .* value$/ })).toBeTruthy();
  });
});

describe("Senders on a phone", () => {
  const DOMAIN: DomainOut = {
    id: "dom-1",
    domain: "vaayu.example.com",
    status: "verified",
    connection_kind: "google",
    dkim_selector: "google",
    spf_include: null,
    records: [],
    last_checked_at: null,
    created_at: "2026-08-28T11:04:09Z",
  };
  const SENDER: SenderOut = {
    id: "snd-1",
    domain_id: "dom-1",
    name: "Priya Raghavan",
    address: "priya.raghavan@vaayu.example.com",
    reply_to: "",
    signature: "",
    department: "Key accounts",
    usable: false,
    verified_at: null,
    created_at: "2026-09-18T13:47:20Z",
  };

  it("is a card per sender that still says in words why it cannot send, with the test one tap away", async () => {
    stubPhone();
    vi.spyOn(client, "listSenders").mockResolvedValue(page([SENDER]));
    vi.spyOn(client, "listDomains").mockResolvedValue(page([DOMAIN]));
    render(
      <MemoryRouter>
        <SendersPage />
      </MemoryRouter>,
    );

    const list = await screen.findByRole("list", { name: "Senders" });
    expect(screen.queryByRole("table")).toBeNull();
    expect(within(list).getByText(/No campaign can send as this address until a test message is delivered\./)).toBeTruthy();
    expect(within(list).getByRole("button", { name: /Send test/ })).toBeTruthy();
  });
});

describe("Campaigns on a phone", () => {
  it("is a card per campaign that opens it, with no table to scroll", async () => {
    stubPhone();
    const campaign = {
      id: "cmp-1",
      name: "Diwali offer",
      subject: "Festive prices",
      status: "sent",
      list_id: null,
      stats: { recipients: 1200, sent: 1180, failed: 20 },
      updated_at: "2026-09-18T13:47:20Z",
    } as unknown as CampaignOut;
    vi.spyOn(client, "listCampaigns").mockResolvedValue(page([campaign]));
    const { CampaignsPage } = await import("@/routes/Campaigns");
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/campaigns"]}>
        <Routes>
          <Route path="/campaigns" element={<CampaignsPage />} />
          <Route path="/campaigns/:id" element={<p>Campaign page</p>} />
        </Routes>
      </MemoryRouter>,
    );

    await screen.findByRole("list", { name: "Campaigns" });
    expect(screen.queryByRole("table")).toBeNull();
    await user.click(screen.getByRole("button", { name: /Diwali offer/ }));
    expect(await screen.findByText("Campaign page")).toBeTruthy();
  });
});

describe("Automation builder", () => {
  it("does not flag a new automation's empty trigger until it has been touched or saved", async () => {
    vi.spyOn(automationsApi, "listTriggers").mockResolvedValue([]);
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/automations/new"]}>
        <Routes>
          <Route path="/automations/new" element={<BuilderPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const save = await screen.findByRole("button", { name: "Save draft" });
    expect(document.querySelector('[id^="step-problems-"]')).toBeNull();

    await user.click(save);
    await waitFor(() => expect(document.querySelector('[id^="step-problems-"]')).not.toBeNull());
  });
});

describe("Customer 360 on the inbox", () => {
  it("starts collapsed on a phone, so the thread and the reply box are what the screen opens on", () => {
    stubPhone();
    render(<Customer360Panel customerId={null} />);
    expect(screen.getByRole("button", { name: "Show" }).getAttribute("aria-expanded")).toBe("false");
  });

  it("starts open on a wide screen", () => {
    render(<Customer360Panel customerId={null} />);
    expect(screen.getByRole("button", { name: "Hide" }).getAttribute("aria-expanded")).toBe("true");
  });
});
