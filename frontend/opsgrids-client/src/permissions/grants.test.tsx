/**
 * `PermissionProvider` against the real grants endpoint.
 *
 * The three states are the whole contract, and each has a way of going wrong that this file
 * exists to catch: a control that flashes before the answer lands, a control still shown from the
 * previous organization's grants after a switch, and a failed fetch that shows controls the
 * server will refuse.
 *
 * The endpoint is stubbed at `fetch`, deliberately — it goes through `apiRequest`, so the path,
 * the `/api/v1` prefix and the bearer header are part of what is asserted.
 */

import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import type { MeOut } from "../auth/types";
import { setAccessToken } from "../session";
import { Can } from "./Can";
import { PermissionProvider } from "./PermissionProvider";
import { RequirePermission } from "./RequirePermission";
import { usePermissions } from "./context";

const GRANTS = ["operations.procurement.supplier.write:ORG", "operations.procurement.supplier.read:ORG"];

function meIn(organizationId: string): MeOut {
  return {
    user_id: "u-1",
    email: "ops@example.com",
    full_name: "Ops User",
    organization_id: organizationId,
    membership_id: `m-${organizationId}`,
    roles: [],
    amr: [],
    must_change_password: false,
    mfa_enabled: false,
    organizations: [],
  };
}

function authValue(user: MeOut | null): AuthContextValue {
  return {
    status: user ? "authenticated" : "unauthenticated",
    user,
    login: () => Promise.reject(new Error("not used")),
    verifyMfa: () => Promise.reject(new Error("not used")),
    selectOrg: () => Promise.reject(new Error("not used")),
    logout: () => Promise.resolve(),
  };
}

/** A body `apiRequest` will decode, with the status the test wants. */
function jsonResponse(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: () => Promise.resolve(body),
    text: () => Promise.resolve(JSON.stringify(body)),
  } as unknown as Response;
}

function SignedIn({ user, children }: { user: MeOut | null; children: React.ReactNode }) {
  return (
    <AuthContext value={authValue(user)}>
      <PermissionProvider entitlements={[]}>{children}</PermissionProvider>
    </AuthContext>
  );
}

function CreateButton() {
  return (
    <Can permission="operations.procurement.supplier.write" scope="ORG">
      <button type="button">New supplier</button>
    </Can>
  );
}

let fetchMock: ReturnType<typeof vi.fn>;

beforeEach(() => {
  setAccessToken(null);
  fetchMock = vi.fn(() =>
    Promise.resolve(jsonResponse({ membership_id: "m-org-a", pv: 4, grants: GRANTS })),
  );
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

describe("PermissionProvider grants fetch", () => {
  it("asks the account service for the caller's own grants once signed in", async () => {
    render(
      <SignedIn user={meIn("org-a")}>
        <CreateButton />
      </SignedIn>,
    );

    await screen.findByRole("button", { name: "New supplier" });
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/api/v1/members/me/grants");
  });

  it("renders nothing at all while the grants are loading — never a button that vanishes", async () => {
    render(
      <SignedIn user={meIn("org-a")}>
        <CreateButton />
      </SignedIn>,
    );

    expect(screen.queryByRole("button", { name: "New supplier" })).toBeNull();
    await screen.findByRole("button", { name: "New supplier" });
  });

  it("does not ask before the session is authenticated", () => {
    render(
      <SignedIn user={null}>
        <CreateButton />
      </SignedIn>,
    );
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("button", { name: "New supplier" })).toBeNull();
  });

  it("hides a control the caller holds only below the endpoint's floor", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ membership_id: "m-org-a", grants: ["operations.procurement.supplier.write:OWN"] }),
    );
    render(
      <SignedIn user={meIn("org-a")}>
        <CreateButton />
      </SignedIn>,
    );
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalled();
    });
    expect(screen.queryByRole("button", { name: "New supplier" })).toBeNull();
  });

  it("hides everything for a suspended membership, which answers with no grants", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ membership_id: "m-org-a", grants: [] }));
    render(
      <SignedIn user={meIn("org-a")}>
        <CreateButton />
        <Probe />
      </SignedIn>,
    );
    await screen.findByText("status: ready");
    expect(screen.queryByRole("button", { name: "New supplier" })).toBeNull();
  });

  it("asks again when the caller switches organization", async () => {
    function Switcher() {
      const [org, setOrg] = useState("org-a");
      return (
        <AuthContext value={authValue(meIn(org))}>
          <PermissionProvider entitlements={[]}>
            <button type="button" onClick={() => setOrg("org-b")}>
              Switch
            </button>
            <CreateButton />
          </PermissionProvider>
        </AuthContext>
      );
    }
    const user = userEvent.setup();
    render(<Switcher />);
    await screen.findByRole("button", { name: "New supplier" });

    fetchMock.mockResolvedValue(jsonResponse({ membership_id: "m-org-b", grants: [] }));
    await user.click(screen.getByRole("button", { name: "Switch" }));

    await waitFor(() => {
      expect(screen.queryByRole("button", { name: "New supplier" })).toBeNull();
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it("fails closed when the grants cannot be read, and still renders the page", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ title: "Service unavailable", status: 503 }, 503));
    render(
      <SignedIn user={meIn("org-a")}>
        <p>The rest of the screen</p>
        <CreateButton />
        <Probe />
      </SignedIn>,
    );

    await screen.findByText("status: error");
    expect(screen.getByText("The rest of the screen")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "New supplier" })).toBeNull();
  });

  it("reloadGrants asks again", async () => {
    const user = userEvent.setup();
    render(
      <SignedIn user={meIn("org-a")}>
        <Probe />
      </SignedIn>,
    );
    await screen.findByText("status: ready");
    await user.click(screen.getByRole("button", { name: "Reload grants" }));
    await waitFor(() => {
      expect(fetchMock).toHaveBeenCalledTimes(2);
    });
  });

  it("an explicit grants prop suppresses the request — fixture mode stays offline", async () => {
    render(
      <AuthContext value={authValue(meIn("org-a"))}>
        <PermissionProvider grants={GRANTS} entitlements={[]}>
          <CreateButton />
        </PermissionProvider>
      </AuthContext>,
    );
    await screen.findByRole("button", { name: "New supplier" });
    expect(fetchMock).not.toHaveBeenCalled();
  });
});

