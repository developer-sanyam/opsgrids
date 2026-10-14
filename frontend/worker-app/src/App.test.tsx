/**
 * The shell's own contract: the put-away queue's drainer lives here, not on any one screen; the
 * Put away entry appears in navigation only for someone who may use it; sign-out empties the queue
 * (after asking, if put-aways would be lost); and a refused put-away is surfaced wherever the worker
 * happens to be.
 */
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter, Route, Routes } from "react-router-dom";

import { AuthContext, PermissionProvider } from "@opsgrids/client";
import type { AuthContextValue, MeOut } from "@opsgrids/client";
import { ThemeProvider } from "@opsgrids/ui";

import { enqueuePutaway, getPutawayQueue, parkPutaway, purgePutawayQueue } from "@/lib/putawayQueue";
import { json, makePutawayOut, stubGateway } from "@/worker/testKit";

import { AppShell } from "./App";

const ORG = {
  organization_id: "org-1",
  slug: "acme",
  name: "Acme Fulfilment",
  roles: ["operations_worker"],
  role_names: ["Warehouse worker"],
};

const ME: MeOut = {
  user_id: "user-1",
  email: "asha@acme.example",
  full_name: "Asha Rao",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: ["operations_worker"],
  role_names: ["Warehouse worker"],
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [ORG],
};

const PUTAWAY = "POST /api/v1/operations/inventory/putaway";
const STORAGE_KEY = "opsgrids.worker.putawayQueue";
const WITH_PUTAWAY = ["operations.inventory.stock.transfer:ORG", "operations.fulfilment.task.read:ORG"];

function seed(owner: { ownerSub: string; organizationId: string | null } = { ownerSub: "user-1", organizationId: "org-1" }) {
  return enqueuePutaway({
    ...owner,
    body: {
      inventory_item_id: "item-1",
      from_location_id: "loc-rcv-1",
      to_barcode: "A-01-03",
      quantity: "3",
      captured_at: "2026-09-28T10:00:00.000Z",
      occurred_on: "2026-09-28",
      lat_micro: 12_971_599,
      lng_micro: 77_594_563,
    },
    summary: { sku: "SKU-100", itemName: "Cotton roll", uom: "m", fromLocationCode: "RCV-01", toBarcode: "A-01-03" },
  });
}

const logout = vi.fn<() => Promise<void>>();

function renderShell({ grants = WITH_PUTAWAY, at = "/" }: { grants?: string[]; at?: string } = {}) {
  const auth: AuthContextValue = {
    status: "authenticated",
    user: ME,
    login: vi.fn(),
    verifyMfa: vi.fn(),
    selectOrg: vi.fn(),
    logout,
  };
  return render(
    <ThemeProvider storageKey="opsgrids.test.theme">
      <MemoryRouter initialEntries={[at]}>
        <AuthContext value={auth}>
          <PermissionProvider grants={grants}>
            <Routes>
              <Route element={<AppShell />}>
                <Route index element={<h1>Home screen</h1>} />
                <Route path="putaway" element={<h1>Put away screen</h1>} />
              </Route>
            </Routes>
          </PermissionProvider>
        </AuthContext>
      </MemoryRouter>
    </ThemeProvider>,
  );
}

async function openSignOut(user: ReturnType<typeof userEvent.setup>) {
  const trigger = screen.getAllByRole("button", { name: /Account menu/ })[0];
  if (!trigger) throw new Error("account menu trigger not found");
  await user.click(trigger);
  await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
}

beforeEach(() => {
  window.scrollTo = vi.fn();
  logout.mockReset();
  logout.mockResolvedValue(undefined);
});

afterEach(() => {
  vi.unstubAllGlobals();
  purgePutawayQueue();
  localStorage.clear();
});

