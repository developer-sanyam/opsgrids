import { afterEach, describe, expect, it, vi } from "vitest";

import {
  addDomain,
  addSender,
  connectProvider,
  deleteDomain,
  editDomain,
  getConnection,
  getSetup,
  listDomains,
  listSenders,
  sendCampaignTest,
  sendTestMessage,
  verifyDomain,
} from "./client";

/**
 * The live branch, which is the default: `USE_FIXTURES` needs `VITE_USE_FIXTURES=true` and the
 * test env does not set it, so every call below goes through `fetch`.
 *
 * What is pinned here is the wire: the gateway mounts this service at `/api/v1/engage`, and a
 * path that drifts by one segment fails as a 404 the UI reports as "couldn't load", with nothing
 * naming the cause. The method matters as much — `POST /engage/connection` is a create the
 * service refuses on a second call, and sending `PUT` would 405 every first-time setup.
 */

/** Stub `fetch` once, capture the request, and answer with the given JSON body. */
function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), {
      status,
      headers: { "content-type": "application/json" },
    }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function call(fetchMock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  const first = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!first) throw new Error("fetch was never called");
  return first;
}

const EMPTY_PAGE = { items: [], total: 0, page: 1, size: 200, pages: 0 };

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("the engage paths", () => {
  it("reads the connection from the singleton route", async () => {
    const fetchMock = stubFetch(EMPTY_PAGE);
    await getConnection();
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/connection");
    expect(init.method).toBe("GET");
  });

  it("creates the connection with POST, because a second one is refused", async () => {
    const fetchMock = stubFetch({});
    await connectProvider({
      kind: "smtp",
      username: "mailer@vaayu-logistics.example.com",
      secret: "an-app-password", // pragma: allowlist secret
      host: "smtp.vaayu-logistics.example.com",
      port: 587,
    });
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/connection");
    expect(init.method).toBe("POST");
  });

  it("asks the server to filter domains by status rather than filtering the page here", async () => {
    const fetchMock = stubFetch(EMPTY_PAGE);
    await listDomains({ status: "pending" });
    expect(call(fetchMock)[0]).toBe("/api/v1/engage/domains?size=200&status=pending");
  });

  it("omits the status filter when none is given, but still caps the page size", async () => {
    const fetchMock = stubFetch(EMPTY_PAGE);
    await listDomains();
    expect(call(fetchMock)[0]).toBe("/api/v1/engage/domains?size=200");
  });

  it("adds a domain and verifies one at the routes that own each", async () => {
    const added = stubFetch({});
    await addDomain({ domain: "vaayu-freight.example.com" });
    expect(call(added)[0]).toBe("/api/v1/engage/domains");
    vi.unstubAllGlobals();

    const verified = stubFetch({});
    await verifyDomain("b90d5e47-2c31-4a88-8f70-61d4c9a5e223");
    const [url, init] = call(verified);
    expect(url).toBe("/api/v1/engage/domains/b90d5e47-2c31-4a88-8f70-61d4c9a5e223/verify");
    expect(init.method).toBe("POST");
  });

  it("scopes senders to one domain with the query parameter the route declares", async () => {
    const fetchMock = stubFetch(EMPTY_PAGE);
    await listSenders({ domainId: "3f6a1c20-7d84-4e1b-9c53-0a2e8b74d611" });
    expect(call(fetchMock)[0]).toBe(
      "/api/v1/engage/senders?size=200&domain_id=3f6a1c20-7d84-4e1b-9c53-0a2e8b74d611",
    );
  });

  it("edits a domain with PATCH and deletes it with DELETE, both at the same resource path", async () => {
    const edited = stubFetch({});
    await editDomain("b90d5e47-2c31-4a88-8f70-61d4c9a5e223", { dkim_selector: "rotated" });
    const [editUrl, editInit] = call(edited);
    expect(editUrl).toBe("/api/v1/engage/domains/b90d5e47-2c31-4a88-8f70-61d4c9a5e223");
    expect(editInit.method).toBe("PATCH");
    vi.unstubAllGlobals();

    const deleteMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", deleteMock);
    await deleteDomain("b90d5e47-2c31-4a88-8f70-61d4c9a5e223");
    const [deleteUrl, deleteInit] = call(deleteMock);
    expect(deleteUrl).toBe("/api/v1/engage/domains/b90d5e47-2c31-4a88-8f70-61d4c9a5e223");
    expect(deleteInit.method).toBe("DELETE");
  });

  it("reads the get-started checklist from its own route", async () => {
    const fetchMock = stubFetch({ steps: [] });
    await getSetup();
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/setup");
    expect(init.method).toBe("GET");
  });

  it("sends a campaign test to the campaign's own test route", async () => {
    const fetchMock = stubFetch({ sent: true, to: "ops@vaayu-logistics.example.com" });
    await sendCampaignTest("c1a2b3c4-1111-2222-3333-444455556666", {
      to_address: "ops@vaayu-logistics.example.com",
    });
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/campaigns/c1a2b3c4-1111-2222-3333-444455556666/test");
    expect(init.method).toBe("POST");
  });

  it("sends the test message to the sender's own test route", async () => {
    const fetchMock = stubFetch({});
    await addSender({
      domain_id: "3f6a1c20-7d84-4e1b-9c53-0a2e8b74d611",
      name: "Support",
      address: "support@vaayu-logistics.example.com",
    });
    expect(call(fetchMock)[0]).toBe("/api/v1/engage/senders");
    vi.unstubAllGlobals();

    const tested = stubFetch({});
    await sendTestMessage("5c8e2a19-4b6f-4d02-a37e-9f15c0b8d744", {
      to_address: "ops@vaayu-logistics.example.com",
    });
    expect(call(tested)[0]).toBe(
      "/api/v1/engage/senders/5c8e2a19-4b6f-4d02-a37e-9f15c0b8d744/test",
    );
  });
});

describe("the credential", () => {
  it("leaves the browser on the connect call and is never asked for back", async () => {
    const fetchMock = stubFetch({});
    await connectProvider({
      kind: "google",
      username: "notifications@vaayu-logistics.example.com",
      secret: "the-app-password", // pragma: allowlist secret
    });
    const [, init] = call(fetchMock);
    expect(init.body).toBe(
      JSON.stringify({
        kind: "google",
        username: "notifications@vaayu-logistics.example.com",
        secret: "the-app-password", // pragma: allowlist secret
      }),
    );

    // The read is a plain GET with no body and no query asking for anything extra. Whatever the
    // service returns, nothing here requests the secret back — the only call that carries one
    // is the one above.
    vi.unstubAllGlobals();
    const read = stubFetch(EMPTY_PAGE);
    await getConnection();
    const [url, readInit] = call(read);
    expect(url).toBe("/api/v1/engage/connection");
    expect(readInit.body).toBeUndefined();
  });
});
