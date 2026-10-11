import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PublicApiError,
  checkServiceability,
  fetchPublicCatalogue,
  submitEnquiry,
} from "./publicClient";

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("checkServiceability", () => {
  it("POSTs the store and normalised postal code to the exact public path", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      respond(200, {
        postal_code: "400050",
        serviceable: true,
        mode: "PROMISED",
        checked_at: "2026-09-21T00:00:00Z",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await checkServiceability({ store: "greenleaf", postal_code: "400 050" });

    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/storefront/public/serviceability");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ store: "greenleaf", postal_code: "400050" });
    // No Authorization header — this call carries no session.
    expect((init.headers as Record<string, string>).Authorization).toBeUndefined();
  });

  it("throws PublicApiError, never a bare object, on a non-2xx", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(respond(404, { detail: "No published store with that name." })));
    await expect(checkServiceability({ store: "nope", postal_code: "400050" })).rejects.toBeInstanceOf(
      PublicApiError,
    );
  });

  it("throws PublicApiError(0, …) on a dead network, never crashes the caller", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const error = await checkServiceability({ store: "greenleaf", postal_code: "400050" }).catch(
      (e: unknown) => e,
    );
    expect(error).toBeInstanceOf(PublicApiError);
    expect((error as PublicApiError).status).toBe(0);
  });
});

describe("fetchPublicCatalogue", () => {
  it("POSTs store and category in the body, page and size as query params", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      respond(200, { categories: [], products: { items: [], total: 0, page: 1, pages: 1, size: 24 } }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchPublicCatalogue({ store: "greenleaf", category: "fasteners", page: 2, size: 50 });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/storefront/public/catalogue?page=2&size=50");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({ store: "greenleaf", category: "fasteners" });
  });

  it("defaults page to 1 and size to 24, and sends category: null when none is asked for", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(respond(200, { categories: [], products: { items: [], total: 0, page: 1, pages: 1, size: 24 } }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchPublicCatalogue({ store: "greenleaf" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/storefront/public/catalogue?page=1&size=24");
    expect(JSON.parse(init.body as string)).toEqual({ store: "greenleaf", category: null });
  });
});

describe("submitEnquiry", () => {
  const VALID = {
    store: "greenleaf",
    name: "Rajeev Bhatia",
    company: "Sunrise Engineering",
    email: "rajeev@sunrise.example.in",
    phone: "",
    requirement: "200 coils of 2.5 sq mm FRLS",
    quantity: "",
    gstin: "",
    lines: [],
  };

  it("refuses locally when a required field is missing, and never calls the network", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);

    await expect(submitEnquiry({ ...VALID, name: "" })).rejects.toThrow(
      "Some required details are missing. Check the fields above.",
    );
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("sends the WhatsApp opt-in only when the buyer ticked it", async () => {
    const fetchMock = vi
      .fn()
      .mockImplementation(() =>
        Promise.resolve(respond(201, { id: "enq-1", received_at: "2026-09-21T00:00:00Z" })),
      );
    vi.stubGlobal("fetch", fetchMock);

    await submitEnquiry({ ...VALID, phone: "+91 98765 43210", whatsappOptIn: true });
    await submitEnquiry({ ...VALID, whatsappOptIn: false });

    const bodies = fetchMock.mock.calls.map(
      (call) => JSON.parse((call[1] as RequestInit).body as string) as Record<string, unknown>,
    );
    expect(bodies[0]?.whatsapp_marketing_opt_in).toBe(true);
    expect(bodies[1]).not.toHaveProperty("whatsapp_marketing_opt_in");
  });

  it("POSTs the exact public path with phone/gstin nulled when blank, and echoes the line list", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(respond(201, { id: "enq-1", received_at: "2026-09-21T00:00:00Z" }));
    vi.stubGlobal("fetch", fetchMock);

    const receipt = await submitEnquiry({
      ...VALID,
      lines: [{ product_id: "prod-1", name: "M12 hex bolt", quantity: 500 }],
    });

    expect(receipt).toEqual({ id: "enq-1", received_at: "2026-09-21T00:00:00Z" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/storefront/public/enquiry");
    expect(init.method).toBe("POST");
    expect(JSON.parse(init.body as string)).toEqual({
      store: "greenleaf",
      name: "Rajeev Bhatia",
      company: "Sunrise Engineering",
      email: "rajeev@sunrise.example.in",
      phone: null,
      gstin: null,
      requirement: "200 coils of 2.5 sq mm FRLS",
      quantity: "",
      lines: [{ product_id: "prod-1", name: "M12 hex bolt", quantity: 500 }],
    });
  });

  it("surfaces the server's own detail on a 422", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(respond(422, { detail: "requirement: field required" })),
    );
    await expect(submitEnquiry(VALID)).rejects.toMatchObject({
      status: 422,
      message: "requirement: field required",
    });
  });
});
