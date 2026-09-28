/**
 * "Which `workers` row am I": the access token's `sref` claim matched against the org's roster
 * (`WorkerProvider.tsx`'s own doc comment). Drives the auth context directly with a fake value
 * rather than a real sign-in — this is a unit test of the resolution, not of `AuthProvider`.
 */
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, setAccessToken } from "@opsgrids/client";
import type { AuthContextValue } from "@opsgrids/client";

import { useMyWorker } from "./context";
import { WorkerProvider } from "./WorkerProvider";

function fakeAuth(status: AuthContextValue["status"]): AuthContextValue {
  return {
    status,
    user:
      status === "authenticated"
        ? {
            user_id: "u-1",
            email: "asha@example.com",
            full_name: "Asha Rao",
            organization_id: "org-1",
            membership_id: "mem-1",
            roles: [],
            amr: ["pwd"],
            must_change_password: false,
            mfa_enabled: false,
            organizations: [],
          }
        : null,
    login: vi.fn(),
    verifyMfa: vi.fn(),
    selectOrg: vi.fn(),
    logout: vi.fn(),
  };
}

/** A syntactically valid, unsigned JWT carrying only the claim this hook reads. */
function fakeToken(claims: Record<string, unknown>): string {
  const base64url = (obj: unknown) => btoa(JSON.stringify(obj)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${base64url({ alg: "none" })}.${base64url(claims)}.`;
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function Probe() {
  const worker = useMyWorker();
  return <div data-testid="status">{worker.status}{worker.worker ? `:${worker.worker.id}` : ""}</div>;
}

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

describe("WorkerProvider", () => {
  it("matches the token's sref claim to the worker roster", async () => {
    setAccessToken(fakeToken({ sref: "employee-1" }));
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          json({
            items: [
              { id: "worker-1", employee_id: "employee-1", warehouse_id: "wh-1", display_name: "Asha", is_active: true, skills: [], presence_status: "online", presence_last_seen_at: null, zone_location_ids: null, created_at: "2026-09-29T09:00:00Z", updated_at: "2026-09-29T09:00:00Z" },
            ],
            total: 1,
            page: 1,
            size: 200,
            pages: 1,
          }),
        ),
      ),
    );

    render(
      <AuthContext value={fakeAuth("authenticated")}>
        <WorkerProvider>
          <Probe />
        </WorkerProvider>
      </AuthContext>,
    );

    expect(await screen.findByText("ready:worker-1")).toBeTruthy();
  });

  it("no-identity: an account with no sref claim never even calls the roster", async () => {
    setAccessToken(fakeToken({}));
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    render(
      <AuthContext value={fakeAuth("authenticated")}>
        <WorkerProvider>
          <Probe />
        </WorkerProvider>
      </AuthContext>,
    );

    expect(await screen.findByText("no-identity")).toBeTruthy();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("not-found: an sref that names nobody on this organization's roster", async () => {
    setAccessToken(fakeToken({ sref: "employee-nobody" }));
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(json({ items: [], total: 0, page: 1, size: 200, pages: 0 }))),
    );

    render(
      <AuthContext value={fakeAuth("authenticated")}>
        <WorkerProvider>
          <Probe />
        </WorkerProvider>
      </AuthContext>,
    );

    expect(await screen.findByText("not-found")).toBeTruthy();
  });
});
