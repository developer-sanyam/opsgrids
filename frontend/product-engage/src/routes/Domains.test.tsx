import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import * as client from "@/api/client";
import type { DnsRecordOut, DomainOut, Page } from "@/api/types";
import { DomainsPage } from "./Domains";

/**
 * The writes this screen owns: adding a domain, verifying one, editing an SMTP domain's
 * selector/include, and deleting a domain (confirmed on the page itself, never
 * `window.confirm`).
 *
 * Verifying and editing do not re-read the list: both rewrite `records`/`status` (or the
 * selector/include) and answer with the whole domain, so the screen shows what the server just
 * said rather than asking a second time and risking a stale value outliving the one it came from.
 */

function records(spfStatus: DnsRecordOut["status"]): DnsRecordOut[] {
  return [
    {
      kind: "spf",
      type: "TXT",
      fqdn: "vaayu-freight.example.com",
      registrar_name: "@",
      value: "v=spf1 include:_spf.google.com ~all",
      suggested_value: "v=spf1 include:_spf.google.com ~all",
      required: true,
      status: spfStatus,
      instruction: "Publish one SPF TXT record at your domain's apex that includes _spf.google.com.",
    },
    {
      kind: "dmarc",
      type: "TXT",
      fqdn: "_dmarc.vaayu-freight.example.com",
      registrar_name: "_dmarc",
      value: "v=DMARC1; p=none; rua=mailto:dmarc@vaayu-freight.example.com",
      suggested_value: null,
      required: false,
      status: "missing",
      instruction: "Recommended: publish a DMARC policy. It never blocks sending.",
    },
  ];
}

const PENDING: DomainOut = {
  id: "b90d5e47-2c31-4a88-8f70-61d4c9a5e223",
  domain: "vaayu-freight.example.com",
  status: "pending",
  connection_kind: "google",
  dkim_selector: "google",
  spf_include: null,
  records: records("missing"),
  last_checked_at: null,
  created_at: "2026-09-20T15:38:57Z",
};

const NO_CONNECTION: DomainOut = {
  ...PENDING,
  connection_kind: null,
  records: [],
};

const SMTP_DOMAIN: DomainOut = {
  ...PENDING,
  connection_kind: "smtp",
  dkim_selector: "sg1",
  spf_include: "amazonses.com",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

const WRITE_GRANTS = ["engage.setup.domain.write:ORG", "engage.setup.domain.read:ORG"];
const READ_ONLY_GRANTS = ["engage.setup.domain.read:ORG"];

afterEach(() => {
  vi.restoreAllMocks();
});

async function mount(domains: DomainOut[], grants: string[] = WRITE_GRANTS) {
  const list = vi.spyOn(client, "listDomains").mockResolvedValue(page(domains));
  const user = userEvent.setup();
  render(
    <PermissionProvider grants={grants}>
      <MemoryRouter>
        <DomainsPage />
      </MemoryRouter>
    </PermissionProvider>,
  );
  await screen.findByLabelText("Domain");
  return { user, list };
}

describe("verifying a domain", () => {
  it("takes its new state from the check's own answer", async () => {
    const { user, list } = await mount([PENDING]);
    vi.spyOn(client, "verifyDomain").mockResolvedValue({
      ...PENDING,
      status: "verified",
      records: records("verified"),
      last_checked_at: "2026-09-21T10:15:00Z",
    });

    expect(screen.getByText("Awaiting DNS")).toBeTruthy();
    expect(screen.getByText("0 of 1 required records found.")).toBeTruthy();

    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("Verified")).toBeTruthy();
    expect(screen.getByText("1 of 1 required records found.")).toBeTruthy();
    expect(screen.getByText(/DNS last checked/)).toBeTruthy();
    // No re-read: the response carried the whole domain.
    expect(list).toHaveBeenCalledTimes(1);
  });

  it("names the domain it couldn't check and leaves its state alone", async () => {
    const { user } = await mount([PENDING]);
    vi.spyOn(client, "verifyDomain").mockRejectedValue(
      new ApiError(404, "No such sending domain", null),
    );

    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect(await screen.findByText("No such sending domain")).toBeTruthy();
    expect(screen.getByText("Awaiting DNS")).toBeTruthy();
  });

  it("hides the Verify button for a caller holding only the read", async () => {
    await mount([PENDING], READ_ONLY_GRANTS);
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
  });
});

describe("a domain with no connection yet", () => {
  it("says to connect a provider first and shows no records", async () => {
    await mount([NO_CONNECTION]);

    expect(screen.getByText("Connect your email provider first")).toBeTruthy();
    expect(screen.getByText("No email provider connected")).toBeTruthy();
    expect(screen.queryByRole("table")).toBeNull();
    // Nothing to verify against yet, so there is nothing to click.
    expect(screen.queryByRole("button", { name: "Verify" })).toBeNull();
  });
});

