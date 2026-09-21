import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import type { DnsRecordOut, DomainOut, Page } from "@/api/types";
import { DomainsPage } from "./Domains";

/**
 * The two writes this screen owns.
 *
 * Adding a domain re-reads the list, because the service mints a DKIM keypair and fills in the
 * record the pure module could not. Verifying does not: the check rewrites `records` and `status`
 * together and answers with both, so the screen shows what DNS just said rather than asking a
 * second time and risking a status that outlives the records it was folded from.
 */

function records(spfVerified: boolean): DnsRecordOut[] {
  return [
    {
      kind: "spf",
      type: "TXT",
      host: "vaayu-freight.example.com",
      value: "v=spf1 include:_spf.opsgrids.com ~all",
      required: true,
      verified: spfVerified,
    },
    {
      kind: "dmarc",
      type: "TXT",
      host: "_dmarc.vaayu-freight.example.com",
      value: "v=DMARC1; p=none; rua=mailto:dmarc@opsgrids.com",
      required: false,
      verified: false,
    },
  ];
}

const PENDING: DomainOut = {
  id: "b90d5e47-2c31-4a88-8f70-61d4c9a5e223",
  domain: "vaayu-freight.example.com",
  status: "pending",
  records: records(false),
  last_checked_at: null,
  created_at: "2026-09-20T15:38:57Z",
};

function page<T>(items: T[]): Page<T> {
  return { items, total: items.length, page: 1, size: 200, pages: items.length ? 1 : 0 };
}

afterEach(() => {
  vi.restoreAllMocks();
});

async function mount(domains: DomainOut[]) {
  const list = vi.spyOn(client, "listDomains").mockResolvedValue(page(domains));
  const user = userEvent.setup();
  render(
    <MemoryRouter>
      <DomainsPage />
    </MemoryRouter>,
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
      records: records(true),
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
});

describe("adding a domain", () => {
  it("re-reads the list, because the records are minted by the write", async () => {
    const { user, list } = await mount([]);
    const added: DomainOut = { ...PENDING, domain: "vaayu-parcel.example.com" };
    const add = vi.spyOn(client, "addDomain").mockResolvedValue(added);
    list.mockResolvedValue(page([added]));

    await user.type(screen.getByLabelText("Domain"), "Vaayu-Parcel.example.com ");
    await user.click(screen.getByRole("button", { name: "Add domain" }));

    // Lower-cased and trimmed on the way out: a domain name is case-insensitive, and the row it
    // creates is compared against DNS answers that are not.
    await waitFor(() => expect(add).toHaveBeenCalledWith({ domain: "vaayu-parcel.example.com" }));
    await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("heading", { name: "vaayu-parcel.example.com" })).toBeTruthy();
    expect((screen.getByLabelText("Domain") as HTMLInputElement).value).toBe("");
  });

  it("keeps the typed name and shows the service's refusal", async () => {
    const { user } = await mount([]);
    vi.spyOn(client, "addDomain").mockRejectedValue(
      new ApiError(409, "vaayu-freight.example.com is already a sending domain", null),
    );

    await user.type(screen.getByLabelText("Domain"), "vaayu-freight.example.com");
    await user.click(screen.getByRole("button", { name: "Add domain" }));

    expect(
      await screen.findByText("vaayu-freight.example.com is already a sending domain"),
    ).toBeTruthy();
    expect((screen.getByLabelText("Domain") as HTMLInputElement).value).toBe(
      "vaayu-freight.example.com",
    );
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
