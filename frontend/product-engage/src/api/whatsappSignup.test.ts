import { afterEach, describe, expect, it, vi } from "vitest";

import {
  completeSignup,
  disconnectWhatsApp,
  getSignupConfig,
  getSignupStatus,
  getWhatsAppHealth,
  listWhatsAppNumbers,
  sendWhatsAppTestMessage,
  setDefaultNumber,
  syncWhatsAppTemplates,
} from "./whatsappSignup";

/**
 * The wire, for the W1 contract: a path that drifts by one segment fails as a 404 the screen
 * reports as "couldn't load", and a method that drifts turns a 202 into a 405.
 */

function stubFetch(body: unknown, status = 200) {
  const fetchMock = vi.fn().mockResolvedValue(
    new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } }),
  );
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function call(fetchMock: ReturnType<typeof vi.fn>): [string, RequestInit] {
  const first = fetchMock.mock.calls[0] as [string, RequestInit] | undefined;
  if (!first) throw new Error("fetch was never called");
  return first;
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("reads", () => {
  it.each([
    ["signup config", () => getSignupConfig(), "/api/v1/engage/whatsapp/signup/config"],
    ["setup status", () => getSignupStatus("abc-123"), "/api/v1/engage/whatsapp/signup/abc-123"],
    ["numbers", () => listWhatsAppNumbers(), "/api/v1/engage/whatsapp/numbers"],
    ["health", () => getWhatsAppHealth(), "/api/v1/engage/whatsapp/health"],
  ])("reads %s with a GET", async (_name, run, path) => {
    const fetchMock = stubFetch({});
    await run();
    const [url, init] = call(fetchMock);
    expect(url).toBe(path);
    expect(init.method).toBe("GET");
  });
});

describe("writes", () => {
  it("posts the code and the ids Meta gave to signup/complete, and nothing else", async () => {
    const fetchMock = stubFetch({ setup_id: "s1", status: "running" }, 202);
    const out = await completeSignup({ code: "AQB", waba_id: "1", phone_number_id: "2" });
    const [url, init] = call(fetchMock);
    expect(url).toBe("/api/v1/engage/whatsapp/signup/complete");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ code: "AQB", waba_id: "1", phone_number_id: "2" });
    expect(out).toEqual({ setup_id: "s1", status: "running" });
  });

  it("posts the default-number, test-message, disconnect and sync calls to their contract paths", async () => {
    const cases: [() => Promise<unknown>, string, unknown][] = [
      [() => setDefaultNumber("n 1"), "/api/v1/engage/whatsapp/numbers/n%201/default", undefined],
      [
        () => sendWhatsAppTestMessage({ to: "+919876543210", template_name: "hello_world", language: "en" }),
        "/api/v1/engage/whatsapp/test-message",
        { to: "+919876543210", template_name: "hello_world", language: "en" },
      ],
      [() => disconnectWhatsApp(), "/api/v1/engage/whatsapp/disconnect", undefined],
      [() => syncWhatsAppTemplates(), "/api/v1/engage/whatsapp/templates/sync", undefined],
    ];
    for (const [run, path, body] of cases) {
      const fetchMock = stubFetch({ ok: true });
      await run();
      const [url, init] = call(fetchMock);
      expect(url).toBe(path);
      expect(init.method).toBe("POST");
      if (body === undefined) expect(init.body).toBeUndefined();
      else expect(JSON.parse(init.body as string)).toEqual(body);
    }
  });
});
