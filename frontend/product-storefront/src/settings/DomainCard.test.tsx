import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "@opsgrids/client";

import * as domain from "./domain";
import { DomainCard } from "./DomainCard";
import type { CheckState, DomainOut } from "./domain";

const PENDING: CheckState = {
  state: "pending",
  last_checked_at: null,
  next_check_at: null,
  code: null,
  detail: null,
  not_after: null,
};

const UNCLAIMED: DomainOut = {
  custom_domain: null,
  status: null,
  platform_host: "greenleaf.stores.opsgrids.com",
  canonical_host: "greenleaf.stores.opsgrids.com",
  verification: null,
  dns: null,
  checks: null,
};

const CLAIMED: DomainOut = {
  ...UNCLAIMED,
  custom_domain: "shop.example.com",
  status: "unverified",
  canonical_host: "shop.example.com",
  verification: {
    record_type: "TXT",
    name: "_opsgrids-challenge.shop.example.com",
    value: "opsgrids-domain-verification=abc123",
    issued_at: "2026-09-23T05:00:00Z",
    claim_expires_at: "2026-10-07T05:00:00Z",
  },
  dns: {
    apex: false,
    preferred: { record_type: "CNAME", name: "shop.example.com", value: "edge.opsgrids.com", note: null },
    fallback: [{ record_type: "A", name: "shop.example.com", value: "20.219.18.84", note: null }],
    proxy_warning: "Turn off any DNS proxy for these records.",
    also_served: [],
  },
  checks: { ownership: PENDING, routing: PENDING, certificate: PENDING },
};

function problem(status: number, code: string, message: string) {
  return new ApiError(status, message, {
    title: "Refused",
    status,
    detail: message,
    errors: [{ field: "custom_domain", code, message }],
  } as ConstructorParameters<typeof ApiError>[2]);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("DomainCard — nothing claimed", () => {
  it("shows the platform address and claims the typed domain, lower-cased", async () => {
    vi.spyOn(domain, "getDomain").mockResolvedValue(UNCLAIMED);
    const claim = vi.spyOn(domain, "claimDomain").mockResolvedValue(CLAIMED);
    const user = userEvent.setup();
    render(<DomainCard canWrite />);

    expect(await screen.findByText("greenleaf.stores.opsgrids.com")).toBeTruthy();
    await user.type(screen.getByLabelText("Domain"), "  Shop.Example.COM ");
    await user.click(screen.getByRole("button", { name: "Connect domain" }));

    await waitFor(() => expect(claim).toHaveBeenCalledWith("shop.example.com"));
    const txt = await screen.findByRole("table", { name: "Verification record" });
    expect(within(txt).getByText("opsgrids-domain-verification=abc123")).toBeTruthy();
    expect(screen.getByText("Waiting for DNS")).toBeTruthy();
  });

  it("refuses an empty domain without calling the server", async () => {
    vi.spyOn(domain, "getDomain").mockResolvedValue(UNCLAIMED);
    const claim = vi.spyOn(domain, "claimDomain");
    const user = userEvent.setup();
    render(<DomainCard canWrite />);

    await user.click(await screen.findByRole("button", { name: "Connect domain" }));
    expect(screen.getByText(/enter the domain you want buyers to use/i)).toBeTruthy();
    expect(claim).not.toHaveBeenCalled();
  });

  it("puts a taken domain on the field, without naming who holds it", async () => {
    vi.spyOn(domain, "getDomain").mockResolvedValue(UNCLAIMED);
    vi.spyOn(domain, "claimDomain").mockRejectedValue(
      problem(409, "custom_domain_taken", "That domain is already connected."),
    );
    const user = userEvent.setup();
    render(<DomainCard canWrite />);

    await user.type(await screen.findByLabelText("Domain"), "shop.example.com");
    await user.click(screen.getByRole("button", { name: "Connect domain" }));

    expect(await screen.findByText("Another store already uses this domain.")).toBeTruthy();
  });

  it("offers no claim form without write access", async () => {
    vi.spyOn(domain, "getDomain").mockResolvedValue(UNCLAIMED);
    render(<DomainCard canWrite={false} />);

    expect(await screen.findByText("greenleaf.stores.opsgrids.com")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Connect domain" })).toBeNull();
  });
});

describe("DomainCard — a claimed domain", () => {
  it("shows the records and the three checks, with no write controls for a reader", async () => {
    vi.spyOn(domain, "getDomain").mockResolvedValue(CLAIMED);
    render(<DomainCard canWrite={false} />);

    expect(await screen.findByRole("table", { name: "Routing record" })).toBeTruthy();
    expect(screen.getByRole("table", { name: "Fallback routing records" })).toBeTruthy();
    expect(screen.getByText("Ownership (TXT record)")).toBeTruthy();
    expect(screen.getByText("HTTPS certificate")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Check now" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Disconnect" })).toBeNull();
  });

  it("explains the check rate limit instead of a generic failure", async () => {
    vi.spyOn(domain, "getDomain").mockResolvedValue(CLAIMED);
    vi.spyOn(domain, "checkDomain").mockRejectedValue(
      new ApiError(429, "Too Many Requests", { title: "Too Many Requests", status: 429 }),
    );
    const user = userEvent.setup();
    render(<DomainCard canWrite />);

    await user.click(await screen.findByRole("button", { name: "Check now" }));
    expect(await screen.findByText(/at most once every 30 seconds/i)).toBeTruthy();
  });

  it("disconnects only after confirmation, then shows the unclaimed state", async () => {
    const get = vi.spyOn(domain, "getDomain").mockResolvedValueOnce(CLAIMED).mockResolvedValue(UNCLAIMED);
    const release = vi.spyOn(domain, "releaseDomain").mockResolvedValue();
    const user = userEvent.setup();
    render(<DomainCard canWrite />);

    await user.click(await screen.findByRole("button", { name: "Disconnect" }));
    expect(release).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: "Disconnect this domain?" });
    await user.click(within(dialog).getByRole("button", { name: "Disconnect" }));

    await waitFor(() => expect(release).toHaveBeenCalledTimes(1));
    expect(await screen.findByRole("button", { name: "Connect domain" })).toBeTruthy();
    expect(get).toHaveBeenCalledTimes(2);
  });
});
