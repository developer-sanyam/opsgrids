import { afterEach, describe, expect, it, vi } from "vitest";

import {
  cancelSubscription,
  createInvitation,
  createLocation,
  createOrgUnit,
  createRole,
  deleteRole,
  getCreditBalance,
  listMembers,
  moveOrgUnit,
  removeMember,
  revokeInvitation,
  setMemberRoles,
  updateLocation,
  updateOrganization,
  updateOrgUnit,
} from "./index";

function respond(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function fetchSpy() {
  const spy = vi.fn().mockResolvedValue(respond({}));
  vi.stubGlobal("fetch", spy);
  return spy;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("account/org-unit/location writes — method, URL and JSON body, exactly", () => {
  it("createInvitation POSTs to /invitations with the exact payload, no product_codes invented", async () => {
    const spy = fetchSpy();
    await createInvitation({ email: "a@b.example", role_codes: ["ORG_ADMIN"], product_codes: [], expires_in_days: 14 });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/invitations");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      email: "a@b.example",
      role_codes: ["ORG_ADMIN"],
      product_codes: [],
      expires_in_days: 14,
    });
  });

  it("revokeInvitation DELETEs /invitations/{id}", async () => {
    const spy = fetchSpy();
    await revokeInvitation("inv-1");
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/invitations/inv-1");
    expect(init.method).toBe("DELETE");
  });

  it("removeMember DELETEs /members/{id} with reason as a query param, not a body", async () => {
    const spy = fetchSpy();
    await removeMember("mem-1", "role change");
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/members/mem-1?reason=role+change");
    expect(init.method).toBe("DELETE");
    expect(init.body).toBeUndefined();
  });

  it("removeMember omits the query param entirely when no reason is given", async () => {
    const spy = fetchSpy();
    await removeMember("mem-1");
    const [url] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/members/mem-1");
  });

  it("setMemberRoles PUTs the full replacement list", async () => {
    const spy = fetchSpy();
    await setMemberRoles("mem-1", ["ORG_ADMIN", "ORG_OWNER"]);
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/members/mem-1/roles");
    expect(init.method).toBe("PUT");
    expect(JSON.parse(init.body as string)).toEqual({ role_codes: ["ORG_ADMIN", "ORG_OWNER"] });
  });

  it("listMembers builds every filter as a query param and drops the ones left unset", async () => {
    const spy = fetchSpy();
    await listMembers({ q: "asha", linked: false, page: 2, size: 50 });
    const [url] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/members?q=asha&linked=false&page=2&size=50");
  });

  it("createRole POSTs code, name and grants", async () => {
    const spy = fetchSpy();
    await createRole({ code: "AUDITOR", name: "Auditor", grants: [{ permission_code: "core.identity.role.read", scope: "ORG" }] });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/roles");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      code: "AUDITOR",
      name: "Auditor",
      grants: [{ permission_code: "core.identity.role.read", scope: "ORG" }],
    });
  });

  it("deleteRole DELETEs /roles/{id}", async () => {
    const spy = fetchSpy();
    await deleteRole("role-1");
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/roles/role-1");
    expect(init.method).toBe("DELETE");
  });

  it("moveOrgUnit sends parent_id as a query parameter with no body at all", async () => {
    const spy = fetchSpy();
    await moveOrgUnit("unit-1", "unit-parent");
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/org-units/unit-1/move?parent_id=unit-parent");
    expect(init.method).toBe("POST");
    expect(init.body).toBeUndefined();
  });

  it("moveOrgUnit omits parent_id when moving to root", async () => {
    const spy = fetchSpy();
    await moveOrgUnit("unit-1", null);
    const [url] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/org-units/unit-1/move");
  });

  it("createOrgUnit POSTs the fields the schema declares", async () => {
    const spy = fetchSpy();
    await createOrgUnit({ name: "Engineering", code: "ENG", kind: "department" });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/org-units");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Engineering", code: "ENG", kind: "department" });
  });

  it("updateOrgUnit PATCHes only the fields given", async () => {
    const spy = fetchSpy();
    await updateOrgUnit("unit-1", { is_active: false });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/org-units/unit-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ is_active: false });
  });

  it("createLocation POSTs to /locations", async () => {
    const spy = fetchSpy();
    await createLocation({ name: "Bengaluru HQ", code: "BLR", timezone: "Asia/Kolkata" });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/locations");
    expect(init.method).toBe("POST");
  });

  it("updateLocation PATCHes /locations/{id}, never the code", async () => {
    const spy = fetchSpy();
    await updateLocation("loc-1", { name: "Bengaluru — renamed" });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/locations/loc-1");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Bengaluru — renamed" });
  });

  it("updateOrganization PATCHes /organization with only the changed fields", async () => {
    const spy = fetchSpy();
    await updateOrganization({ name: "Greenleaf Organics" });
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/organization");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body as string)).toEqual({ name: "Greenleaf Organics" });
  });

  it("cancelSubscription POSTs a reason only when one was given", async () => {
    const spy = fetchSpy();
    await cancelSubscription("sub-1");
    const [url, init] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/subscriptions/sub-1/cancel");
    expect(JSON.parse(init.body as string)).toEqual({});
  });

  it("getCreditBalance requires currency as a query parameter", async () => {
    const spy = fetchSpy();
    await getCreditBalance("INR");
    const [url] = spy.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/credit-balance?currency=INR");
  });
});