describe("adding a domain", () => {
  it("explains the expected shape and re-reads the list once added", async () => {
    const { user, list } = await mount([]);
    const added: DomainOut = { ...PENDING, domain: "vaayu-parcel.example.com" };
    const add = vi.spyOn(client, "addDomain").mockResolvedValue(added);
    list.mockResolvedValue(page([added]));

    expect(screen.getByText("Enter just the domain, like example.com")).toBeTruthy();

    await user.type(screen.getByLabelText("Domain"), "https://Vaayu-Parcel.example.com/");
    await user.click(screen.getByRole("button", { name: "Add domain" }));

    // Whatever the customer typed goes to the server verbatim — normalising a pasted URL down
    // to its bare domain is `dns_check.normalize_domain_name`'s job, not this form's.
    await waitFor(() =>
      expect(add).toHaveBeenCalledWith({ domain: "https://Vaayu-Parcel.example.com/" }),
    );
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("heading", { name: "vaayu-parcel.example.com" })).toBeTruthy();
    expect((screen.getByLabelText("Domain") as HTMLInputElement).value).toBe("");
  });

  it("keeps the typed name and shows the service's refusal", async () => {
    const { user } = await mount([]);
    vi.spyOn(client, "addDomain").mockRejectedValue(
      new ApiError(422, "Enter just the domain, like example.com", null),
    );

    await user.type(screen.getByLabelText("Domain"), "not a domain");
    await user.click(screen.getByRole("button", { name: "Add domain" }));

    // The helper text under the field carries the same sentence, so the assertion is scoped to
    // the alert the failed submit raises rather than to the string alone.
    const alert = await screen.findByRole("alert");
    expect(within(alert).getByText("Enter just the domain, like example.com")).toBeTruthy();
    expect((screen.getByLabelText("Domain") as HTMLInputElement).value).toBe("not a domain");
  });

  it("is the empty state, so there is no panel telling the customer to use it", async () => {
    await mount([]);

    expect(screen.getByRole("heading", { name: "Add your sending domain" })).toBeTruthy();
    expect(screen.queryByText("No sending domains")).toBeNull();
  });
});

describe("the record table it shows", () => {
  it("separates the required records from the recommended one", async () => {
    await mount([PENDING]);

    const table = screen.getByRole("table");
    expect(within(table).getByText("Required")).toBeTruthy();
    expect(within(table).getByText("Recommended — doesn't block sending")).toBeTruthy();
  });
});

describe("an SMTP domain's provider settings", () => {
  it("shows the selector and include inputs, and saves an edit through the PATCH route", async () => {
    const { user } = await mount([SMTP_DOMAIN]);

    expect((screen.getByLabelText("DKIM selector") as HTMLInputElement).value).toBe("sg1");
    expect((screen.getByLabelText("SPF include") as HTMLInputElement).value).toBe("amazonses.com");

    const edit = vi.spyOn(client, "editDomain").mockResolvedValue({
      ...SMTP_DOMAIN,
      dkim_selector: "sg2",
      status: "pending",
    });

    await user.clear(screen.getByLabelText("DKIM selector"));
    await user.type(screen.getByLabelText("DKIM selector"), "sg2");
    await user.click(screen.getByRole("button", { name: "Save" }));

    await waitFor(() =>
      expect(edit).toHaveBeenCalledWith(SMTP_DOMAIN.id, {
        dkim_selector: "sg2",
        spf_include: "amazonses.com",
      }),
    );
  });

  it("does not show the provider-settings form for a Google or Microsoft domain", async () => {
    await mount([PENDING]);
    expect(screen.queryByLabelText("DKIM selector")).toBeNull();
  });
});

describe("deleting a domain", () => {
  it("asks for confirmation in an in-app dialog, never window.confirm", async () => {
    const { user } = await mount([PENDING]);
    const del = vi.spyOn(client, "deleteDomain").mockResolvedValue(undefined);
    const list = vi.spyOn(client, "listDomains").mockResolvedValue(page([]));

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/Delete this domain\?/)).toBeTruthy();

    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    await waitFor(() => expect(del).toHaveBeenCalledWith(PENDING.id));
    await waitFor(() => expect(list).toHaveBeenCalled());
  });

  it("shows the service's refusal and keeps the domain when a campaign is using it", async () => {
    const { user } = await mount([PENDING]);
    vi.spyOn(client, "deleteDomain").mockRejectedValue(
      new ApiError(409, "news@vaayu-freight.example.com is used by 1 campaign(s)", null),
    );

    await user.click(screen.getByRole("button", { name: "Delete" }));
    const dialog = await screen.findByRole("dialog");
    await user.click(within(dialog).getByRole("button", { name: "Delete" }));

    expect(
      await screen.findByText("news@vaayu-freight.example.com is used by 1 campaign(s)"),
    ).toBeTruthy();
    expect(screen.getByRole("heading", { name: "vaayu-freight.example.com" })).toBeTruthy();
  });

  it("hides the Delete button for a caller holding only the read", async () => {
    await mount([PENDING], READ_ONLY_GRANTS);
    expect(screen.queryByRole("button", { name: "Delete" })).toBeNull();
  });
});
