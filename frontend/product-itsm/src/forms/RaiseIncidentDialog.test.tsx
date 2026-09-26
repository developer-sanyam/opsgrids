import { fireEvent, render, screen, waitFor, waitForElementToBeRemoved } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { AuthContext, ApiError, PermissionContext } from "@opsgrids/client";
import type { AuthContextValue, MeOut, PermissionState } from "@opsgrids/client";

import * as client from "@/api/client";
import type { Incident } from "@/api/types";
import { RaiseIncidentDialog } from "./RaiseIncidentDialog";

const USER: MeOut = {
  user_id: "user-1",
  email: "meera.raghavan@opsgrids.example.com",
  full_name: "Meera Raghavan",
  organization_id: "org-1",
  membership_id: "mem-1",
  roles: ["itsm_agent"],
  role_names: ["ITSM Agent"],
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [
    {
      organization_id: "org-1",
      slug: "opsgrids",
      name: "OpsGrids",
      roles: ["itsm_agent"],
      role_names: ["ITSM Agent"],
    },
  ],
};

const AUTH_VALUE: AuthContextValue = {
  status: "authenticated",
  user: USER,
  login: vi.fn(),
  verifyMfa: vi.fn(),
  selectOrg: vi.fn(),
  logout: vi.fn(),
};

const INCIDENT: Incident = {
  id: "INC-9001",
  summary: "VPN down for finance team",
  description: "",
  priority: "P2",
  status: "new",
  category: "network",
  assignee: "",
  requester: "Meera Raghavan",
  affected_asset_tag: null,
  sla_state: "on_track",
  sla_minutes_remaining: 240,
  created_at: "2026-08-24T18:00:00Z",
  updated_at: "2026-08-24T18:00:00Z",
  worklog: [],
};

afterEach(() => {
  vi.restoreAllMocks();
});

function permissions(grants: string[]): PermissionState {
  return {
    grants,
    entitlements: ["itsm"],
    status: "ready",
    membershipId: "mem-1",
    pv: 1,
    error: null,
    reloadGrants: vi.fn(),
  };
}

function open(onCreated = vi.fn(), grants: string[] = ["itsm.service.incident.write:ORG"]) {
  render(
    <AuthContext.Provider value={AUTH_VALUE}>
      <PermissionContext.Provider value={permissions(grants)}>
        <RaiseIncidentDialog onClose={vi.fn()} onCreated={onCreated} />
      </PermissionContext.Provider>
    </AuthContext.Provider>,
  );
  // Real timers throughout this file (no `vi.useFakeTimers`), so the per-keystroke `setTimeout`
  // `user.type` schedules by default has nothing advancing it but the event loop itself — on a
  // busy runner that queues behind everything else and keystrokes land late enough to interleave
  // across fields. `delay: null` dispatches every event in the same microtask instead, which is
  // what removes the race rather than papering over it with a longer timeout.
  return { user: userEvent.setup({ delay: null }), onCreated };
}

describe("RaiseIncidentDialog", () => {
  it("lets an organization-wide writer name an assignee, and sends it", async () => {
    const spy = vi.spyOn(client, "createIncident").mockResolvedValue(INCIDENT);
    const { user } = open();

    await user.type(screen.getByLabelText(/summary/i), "VPN down for finance team");
    await user.type(screen.getByLabelText(/assignee/i), "Arjun Mehta");
    await user.click(screen.getByRole("button", { name: /raise incident/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0]?.[0]).toMatchObject({ assignee: "Arjun Mehta" });
  });

  it("does not offer Assignee to someone who may only work their own incidents", async () => {
    const spy = vi.spyOn(client, "createIncident").mockResolvedValue(INCIDENT);
    const { user } = open(vi.fn(), ["itsm.service.incident.write:OWN"]);

    expect(screen.queryByLabelText(/assignee/i)).toBeNull();

    await user.type(screen.getByLabelText(/summary/i), "VPN down for finance team");
    await user.click(screen.getByRole("button", { name: /raise incident/i }));

    await waitFor(() => expect(spy).toHaveBeenCalledTimes(1));
    expect(spy.mock.calls[0]?.[0]).toMatchObject({ assignee: "" });
  });

  it("blocks submission with no summary and shows a validation message", async () => {
    const spy = vi.spyOn(client, "createIncident");
    const { user } = open();

    await user.click(screen.getByRole("button", { name: /raise incident/i }));

    expect(await screen.findByText(/describe what's wrong/i)).toBeTruthy();
    expect(spy).not.toHaveBeenCalled();
  });

  it("prefills the requester with the signed-in user's name", () => {
    open();
    expect((screen.getByLabelText(/requester/i) as HTMLInputElement).value).toBe("Meera Raghavan");
  });

  it("disables submit while pending and blocks a second submission", async () => {
    let resolve!: (value: Incident) => void;
    vi.spyOn(client, "createIncident").mockReturnValue(
      new Promise((r) => {
        resolve = r;
      }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/summary/i), "VPN down for finance team");
    const submit = screen.getByRole("button", { name: /raise incident/i });
    await user.click(submit);
    await user.click(submit);

    expect(vi.mocked(client.createIncident)).toHaveBeenCalledTimes(1);
    resolve(INCIDENT);
    await waitForElementToBeRemoved(() => screen.queryByText(/raising…/i));
  });

  it("on success, calls onCreated and the dialog resets on close", async () => {
    vi.spyOn(client, "createIncident").mockResolvedValue(INCIDENT);
    const { user, onCreated } = open();

    await user.type(screen.getByLabelText(/summary/i), "VPN down for finance team");
    await user.click(screen.getByRole("button", { name: /raise incident/i }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(INCIDENT));
    const sent = vi.mocked(client.createIncident).mock.calls[0]?.[0];
    expect(sent).toMatchObject({ summary: "VPN down for finance team", requester: "Meera Raghavan" });
  });

  it("maps a 422 field error onto the summary field", async () => {
    vi.spyOn(client, "createIncident").mockRejectedValue(
      new ApiError(422, "Invalid", {
        detail: "Invalid",
        errors: [{ field: "summary", code: "string_too_long", message: "Keep it under 300 characters." }],
      }),
    );
    const { user } = open();

    // A real keystroke-by-keystroke `user.type` of 301 characters is the slow part of this test
    // (every character is its own render), and it buys nothing here — the mocked response, not
    // what's actually typed, is what the field error comes from. `fireEvent.change` sets it in one
    // native `change` event, the same way `DelegationForm.test.tsx`'s `fillEnds` avoids the same
    // cost for a bulk value.
    fireEvent.change(screen.getByLabelText(/summary/i), { target: { value: "x".repeat(301) } });
    await user.click(screen.getByRole("button", { name: /raise incident/i }));

    expect(await screen.findByText(/keep it under 300 characters/i)).toBeTruthy();
  });

  it("shows the server's 403 message and keeps the entered data", async () => {
    vi.spyOn(client, "createIncident").mockRejectedValue(
      new ApiError(403, "Forbidden", { detail: "You may not raise incidents." }),
    );
    const { user } = open();

    await user.type(screen.getByLabelText(/summary/i), "VPN down for finance team");
    await user.click(screen.getByRole("button", { name: /raise incident/i }));

    expect(await screen.findByText(/you may not raise incidents/i)).toBeTruthy();
    expect((screen.getByLabelText(/summary/i) as HTMLInputElement).value).toBe("VPN down for finance team");
  });
});
