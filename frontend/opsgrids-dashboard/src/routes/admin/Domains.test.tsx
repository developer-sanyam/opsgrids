import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ApiError, PermissionProvider } from "@opsgrids/client";

import { DomainsPage } from "./Domains";

vi.mock("@/api", () => ({
  listDomains: vi.fn(),
  claimDomain: vi.fn(),
  verifyDomain: vi.fn(),
  releaseDomain: vi.fn(),
}));

import * as api from "@/api";

const listDomains = vi.mocked(api.listDomains);
const claimDomain = vi.mocked(api.claimDomain);
const verifyDomain = vi.mocked(api.verifyDomain);
const releaseDomain = vi.mocked(api.releaseDomain);

const pending = {
  id: "dom-1",
  domain: "acme.example",
  is_primary: true,
  auto_join: false,
  verified_at: null,
  created_at: "2026-09-01T00:00:00Z",
  expires_at: "2099-01-01T00:00:00Z",
  verification_host: "_opsgrids-verify.acme.example",
  verification_value: "opsgrids-verify=deadbeef",
  last_checked_at: null,
  last_check_ok: null,
  failing_since: null,
};

const verified = {
  ...pending,
  id: "dom-2",
  domain: "verified.example",
  verified_at: "2026-09-05T00:00:00Z",
  expires_at: null,
  last_checked_at: "2026-09-20T00:00:00Z",
  last_check_ok: true,
};

const expired = { ...pending, id: "dom-3", domain: "expired.example", expires_at: "2020-01-01T00:00:00Z" };

const failingVerified = {
  ...verified,
  id: "dom-4",
  domain: "failing.example",
  last_check_ok: false,
  failing_since: "2026-09-18T00:00:00Z",
};

function renderPage(grants: string[] = ["core.identity.domain.write:ORG"]) {
  return render(
    <PermissionProvider grants={grants}>
      <DomainsPage />
    </PermissionProvider>,
  );
}

describe("DomainsPage — list", () => {
  it("shows an empty state with a call to action when there are none", async () => {
    listDomains.mockResolvedValue([]);
    renderPage();

    expect(await screen.findByText("No domains claimed yet")).toBeTruthy();
    expect(screen.getAllByRole("button", { name: "Claim a domain" }).length).toBeGreaterThan(0);
  });

  it("hides every write control without the write permission", async () => {
    listDomains.mockResolvedValue([pending]);
    renderPage([]);

    await screen.findByText("acme.example");
    expect(screen.queryByRole("button", { name: "Claim a domain" })).toBeNull();
    const row = screen.getByText("acme.example").closest("tr");
    expect(row).toBeTruthy();
    expect(within(row as HTMLElement).queryByRole("button", { name: /remove/i })).toBeNull();
  });

  it("renders the right status tag for a pending, verified and expired domain", async () => {
    listDomains.mockResolvedValue([pending, verified, expired]);
    renderPage();

    await screen.findByText("acme.example");
    expect(screen.getByText("Pending")).toBeTruthy();
    expect(screen.getByText("Verified")).toBeTruthy();
    expect(screen.getByText("Expired")).toBeTruthy();
  });

  it("badges a verified domain whose periodic re-check is failing, without changing its status", async () => {
    listDomains.mockResolvedValue([failingVerified]);
    renderPage();

    await screen.findByText("failing.example");
    // Still verified — a failing re-check is a flag, never a silent revocation.
    expect(screen.getByText("Verified")).toBeTruthy();
    expect(screen.getByText("Check failing")).toBeTruthy();
  });

  it("does not badge a verified domain that has never failed a check", async () => {
    listDomains.mockResolvedValue([verified]);
    renderPage();

    await screen.findByText("verified.example");
    expect(screen.queryByText("Check failing")).toBeNull();
  });

  it("marks the organisation's primary domain", async () => {
    listDomains.mockResolvedValue([pending]);
    renderPage();

    await screen.findByText("acme.example");
    expect(screen.getByText("Primary")).toBeTruthy();
  });

  it("shows an error state with a retry when loading fails", async () => {
    listDomains.mockRejectedValue(new ApiError(500, "boom", { status: 500, detail: "boom" }));
    renderPage();

    expect(await screen.findByText("Couldn't load domains")).toBeTruthy();
  });
});

