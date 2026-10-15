import { render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext } from "../auth/context";
import type { AuthContextValue } from "../auth/context";
import type { MeOut } from "../auth/types";
import { PermissionProvider } from "../permissions/PermissionProvider";
import { UNKNOWN_MEMBER_NAME, useMemberNames } from "./useMemberNames";

const ME: MeOut = {
  user_id: "11111111-1111-1111-1111-111111111111",
  email: "casey@example.com",
  full_name: "Casey Rivera",
  organization_id: "org-a",
  membership_id: "m-1",
  roles: [],
  role_names: [],
  amr: [],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [],
};

const COLLEAGUE = "22222222-2222-2222-2222-222222222222";
const STRANGER = "33333333-3333-3333-3333-333333333333";

const auth: AuthContextValue = {
  status: "authenticated",
  user: ME,
  login: () => Promise.reject(new Error("not used")),
  verifyMfa: () => Promise.reject(new Error("not used")),
  selectOrg: () => Promise.reject(new Error("not used")),
  logout: () => Promise.resolve(),
};

function Probe({ ids }: { ids: string[] }) {
  const nameOf = useMemberNames();
  return (
    <ul>
      {ids.map((id) => (
        <li key={id}>{`${id.slice(0, 2)}: ${nameOf(id)}`}</li>
      ))}
    </ul>
  );
}

function renderProbe(grants: string[]) {
  return render(
    <AuthContext value={auth}>
      <PermissionProvider grants={grants} entitlements={[]}>
        <Probe ids={[ME.user_id, COLLEAGUE, STRANGER]} />
      </PermissionProvider>
    </AuthContext>,
  );
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("useMemberNames", () => {
  it("resolves ids to names from the member directory, and never shows an id", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response(
            JSON.stringify({
              items: [{ user_id: COLLEAGUE, full_name: "Priya Nair", display_name: null }],
            }),
            { status: 200, headers: { "content-type": "application/json" } },
          ),
        ),
      ),
    );
    renderProbe(["core.identity.membership.read:ORG"]);

    await waitFor(() => expect(screen.getByText("22: Priya Nair")).toBeTruthy());
    expect(screen.getByText("11: Casey Rivera")).toBeTruthy();
    expect(screen.getByText(`33: ${UNKNOWN_MEMBER_NAME}`)).toBeTruthy();
    expect(document.body.textContent).not.toContain(STRANGER);
  });

  it("without the directory permission it asks nobody and still shows no id", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    renderProbe([]);

    expect(screen.getByText("11: Casey Rivera")).toBeTruthy();
    expect(screen.getByText(`22: ${UNKNOWN_MEMBER_NAME}`)).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("falls back to the neutral name when the directory read fails", async () => {
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(new Response("{}", { status: 500 }))));
    renderProbe(["core.identity.membership.read:ORG"]);

    await waitFor(() => expect(screen.getByText(`22: ${UNKNOWN_MEMBER_NAME}`)).toBeTruthy());
  });
});
