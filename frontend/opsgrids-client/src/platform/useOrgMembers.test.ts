import { describe, expect, it, vi } from "vitest";

import { ApiError } from "../http";
import { resolveRosterRef } from "./useOrgMembers";

const ID = "0f6b2c1e-9a4d-4e2b-8c3f-5d7a1b2c3d4e";
const members = [{ user_id: ID, name: "Asha Rao" }];

describe("resolveRosterRef", () => {
  it("passes a roster ref through untouched", async () => {
    const create = vi.fn();
    expect(await resolveRosterRef("p01", members, create)).toBe("p01");
    expect(create).not.toHaveBeenCalled();
  });

  it("adds a picked member to the roster under their whole user id", async () => {
    const create = vi.fn().mockResolvedValue(undefined);
    expect(await resolveRosterRef(`m:${ID}`, members, create)).toBe(`u-${ID}`);
    expect(create).toHaveBeenCalledWith(`u-${ID}`, "Asha Rao");
  });

  it("uses the existing roster row on a 409", async () => {
    const create = vi.fn().mockRejectedValue(new ApiError(409, "exists", null));
    expect(await resolveRosterRef(`m:${ID}`, members, create)).toBe(`u-${ID}`);
  });

  it("surfaces a refusal instead of assigning nobody", async () => {
    const create = vi.fn().mockRejectedValue(new ApiError(403, "forbidden", null));
    await expect(resolveRosterRef(`m:${ID}`, members, create)).rejects.toThrow("forbidden");
    await expect(resolveRosterRef("m:unknown", members, create)).rejects.toThrow(/no longer a member/);
  });
});
