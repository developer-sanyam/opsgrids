/** The scan client carries the code in the query, percent-encoded, never in the path. */
import { describe, expect, it, vi } from "vitest";

const apiRequest = vi.hoisted(() => vi.fn().mockResolvedValue({}));
vi.mock("@opsgrids/client", () => ({ apiRequest }));

import { resolveScan } from "./scan";

describe("resolveScan", () => {
  it("sends awkward lot numbers as an encoded query value", async () => {
    await resolveScan("og:lot:A/../B?x#y%41");
    const url = apiRequest.mock.calls[0]![0] as string;
    expect(url.startsWith("/operations/inventory/scan?code=")).toBe(true);
    expect(new URL(url, "http://x").searchParams.get("code")).toBe("og:lot:A/../B?x#y%41");
  });
});
