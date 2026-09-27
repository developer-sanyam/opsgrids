import { describe, expect, it, vi } from "vitest";

import { DEFAULT_SLICE_MS, DIGEST_PATTERN, SALT_PATTERN, digestAll, hashingAvailable, newSalt } from "./baselineHash";
import type { CellHashInput } from "./baselineHash";

const SALT = "AAAAAAAAAAAAAAAAAAAAAA";

/** The digest as the format describes it, worked out again here: SHA-256 of the JSON, 16 bytes, base64 without padding. */
async function expected(salt: string, input: CellHashInput): Promise<string> {
  const message = new TextEncoder().encode(JSON.stringify([salt, input.key, input.recordId, input.token]));
  const hash = new Uint8Array(await crypto.subtle.digest("SHA-256", message)).slice(0, 10);
  return btoa(String.fromCharCode(...hash)).replace(/=+$/, "");
}

const input = (overrides: Partial<CellHashInput> = {}): CellHashInput => ({
  key: "name",
  recordId: "p-1",
  token: "Steel bracket",
  ...overrides,
});

describe("the digest of a cell", () => {
  it("is 14 characters of base64, and the same for the same salt, column, record and cell", async () => {
    const [first, again] = await digestAll(SALT, [input(), input()]);
    expect(first).toMatch(DIGEST_PATTERN);
    expect(first).toBe(again);
  });

  it("is the first 80 bits of SHA-256 of the four parts as JSON, checked against a second implementation", async () => {
    const cases = [
      input(),
      input({ token: null }),
      input({ token: "line one\nline two" }),
      input({ token: "Zoë 東京 🚀" }),
      input({ key: 'a"quoted"key', recordId: "id with , and ]" }),
    ];
    const digests = await digestAll(SALT, cases);
    expect(digests).toEqual(await Promise.all(cases.map((one) => expected(SALT, one))));
  });

  it("differs when any one of the four parts differs", async () => {
    const base = input();
    const varied = await digestAll(SALT, [
      base,
      input({ key: "title" }),
      input({ recordId: "p-2" }),
      input({ token: "Steel bracket " }),
      input({ token: "steel bracket" }),
    ]);
    expect(new Set(varied).size).toBe(varied.length);
    expect((await digestAll("BBBBBBBBBBBBBBBBBBBBBB", [base]))[0]).not.toBe(varied[0]);
  });

  it("tells a blank cell from the text 'null', and the parts of the message from one another", async () => {
    const digests = await digestAll(SALT, [
      input({ token: null }),
      input({ token: "null" }),
      // A value that imitates a separator cannot move the boundary between the column and the record.
      input({ key: "a", recordId: "b,c" }),
      input({ key: "a,b", recordId: "c" }),
      input({ key: "a\u0000b", recordId: "c" }),
    ]);
    expect(new Set(digests).size).toBe(digests.length);
  });

  it("keeps the order of its inputs across the groups it works in", async () => {
    const many = Array.from({ length: 700 }, (_, index) => input({ recordId: `p-${index}` }));
    const digests = await digestAll(SALT, many);
    expect(digests).toHaveLength(700);
    for (const at of [0, 127, 128, 255, 256, 699]) expect(digests[at]).toBe(await expected(SALT, many[at] ?? input()));
  });

  it("is nothing for nothing", async () => {
    expect(await digestAll(SALT, [])).toEqual([]);
  });
});

describe("the salt", () => {
  it("is 22 characters of base64 and new every time", () => {
    const salts = Array.from({ length: 20 }, () => newSalt());
    for (const salt of salts) expect(salt).toMatch(SALT_PATTERN);
    expect(new Set(salts).size).toBe(20);
  });
});

describe("yielding to the page", () => {
  it("lets the event loop run between groups when hashing takes longer than a slice", async () => {
    const order: string[] = [];
    setTimeout(() => order.push("timer"), 0);
    // A slice of zero yields after every group, so a timer set before it fires before it ends.
    const digests = await digestAll(SALT, Array.from({ length: 600 }, (_, index) => input({ recordId: `p-${index}` })), 0);
    order.push("hashed");
    expect(digests).toHaveLength(600);
    expect(order).toEqual(["timer", "hashed"]);
  });

  it("does not wait after the last group: a job of one group never yields, however long the slice", async () => {
    const order: string[] = [];
    setTimeout(() => order.push("timer"), 0);
    await digestAll(SALT, [input(), input({ recordId: "p-2" })], 0);
    order.push("hashed");
    expect(order).toEqual(["hashed"]);
    await new Promise((resolve) => setTimeout(resolve, 5));
    expect(DEFAULT_SLICE_MS).toBe(8);
  });

  it("never lets a yield change what it returns", async () => {
    const inputs = Array.from({ length: 900 }, (_, index) => input({ recordId: `p-${index}`, token: String(index) }));
    expect(await digestAll(SALT, inputs, 0)).toEqual(await digestAll(SALT, inputs, 10_000));
  });
});

describe("a context that cannot hash", () => {
  it("is said so, and hashing there fails loudly instead of returning something that would match", async () => {
    vi.stubGlobal("crypto", { getRandomValues: crypto.getRandomValues.bind(crypto) });
    try {
      expect(hashingAvailable()).toBe(false);
      await expect(digestAll(SALT, [input()])).rejects.toThrow("cannot hash");
    } finally {
      vi.unstubAllGlobals();
    }
    expect(hashingAvailable()).toBe(true);
  });
});
