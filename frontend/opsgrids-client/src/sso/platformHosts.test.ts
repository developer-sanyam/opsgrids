import { describe, expect, it } from "vitest";

import { PLATFORM_APP_HOSTS, isAllowedRedirectTarget } from "./platformHosts";

describe("isAllowedRedirectTarget", () => {
  it.each(PLATFORM_APP_HOSTS)("accepts a plain https URL on %s", (host) => {
    expect(isAllowedRedirectTarget(`https://${host}/some/path?x=1`)).toBe(true);
  });

  it("accepts the bare origin with no path", () => {
    expect(isAllowedRedirectTarget("https://people.opsgrids.com")).toBe(true);
  });

  it("rejects a host not on the list", () => {
    expect(isAllowedRedirectTarget("https://evil.example/")).toBe(false);
  });

  it("rejects a storefront subdomain", () => {
    expect(isAllowedRedirectTarget("https://acme.stores.opsgrids.com/")).toBe(false);
  });

  it("rejects a merchant's custom domain", () => {
    expect(isAllowedRedirectTarget("https://shop.merchant.example/")).toBe(false);
  });

  it("rejects a subdomain of an allowed host", () => {
    expect(isAllowedRedirectTarget("https://evil.app.opsgrids.com/")).toBe(false);
  });

  it("rejects a suffix look-alike host", () => {
    expect(isAllowedRedirectTarget("https://app.opsgrids.com.evil.example/")).toBe(false);
  });

  it("rejects plain http", () => {
    expect(isAllowedRedirectTarget("http://app.opsgrids.com/")).toBe(false);
  });

  it("rejects javascript: and other exotic schemes", () => {
    expect(isAllowedRedirectTarget("javascript:alert(1)")).toBe(false);
    expect(isAllowedRedirectTarget("data:text/html,<script>alert(1)</script>")).toBe(false);
    expect(isAllowedRedirectTarget("file:///etc/passwd")).toBe(false);
  });

  it("rejects a protocol-relative URL", () => {
    expect(isAllowedRedirectTarget("//evil.example/app.opsgrids.com")).toBe(false);
  });

  it("rejects a userinfo trick whose real host is not allowed", () => {
    expect(isAllowedRedirectTarget("https://app.opsgrids.com@evil.example/")).toBe(false);
  });

  it("rejects a userinfo trick even when the userinfo names an allowed host and the real host is not", () => {
    expect(isAllowedRedirectTarget("https://app.opsgrids.com:x@evil.example/")).toBe(false);
  });

  it("rejects an explicit port on an otherwise-allowed host", () => {
    expect(isAllowedRedirectTarget("https://app.opsgrids.com:8443/")).toBe(false);
  });

  it("rejects a value that is not a URL at all", () => {
    expect(isAllowedRedirectTarget("not a url")).toBe(false);
    expect(isAllowedRedirectTarget("")).toBe(false);
    expect(isAllowedRedirectTarget("/relative/path")).toBe(false);
  });
});
