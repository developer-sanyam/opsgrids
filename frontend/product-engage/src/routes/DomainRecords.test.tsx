import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { DnsRecordOut } from "@/api/types";
import { DomainRecordsTable } from "./DomainRecords";

/**
 * The record table is retyped into a registrar, so what is pinned here is fidelity and the
 * required/recommended split.
 *
 * A value that is reflowed, truncated or prettified is a record that will never verify, and the
 * customer will blame their registrar. A DMARC row that reads as a blocker sends them to argue
 * about `p=` with their security team before they can send at all — `fold_domain_status` folds
 * from the required records alone, and this table has to say so.
 */

const DKIM_VALUE =
  "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu1xK0Yb7VqJ8mQ3pN2fR" +
  "wZ6tD9sXcL4hVgB1yPoE5aTnUmK7jH0dSfCvA3rXqW8ZbN6MlGyIuOpQ2eRtF4xDkJ9vS1cHnA7mB3gYwP";

/** SPF and the return path are up, DKIM and tracking are not, DMARC is unpublished and optional —
 *  the mid-setup shape the service produces most often. */
const RECORDS: DnsRecordOut[] = [
  {
    kind: "spf",
    type: "TXT",
    host: "vaayu-freight.example.com",
    value: "v=spf1 include:_spf.opsgrids.com ~all",
    required: true,
    verified: true,
  },
  {
    kind: "dkim",
    type: "TXT",
    host: "og8b30e57c14da._domainkey.vaayu-freight.example.com",
    value: DKIM_VALUE,
    required: true,
    verified: false,
  },
  {
    kind: "dmarc",
    type: "TXT",
    host: "_dmarc.vaayu-freight.example.com",
    value: "v=DMARC1; p=none; rua=mailto:dmarc@opsgrids.com",
    required: false,
    verified: false,
  },
  {
    kind: "return_path",
    type: "CNAME",
    host: "bounce.vaayu-freight.example.com",
    value: "bounce.opsgrids.com",
    required: true,
    verified: true,
  },
  {
    kind: "tracking",
    type: "CNAME",
    host: "track.vaayu-freight.example.com",
    value: "track.opsgrids.com",
    required: true,
    verified: false,
  },
];

function mount(records = RECORDS) {
  render(<DomainRecordsTable records={records} domainName="vaayu-freight.example.com" />);
  return userEvent.setup();
}

/** The row whose row header names this record. */
function rowFor(name: RegExp): HTMLElement {
  const row = screen.getByRole("rowheader", { name }).closest("tr");
  if (row === null) throw new Error(`No row for ${String(name)}`);
  return row;
}

/**
 * jsdom ships no clipboard, and `userEvent.setup()` installs one of its own — so a stub has to go
 * in *after* the user is set up, or it is the one that gets overwritten.
 */
function stubClipboard(clipboard: { writeText: () => Promise<void> } | undefined) {
  Object.defineProperty(navigator, "clipboard", { value: clipboard, configurable: true });
}

afterEach(() => {
  vi.restoreAllMocks();
  Reflect.deleteProperty(navigator, "clipboard");
});

describe("the DNS record table", () => {
  it("renders one row per record, with its type, host and value exactly as the service sent them", () => {
    mount();

    const dkim = rowFor(/DKIM/);
    expect(within(dkim).getByText("TXT")).toBeTruthy();
    expect(
      within(dkim).getByText("og8b30e57c14da._domainkey.vaayu-freight.example.com"),
    ).toBeTruthy();
    // Character for character. A key with a line break inserted for looks never verifies.
    expect(within(dkim).getByText(DKIM_VALUE)).toBeTruthy();

    const returnPath = rowFor(/Return path/);
    expect(within(returnPath).getByText("CNAME")).toBeTruthy();
    expect(within(returnPath).getByText("bounce.opsgrids.com")).toBeTruthy();

    // Five records, plus the header row.
    expect(screen.getAllByRole("row")).toHaveLength(6);
  });

  it("marks each record required, and DMARC as recommended and not blocking", () => {
    mount();

    expect(within(rowFor(/SPF/)).getByText("Required")).toBeTruthy();
    expect(within(rowFor(/DKIM/)).getByText("Required")).toBeTruthy();
    expect(within(rowFor(/Return path/)).getByText("Required")).toBeTruthy();
    expect(within(rowFor(/Tracking/)).getByText("Required")).toBeTruthy();
    expect(
      within(rowFor(/DMARC/)).getByText("Recommended — doesn't block sending"),
    ).toBeTruthy();
  });

  it("states each record's own state, in words rather than by colour", () => {
    mount();

    expect(within(rowFor(/SPF/)).getByText("Published")).toBeTruthy();
    expect(within(rowFor(/Return path/)).getByText("Published")).toBeTruthy();
    expect(within(rowFor(/DKIM/)).getByText("Not found")).toBeTruthy();
    expect(within(rowFor(/DMARC/)).getByText("Not found")).toBeTruthy();
    expect(within(rowFor(/Tracking/)).getByText("Not found")).toBeTruthy();
  });

  it("counts the required records only, so an unpublished DMARC never reads as the blocker", () => {
    mount();
    // Two of the four required records are up; DMARC is in neither half of that fraction.
    expect(screen.getByText("2 of 4 required records found.")).toBeTruthy();
  });

  it("says a DKIM value is still being generated rather than showing an empty cell", () => {
    /* `domain.dns_records_for` is pure and cannot mint a keypair, so it emits the DKIM record
       with an empty value and the writer fills it in. A domain read between those two moments
       is the shape below. */
    mount([
      {
        kind: "dkim",
        type: "TXT",
        host: "og8b30e57c14da._domainkey.vaayu-freight.example.com",
        value: "",
        required: true,
        verified: false,
      },
    ]);

    expect(screen.getByText("Generated when the domain is added")).toBeTruthy();
    // And no copy control for a value there is nothing to copy.
    expect(screen.queryByRole("button", { name: /Copy DKIM.*value/ })).toBeNull();
  });

  it("copies a value verbatim, which is the whole reason the button is there", async () => {
    const user = mount();
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard({ writeText });

    await user.click(screen.getByRole("button", { name: /Copy DKIM.*value/ }));

    expect(writeText).toHaveBeenCalledWith(DKIM_VALUE);
    expect(await screen.findByText("Copied")).toBeTruthy();
  });

  it("copies a host as well as a value, because both are typed into the registrar", async () => {
    const user = mount();
    const writeText = vi.fn().mockResolvedValue(undefined);
    stubClipboard({ writeText });

    await user.click(screen.getByRole("button", { name: /Copy DMARC.*host/ }));

    expect(writeText).toHaveBeenCalledWith("_dmarc.vaayu-freight.example.com");
  });

  it("survives a browser with no clipboard rather than leaving a button that throws", async () => {
    const user = mount();
    // What an insecure origin looks like, where `navigator.clipboard` is simply not there.
    stubClipboard(undefined);

    await user.click(screen.getByRole("button", { name: /Copy SPF.*value/ }));

    // The value is still on screen to select by hand, and nothing claims it was copied.
    expect(screen.getByText("v=spf1 include:_spf.opsgrids.com ~all")).toBeTruthy();
    expect(screen.queryByText("Copied")).toBeNull();
  });
});
