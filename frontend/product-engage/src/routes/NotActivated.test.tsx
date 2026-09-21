import { render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";

import { ApiError } from "@opsgrids/client";

import * as client from "@/api/client";
import { isNotActivated } from "@/lib/errors";
import { ConnectionPage } from "./Connection";
import { DomainsPage } from "./Domains";
import { OverviewPage } from "./Overview";
import { SendersPage } from "./Senders";

/**
 * The gateway answers every `/engage` read with a `402` until the organization has Engage, and
 * that reached production as a red "Couldn't load your sending identity — engage is not active
 * for this organization" on the Overview of an owner who had done nothing wrong.
 *
 * What is pinned here: a `402` on any of the four screens is the not-active state, with the way
 * to turn Engage on and none of the error's dress; and everything that is not a `402` — a `500`,
 * a `403`, a request that never reached the gateway — is still the error it always was.
 */

/** The gateway's own answer, as `PaymentRequiredProblem` puts it on the wire. */
const NOT_ENTITLED = new ApiError(402, "engage is not active for this organization", {
  type: "product-not-entitled",
  title: "Product not available on this plan",
  status: 402,
  detail: "engage is not active for this organization",
});

const NOT_ACTIVE_TITLE = "Engage isn't active for this organization yet";

/** The dashboard's Products page — its site in the edge Caddyfile, its index route. Spelled out
 *  rather than imported, so a change to the constant has to change this line too. */
const PRODUCTS_URL = "https://app.opsgrids.com/";

/** Every read the four screens make fails the same way; each screen calls its own subset. */
function failEveryRead(error: Error) {
  vi.spyOn(client, "getConnection").mockRejectedValue(error);
  vi.spyOn(client, "listDomains").mockRejectedValue(error);
  vi.spyOn(client, "listSenders").mockRejectedValue(error);
}

const SCREENS: { name: string; page: () => ReactElement; errorTitle: string }[] = [
  { name: "Overview", page: () => <OverviewPage />, errorTitle: "Couldn't load your sending identity" },
  { name: "Connections", page: () => <ConnectionPage />, errorTitle: "Couldn't load the connection" },
  { name: "Domains", page: () => <DomainsPage />, errorTitle: "Couldn't load your domains" },
  { name: "Senders", page: () => <SendersPage />, errorTitle: "Couldn't load your senders" },
];

function mount(page: ReactElement) {
  render(<MemoryRouter>{page}</MemoryRouter>);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe.each(SCREENS)("$name", ({ page, errorTitle }) => {
  it("shows a 402 as Engage not being active yet, with the way to turn it on", async () => {
    failEveryRead(NOT_ENTITLED);
    mount(page());

    expect(await screen.findByText(NOT_ACTIVE_TITLE)).toBeTruthy();
    const link = screen.getByRole("link", { name: /Open Products in the dashboard/ });
    expect(link.getAttribute("href")).toBe(PRODUCTS_URL);
  });

  it("does not dress a 402 as a failure", async () => {
    failEveryRead(NOT_ENTITLED);
    mount(page());
    await screen.findByText(NOT_ACTIVE_TITLE);

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.queryByText(errorTitle)).toBeNull();
    expect(screen.queryByText(/Couldn't load/)).toBeNull();
    expect(document.querySelector(".text-negative")).toBeNull();
    // The gateway's sentence names the product key in lower case and reads as a fault; the
    // state says the same fact in its own words.
    expect(document.body.textContent).not.toContain("engage is not active");
  });

  it("still shows a 500 as the error it is", async () => {
    failEveryRead(new ApiError(500, "Internal Server Error", null));
    mount(page());

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain(errorTitle);
    expect(alert.textContent).toContain("Internal Server Error");
    expect(screen.queryByText(NOT_ACTIVE_TITLE)).toBeNull();
    expect(screen.queryByRole("link", { name: /Open Products in the dashboard/ })).toBeNull();
  });
});

describe("what is not a 402", () => {
  it("keeps a 403 an error — a permission fact is not a plan fact", async () => {
    failEveryRead(new ApiError(403, "You do not have engage.domain.read", null));
    mount(<DomainsPage />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load your domains");
    expect(screen.queryByText(NOT_ACTIVE_TITLE)).toBeNull();
  });

  it("keeps a network failure an error", async () => {
    failEveryRead(new TypeError("Failed to fetch"));
    mount(<OverviewPage />);

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Couldn't load your sending identity");
    expect(screen.queryByText(NOT_ACTIVE_TITLE)).toBeNull();
  });

  it("is decided by the status, not by the gateway's wording", () => {
    expect(isNotActivated(NOT_ENTITLED)).toBe(true);
    expect(isNotActivated(new ApiError(402, "a sentence the gateway may reword", null))).toBe(true);
    expect(isNotActivated(new ApiError(403, "engage is not active for this organization", null))).toBe(false);
    expect(isNotActivated(new ApiError(500, "engage is not active for this organization", null))).toBe(false);
    expect(isNotActivated(new Error("engage is not active for this organization"))).toBe(false);
  });
});