describe("AppShell — the put-away drainer", () => {
  it("syncs a saved put-away while the worker is on Home, never having opened Put away", async () => {
    const entry = seed();
    const { calls } = stubGateway({ [PUTAWAY]: () => json(makePutawayOut(), 201) });
    renderShell();

    expect(await screen.findByRole("heading", { name: "Home screen" })).toBeTruthy();
    await waitFor(() => expect(calls.filter((call) => call.key === PUTAWAY)).toHaveLength(1));
    const [sent] = calls;
    expect(sent?.headers["idempotency-key"]).toBe(entry.idempotencyKey);
    expect(sent?.body).toEqual(entry.body);
    await waitFor(() => expect(getPutawayQueue()).toEqual([]));
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("does not send another account's saved put-aways", async () => {
    seed({ ownerSub: "user-2", organizationId: "org-1" });
    seed({ ownerSub: "user-1", organizationId: "org-2" });
    const { calls } = stubGateway({ [PUTAWAY]: () => json(makePutawayOut(), 201) });
    renderShell();

    await screen.findByRole("heading", { name: "Home screen" });

    expect(calls).toHaveLength(0);
    expect(getPutawayQueue()).toHaveLength(2);
  });

  it("stays quiet with nothing to send", async () => {
    const { calls } = stubGateway({});
    renderShell();

    await screen.findByRole("heading", { name: "Home screen" });

    expect(calls).toHaveLength(0);
  });
});

describe("AppShell — navigation", () => {
  it("lists Put away in the sidebar and the phone's bottom bar for a worker who may use it", () => {
    renderShell();

    const links = screen.getAllByRole("link", { name: /Put away/ });
    expect(links.length).toBeGreaterThan(0);
    expect(links.every((link) => link.getAttribute("href") === "/putaway")).toBe(true);

    const bottom = screen.getByRole("navigation", { name: "Primary mobile navigation" });
    const labels = within(bottom)
      .getAllByRole("link")
      .map((link) => link.textContent);
    expect(labels).toEqual(["Home", "Counts", "Put away"]);
  });

  it("puts Profile in the header's account menu, not in a bottom slot", async () => {
    const user = userEvent.setup();
    renderShell();

    const bottom = screen.getByRole("navigation", { name: "Primary mobile navigation" });
    expect(within(bottom).queryByRole("link", { name: "Profile" })).toBeNull();

    const trigger = screen.getAllByRole("button", { name: /Account menu/ })[0];
    if (!trigger) throw new Error("account menu trigger not found");
    await user.click(trigger);
    expect(screen.getByRole("menuitem", { name: "Profile" })).toBeTruthy();
  });

  it("leaves it out for one who may not — the screen is gated too, but a dead entry is noise", () => {
    renderShell({ grants: ["operations.fulfilment.task.read:ORG"] });

    expect(screen.queryByRole("link", { name: /Put away/ })).toBeNull();
    const bottom = screen.getByRole("navigation", { name: "Primary mobile navigation" });
    expect(
      within(bottom)
        .getAllByRole("link")
        .map((link) => link.textContent),
    ).toEqual(["Home", "Counts"]);
  });

  it("an OWN grant is not enough to show it", () => {
    renderShell({ grants: ["operations.inventory.stock.transfer:OWN"] });

    expect(screen.queryByRole("link", { name: /Put away/ })).toBeNull();
  });
});

describe("AppShell — sign out", () => {
  it("with nothing saved, signs out at once and leaves nothing behind", async () => {
    const user = userEvent.setup();
    stubGateway({});
    renderShell();

    await openSignOut(user);

    expect(logout).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("with put-aways that never reached the server, asks first — and signing out discards them, coordinates included", async () => {
    const user = userEvent.setup();
    // Offline: nothing syncs in the background, so what is on the phone is exactly what is at risk.
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    seed();
    seed({ ownerSub: "user-2", organizationId: "org-1" });
    stubGateway({});
    renderShell();

    await openSignOut(user);

    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText("Sign out and discard put-aways?")).toBeTruthy();
    expect(within(dialog).getByText(/2 put-aways on this phone haven't been recorded/)).toBeTruthy();
    expect(logout).not.toHaveBeenCalled();
    expect(getPutawayQueue()).toHaveLength(2);

    await user.click(within(dialog).getByRole("button", { name: "Sign out and discard" }));

    expect(logout).toHaveBeenCalledTimes(1);
    // Everyone's entries go, not just the signed-in user's: a shared handset keeps nothing.
    expect(getPutawayQueue()).toEqual([]);
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("a put-away confirmed while the sign-out request is still on its way does not outlive the sign-out", async () => {
    const user = userEvent.setup();
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    let finishLogout: (() => void) | undefined;
    logout.mockReturnValue(
      new Promise<void>((resolve) => {
        finishLogout = resolve;
      }),
    );
    seed();
    stubGateway({});
    renderShell();

    await openSignOut(user);
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Sign out and discard" }));
    expect(getPutawayQueue()).toEqual([]);

    // The logout round trip is slow and the Put away screen is still usable: a confirm lands now.
    seed();
    expect(getPutawayQueue()).toHaveLength(1);

    finishLogout?.();
    await waitFor(() => expect(getPutawayQueue()).toEqual([]));
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it("staying signed in keeps the queue and the session", async () => {
    const user = userEvent.setup();
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    seed();
    stubGateway({});
    renderShell();

    await openSignOut(user);
    await user.click(within(await screen.findByRole("dialog")).getByRole("button", { name: "Stay signed in" }));

    expect(logout).not.toHaveBeenCalled();
    expect(getPutawayQueue()).toHaveLength(1);
  });

  it("a refused put-away waiting to be read counts as one that would be lost", async () => {
    const user = userEvent.setup();
    vi.spyOn(window.navigator, "onLine", "get").mockReturnValue(false);
    const entry = seed();
    parkPutaway(entry.idempotencyKey, { httpStatus: 409, message: "reserved" });
    stubGateway({});
    renderShell();

    await openSignOut(user);

    expect(await screen.findByText(/1 put-away on this phone hasn't been recorded/)).toBeTruthy();
  });
});

describe("AppShell — put-aways that need attention", () => {
  function parkOne() {
    const entry = seed();
    parkPutaway(entry.idempotencyKey, { httpStatus: 409, message: "That stock is reserved." });
  }

  it("says so on any other screen, with a way to the Put away screen", async () => {
    parkOne();
    stubGateway({});
    renderShell();

    expect(await screen.findByText("1 put-away couldn't be recorded")).toBeTruthy();
    expect(screen.getByRole("link", { name: "Review" }).getAttribute("href")).toBe("/putaway");
  });

  it("stays out of the way on the Put away screen, where the same entries are listed in full", async () => {
    parkOne();
    stubGateway({});
    renderShell({ at: "/putaway" });

    await screen.findByRole("heading", { name: "Put away screen" });

    expect(screen.queryByText(/couldn't be recorded/)).toBeNull();
  });
});
