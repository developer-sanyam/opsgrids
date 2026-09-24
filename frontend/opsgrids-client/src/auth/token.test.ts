/**
 * Reading a token's own claims. It is a hint for what to render and never an authorization
 * decision — the gateway verifies the signature — so what is pinned here is that the claims the
 * operator console keys its audience check on survive the decode, and that nothing readable is
 * ever mistaken for a token.
 */

import { describe, expect, it } from "vitest";

import { setAccessToken } from "../session";
import { decodeAccessToken } from "./token";

/** A JWT whose payload is readable and whose signature is meaningless — which is the point. */
function tokenWith(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    btoa(JSON.stringify(value)).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
  return `${encode({ alg: "EdDSA" })}.${encode(claims)}.not-a-signature`;
}

describe("decodeAccessToken", () => {
  it("reads the operator audience, the staff flag and the authentication methods", () => {
    const claims = decodeAccessToken(
      tokenWith({ aud: "operator", stf: true, amr: ["pwd", "otp"], roles: ["owner"] }),
    );
    expect(claims?.aud).toBe("operator");
    expect(claims?.stf).toBe(true);
    expect(claims?.amr).toEqual(["pwd", "otp"]);
    expect(claims?.roles).toEqual(["owner"]);
  });

  it("leaves the staff flag absent on a product token", () => {
    const claims = decodeAccessToken(tokenWith({ aud: "hrms", ent: ["hrms"] }));
    expect(claims?.aud).toBe("hrms");
    expect(claims?.stf).toBeUndefined();
  });

  it("reads the stored token when none is passed", () => {
    setAccessToken(tokenWith({ aud: "operator", stf: true }));
    expect(decodeAccessToken()?.stf).toBe(true);
    setAccessToken(null);
  });

  it("is null for no token, a token with no payload, and text that is not base64 JSON", () => {
    expect(decodeAccessToken(null)).toBeNull();
    expect(decodeAccessToken("no-dots-here")).toBeNull();
    expect(decodeAccessToken("a.%%%.c")).toBeNull();
  });

  it("is null when the payload is JSON but not an object", () => {
    const encode = (value: unknown) => btoa(JSON.stringify(value)).replace(/=+$/, "");
    expect(decodeAccessToken(`${encode({})}.${encode("just a string")}.sig`)).toBeNull();
  });
});
