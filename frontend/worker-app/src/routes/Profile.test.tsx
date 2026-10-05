/**
 * Who is signed in, and signing out — real today because it needs nothing beyond `GET /auth/me`,
 * which `AuthProvider` already loads at boot for every screen.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, getAccessToken, setAccessToken } from "@opsgrids/client";

import { enqueuePutaway, getPutawayQueue, purgePutawayQueue } from "@/lib/putawayQueue";

import { ProfilePage } from "./Profile";

const ORG = {
  organization_id: "org-a",
  slug: "acme-fulfilment",
  name: "Acme Fulfilment",
  roles: ["fulfilment_worker"],
  role_names: ["Fulfilment Worker"],
};

const ME = {
  user_id: "u-1",
  email: "asha.picker@acme.example.com",
  full_name: "Asha Rao",
  organization_id: ORG.organization_id,
  membership_id: "mem-1",
  roles: ["fulfilment_worker"],
  role_names: ["Fulfilment Worker"],
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [ORG],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

let routes: Record<string, () => Response>;

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      const route = routes[url];
      if (!route) throw new Error(`No stub for ${url}`);
      return Promise.resolve(route());
    }),
  );
}

function renderPage() {
  return render(
    <AuthProvider>
      <ProfilePage />
    </AuthProvider>,
  );
}

beforeEach(() => {
  routes = {};
  setAccessToken(null);
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

it("shows the signed-in worker's name, email, role and organization", async () => {
  setAccessToken("session-token");
  routes = { "/api/v1/auth/me": () => json(ME) };
  renderPage();

  expect(await screen.findByText("Asha Rao")).toBeTruthy();
  expect(screen.getByText("asha.picker@acme.example.com")).toBeTruthy();
  expect(screen.getByText("Fulfilment Worker")).toBeTruthy();
  expect(screen.getByText("Acme Fulfilment")).toBeTruthy();
});

describe("sign out", () => {
  it("clears the session and disables the button so a second click cannot double-submit", async () => {
    const user = userEvent.setup();
    setAccessToken("session-token");
    let resolveLogout: ((response: Response) => void) | undefined;
    const pendingLogout = new Promise<Response>((resolve) => {
      resolveLogout = resolve;
    });
    routes = {
      "/api/v1/auth/me": () => json(ME),
      "/api/v1/auth/logout": () => json({}),
    };
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/api/v1/auth/logout") return pendingLogout;
        const route = routes[url];
        if (!route) throw new Error(`No stub for ${url}`);
        return Promise.resolve(route());
      }),
    );
    renderPage();
    await screen.findByText("Asha Rao");

    const signOut = screen.getByRole("button", { name: "Sign out" }) as HTMLButtonElement;
    await user.click(signOut);

    expect(signOut.disabled).toBe(true);
    await user.click(signOut); // A click on a disabled button reaches nothing.

    resolveLogout?.(json({}));
    await waitFor(() => expect(getAccessToken()).toBeNull());
  });

  describe("the put-away queue", () => {
    function seedPutaway() {
      return enqueuePutaway({
        ownerSub: "u-1",
        organizationId: ORG.organization_id,
        body: {
          inventory_item_id: "item-1",
          from_location_id: "loc-rcv-1",
          to_barcode: "A-01-03",
          quantity: "3",
          lat_micro: 12_971_599,
          lng_micro: 77_594_563,
        },
        summary: { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" },
      });
    }

    afterEach(() => {
      purgePutawayQueue();
      localStorage.clear();
    });

    it("is purged on sign-out — and an empty queue signs out without a question", async () => {
      const user = userEvent.setup();
      setAccessToken("session-token");
      routes = { "/api/v1/auth/me": () => json(ME), "/api/v1/auth/logout": () => json({}) };
      renderPage();
      await screen.findByText("Asha Rao");

      await user.click(screen.getByRole("button", { name: "Sign out" }));

      expect(screen.queryByRole("dialog")).toBeNull();
      await waitFor(() => expect(getAccessToken()).toBeNull());
      expect(getPutawayQueue()).toEqual([]);
    });

    it("asks before throwing away put-aways that never reached the server, then purges them with the session", async () => {
      const user = userEvent.setup();
      setAccessToken("session-token");
      routes = { "/api/v1/auth/me": () => json(ME), "/api/v1/auth/logout": () => json({}) };
      seedPutaway();
      renderPage();
      await screen.findByText("Asha Rao");

      await user.click(screen.getByRole("button", { name: "Sign out" }));
      const dialog = await screen.findByRole("dialog");
      expect(within(dialog).getByText(/1 put-away on this phone hasn't been recorded/)).toBeTruthy();
      expect(getAccessToken()).toBe("session-token");
      expect(getPutawayQueue()).toHaveLength(1);

      await user.click(within(dialog).getByRole("button", { name: "Sign out and discard" }));

      await waitFor(() => expect(getAccessToken()).toBeNull());
      expect(getPutawayQueue()).toEqual([]);
      expect(localStorage.getItem("opsgrids.worker.putawayQueue")).toBeNull();
    });

    it("Stay signed in changes nothing", async () => {
      const user = userEvent.setup();
      setAccessToken("session-token");
      routes = { "/api/v1/auth/me": () => json(ME) };
      seedPutaway();
      renderPage();
      await screen.findByText("Asha Rao");

      await user.click(screen.getByRole("button", { name: "Sign out" }));
      await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stay signed in" }));

      expect(getAccessToken()).toBe("session-token");
      expect(getPutawayQueue()).toHaveLength(1);
    });
  });
});