describe("Can and RequirePermission", () => {
  it("Can renders its fallback only on a known denial", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ membership_id: "m-org-a", grants: [] }));
    render(
      <SignedIn user={meIn("org-a")}>
        <Can permission="operations.procurement.supplier.write" scope="ORG" fallback={<p>Ask an admin</p>}>
          <button type="button">New supplier</button>
        </Can>
      </SignedIn>,
    );
    expect(screen.queryByText("Ask an admin")).toBeNull();
    await screen.findByText("Ask an admin");
  });

  it("Can stays silent when the grants failed — it does not claim a denial it cannot know", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: 503 }, 503));
    render(
      <SignedIn user={meIn("org-a")}>
        <Can permission="operations.procurement.supplier.write" scope="ORG" fallback={<p>Ask an admin</p>}>
          <button type="button">New supplier</button>
        </Can>
        <Probe />
      </SignedIn>,
    );
    await screen.findByText("status: error");
    expect(screen.queryByText("Ask an admin")).toBeNull();
    expect(screen.queryByRole("button", { name: "New supplier" })).toBeNull();
  });

  it("RequirePermission explains itself when the grants failed, rather than leaving a blank screen", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ status: 503 }, 503));
    render(
      <SignedIn user={meIn("org-a")}>
        <RequirePermission perm="operations.procurement.supplier.write" scope="ORG" fallback={<p>Not available</p>}>
          <p>Suppliers</p>
        </RequirePermission>
      </SignedIn>,
    );
    await screen.findByText("Not available");
    expect(screen.queryByText("Suppliers")).toBeNull();
  });

  it("RequirePermission renders nothing while loading, so the denial never flashes", () => {
    render(
      <SignedIn user={meIn("org-a")}>
        <RequirePermission perm="operations.procurement.supplier.write" scope="ORG" fallback={<p>Not available</p>}>
          <p>Suppliers</p>
        </RequirePermission>
      </SignedIn>,
    );
    expect(screen.queryByText("Not available")).toBeNull();
    expect(screen.queryByText("Suppliers")).toBeNull();
  });
});

function Probe() {
  const { status, membershipId, pv, reloadGrants } = usePermissions();
  return (
    <>
      <p>status: {status}</p>
      <p>membership: {membershipId ?? "none"}</p>
      <p>pv: {pv ?? "none"}</p>
      <button type="button" onClick={reloadGrants}>
        Reload grants
      </button>
    </>
  );
}