describe("DomainsPage — claiming", () => {
  it("refuses an empty domain before calling the server", async () => {
    listDomains.mockResolvedValue([]);
    const user = userEvent.setup();
    renderPage();
    await user.click((await screen.findAllByRole("button", { name: "Claim a domain" }))[0] as HTMLElement);
    await screen.findByRole("dialog", { name: "Claim a domain" });

    await user.click(screen.getByRole("button", { name: "Claim domain" }));

    expect(await screen.findByText("Enter the domain you want to claim.")).toBeTruthy();
    expect(claimDomain).not.toHaveBeenCalled();
  });

  it("opens the DNS record dialog on success", async () => {
    listDomains.mockResolvedValue([]);
    claimDomain.mockResolvedValue(pending);
    const user = userEvent.setup();
    renderPage();
    await user.click((await screen.findAllByRole("button", { name: "Claim a domain" }))[0] as HTMLElement);
    await screen.findByRole("dialog", { name: "Claim a domain" });

    await user.type(screen.getByLabelText("Domain"), "acme.example");
    await user.click(screen.getByRole("button", { name: "Claim domain" }));

    await screen.findByRole("dialog", { name: "acme.example" });
    expect(screen.getByText("_opsgrids-verify.acme.example")).toBeTruthy();
    expect(screen.getByText("opsgrids-verify=deadbeef")).toBeTruthy();
  });

  it("maps a 422 for a public suffix to the domain field", async () => {
    listDomains.mockResolvedValue([]);
    claimDomain.mockRejectedValue(
      new ApiError(422, "That is a public suffix, not a domain anyone can own.", {
        status: 422,
        detail: "That is a public suffix, not a domain anyone can own.",
        errors: [
          {
            field: "domain",
            code: "domain_public_suffix",
            message: "That is a public suffix, not a domain anyone can own.",
          },
        ],
      }),
    );
    const user = userEvent.setup();
    renderPage();
    await user.click((await screen.findAllByRole("button", { name: "Claim a domain" }))[0] as HTMLElement);
    await screen.findByRole("dialog", { name: "Claim a domain" });

    await user.type(screen.getByLabelText("Domain"), "com");
    await user.click(screen.getByRole("button", { name: "Claim domain" }));

    expect(await screen.findByText("That is a public suffix, not a domain anyone can own.")).toBeTruthy();
  });
});

describe("DomainsPage — the record dialog", () => {
  it("shows the failing check as a flag rather than a revocation", async () => {
    listDomains.mockResolvedValue([failingVerified]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "View record failing.example" }));
    await screen.findByRole("dialog", { name: "failing.example" });

    expect(screen.getByText(/could not be found on a recent check/)).toBeTruthy();
    expect(screen.queryByText("This domain is verified.")).toBeNull();
    // Still verified: the row's own status tag stays, no "claim again" is offered — there is
    // nothing to re-claim, the domain still is what it says it is.
    expect(screen.getAllByText("Verified").length).toBeGreaterThan(0);
    expect(screen.queryByRole("button", { name: "Claim again" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Verify now" })).toBeNull();
  });

  it("verifies now and reflects the new state", async () => {
    listDomains.mockResolvedValue([pending]);
    verifyDomain.mockResolvedValue(verified);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Publish record acme.example" }));
    await screen.findByRole("dialog", { name: "acme.example" });

    await user.click(screen.getByRole("button", { name: "Verify now" }));

    await waitFor(() => expect(verifyDomain).toHaveBeenCalledWith("dom-1"));
    expect(await screen.findByText("This domain is verified.")).toBeTruthy();
  });

  it("offers to claim again once expired, instead of verifying", async () => {
    listDomains.mockResolvedValue([expired]);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Publish record expired.example" }));
    await screen.findByRole("dialog", { name: "expired.example" });

    expect(screen.getByText("This claim has expired.")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Verify now" })).toBeNull();
    expect(screen.getByRole("button", { name: "Claim again" })).toBeTruthy();
  });

  it("copies the record value to the clipboard, and says so when it can't", async () => {
    listDomains.mockResolvedValue([pending]);
    const user = userEvent.setup();
    const writeText = vi.fn().mockResolvedValue(undefined);
    renderPage();
    // After `userEvent.setup()`, which installs its own clipboard — a stub set up earlier
    // would be the one overwritten.
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });

    await user.click(await screen.findByRole("button", { name: "Publish record acme.example" }));
    await screen.findByRole("dialog", { name: "acme.example" });

    const copyButtons = screen.getAllByRole("button", { name: "Copy" });
    await user.click(copyButtons[0] as HTMLElement);

    expect(writeText).toHaveBeenCalledWith("_opsgrids-verify.acme.example");
    expect(await screen.findByText("Copied")).toBeTruthy();

    writeText.mockRejectedValueOnce(new Error("denied"));
    await user.click(screen.getAllByRole("button", { name: "Copy" })[1] as HTMLElement);
    expect(await screen.findByText(/Couldn't copy that/)).toBeTruthy();
  });
});

describe("DomainsPage — removing", () => {
  it("removes a domain after confirmation and reloads the list", async () => {
    listDomains.mockResolvedValueOnce([pending]).mockResolvedValueOnce([]);
    releaseDomain.mockResolvedValue(undefined);
    const user = userEvent.setup();
    renderPage();

    await user.click(await screen.findByRole("button", { name: "Remove acme.example" }));
    await screen.findByRole("dialog", { name: "Remove acme.example?" });

    await user.click(screen.getByRole("button", { name: "Remove" }));

    await waitFor(() => expect(releaseDomain).toHaveBeenCalledWith("dom-1"));
    await waitFor(() => expect(listDomains).toHaveBeenCalledTimes(2));
  });
});
