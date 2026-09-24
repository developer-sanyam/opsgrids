/**
 * The boundary between the two token populations (ADR-0003 §2), from the client's side.
 *
 * The gateways enforce it: a tenant token is rejected on admin-gateway at signature-and-audience
 * time, and an operator token means nothing on a product route. What is pinned here is that the
 * *console* cannot be the one to cause the mistake — a tenant token never leaves this app on an
 * operator request, and an operator token never leaves it on a tenant route — because a client
 * that sends the wrong token has already leaked one, even if the server then refuses it.
 *
 * What did NOT go out is asserted as hard as what did: a refused request must leave no trace on
 * the wire.
 */

import { describe, expect, it, vi } from "vitest";

import { ApiError, getAccessToken, setAccessToken, setUnauthorizedHandler } from "@opsgrids/client";

import { json, operatorToken, problem, stubFetch, tenantToken, tokenWith } from "@/testing";
import { isOperatorPath, isOperatorToken, operatorRequest, SESSION_NOT_VALID } from "./operatorRequest";

describe("isOperatorPath", () => {
  it("admits the operator surface: admin routes and operator sign-in", () => {
    for (const path of [
      "/admin/organizations",
      "/admin/organizations?q=acme&status=active",
      "/admin/organizations/3f2a9c10-1111-4222-8333-444455556666",
      "/admin/metrics/overview",
      "/auth/operator/login",
      "/auth/operator/mfa/enrol/confirm",
      "/auth/operator/me",
    ]) {
      expect(isOperatorPath(path), path).toBe(true);
    }
  });

  it("refuses every tenant route, including the /auth ones admin-gateway would forward", () => {
    for (const path of [
      "/auth/login",
      "/auth/mfa/verify",
      "/auth/me",
      "/auth/select-org",
      "/auth/logout",
      "/members/me/grants",
      "/account/signup",
      "/subscriptions",
      "/hrms/employees",
    ]) {
      expect(isOperatorPath(path), path).toBe(false);
    }
  });

  it("refuses a bare prefix, which is not a route", () => {
    expect(isOperatorPath("/admin")).toBe(false);
    expect(isOperatorPath("/auth/operator")).toBe(false);
    expect(isOperatorPath("/authoperator/x")).toBe(false);
  });

  it("refuses a path that walks out of the prefix, plainly or percent-encoded", () => {
    // A URL parser resolves these to /members/me/grants, which is a tenant route.
    for (const path of [
      "/admin/../members/me/grants",
      "/admin/organizations/../../members/me/grants",
      "/admin/%2e%2e/members/me/grants",
      "/admin/%2E%2E/members/me/grants",
      "/admin/./organizations",
      "/auth/operator/../login",
    ]) {
      expect(isOperatorPath(path), path).toBe(false);
    }
  });

  it("refuses separators some parsers treat as slashes, and undecodable escapes", () => {
    expect(isOperatorPath("/admin//organizations")).toBe(false);
    expect(isOperatorPath("/admin\\..\\members")).toBe(false);
    expect(isOperatorPath("/admin/%E0%A4%A")).toBe(false);
  });

  it("does not mind a dot-dot in the query string, which is not a path", () => {
    expect(isOperatorPath("/admin/organizations?q=..")).toBe(true);
  });
});

describe("isOperatorToken", () => {
  it("recognises an operator token by its audience and the staff flag", () => {
    expect(isOperatorToken(operatorToken())).toBe(true);
  });

  it("does not recognise a product token", () => {
    expect(isOperatorToken(tenantToken())).toBe(false);
  });

  it("wants both: the operator audience without the staff flag is not enough, nor the flag alone", () => {
    expect(isOperatorToken(tokenWith({ aud: "operator" }))).toBe(false);
    expect(isOperatorToken(tokenWith({ stf: true }))).toBe(false);
    expect(isOperatorToken(tokenWith({ aud: "hrms", stf: true }))).toBe(false);
  });

  it("does not recognise nothing, or something that is not a token", () => {
    expect(isOperatorToken(null)).toBe(false);
    expect(isOperatorToken("opaque-string")).toBe(false);
  });
});

describe("operatorRequest", () => {
  it("sends the operator token, and only to an operator route", async () => {
    const token = operatorToken();
    setAccessToken(token);
    const { calls } = stubFetch({ "GET /admin/organizations": () => json({ organizations: [] }) });

    await operatorRequest("/admin/organizations");

    expect(calls).toHaveLength(1);
    expect(calls[0]?.headers.Authorization).toBe(`Bearer ${token}`);
  });

  it("never sends a tenant token: it is dropped, the sign-in screen is asked for, and nothing goes out", async () => {
    setAccessToken(tenantToken());
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    const { calls } = stubFetch({});

    const failure = await operatorRequest("/admin/organizations").catch((error: unknown) => error);

    expect(calls).toHaveLength(0);
    expect(failure).toBeInstanceOf(ApiError);
    expect((failure as ApiError).status).toBe(401);
    expect((failure as ApiError).message).toBe(SESSION_NOT_VALID);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
  });

  it("does not send an unreadable value from the session slot either", async () => {
    setAccessToken("not-a-jwt");
    setUnauthorizedHandler(vi.fn());
    const { calls } = stubFetch({});

    await expect(operatorRequest("/admin/organizations")).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(0);
  });

  it("sends nothing when there is no session at all", async () => {
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    const { calls } = stubFetch({});

    await expect(operatorRequest("/admin/metrics/overview")).rejects.toBeInstanceOf(ApiError);
    expect(calls).toHaveLength(0);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("never sends the operator token to a tenant route: it refuses the path before any request", async () => {
    setAccessToken(operatorToken());
    const { calls } = stubFetch({});

    for (const path of ["/members/me/grants", "/auth/me", "/auth/login", "/admin/../members/me/grants"]) {
      await expect(operatorRequest(path), path).rejects.toThrow(/only calls \/admin and \/auth\/operator/);
    }
    expect(calls).toHaveLength(0);
    // The operator's own session is untouched by a refused path: it is a wiring mistake, not a sign-out.
    expect(getAccessToken()).not.toBeNull();
  });

  it("carries no bearer token on a pre-auth call, even with a session in the slot", async () => {
    setAccessToken(operatorToken());
    const { calls } = stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: "p" }),
    });

    await operatorRequest("/auth/operator/login", {
      method: "POST",
      body: { email: "owner@opsgrids.local", password: "x" },
      auth: false,
      signalUnauthorized: false,
    });

    expect(calls[0]?.headers.Authorization).toBeUndefined();
  });

  it("signs out on a 401 from the gateway, and does not retry it", async () => {
    setAccessToken(operatorToken());
    const onUnauthorized = vi.fn();
    setUnauthorizedHandler(onUnauthorized);
    const { calls } = stubFetch({
      "GET /admin/audit": () => problem(401, "That session is no longer valid.", { type: "unauthorized" }),
    });

    await expect(operatorRequest("/admin/audit")).rejects.toMatchObject({ status: 401 });

    expect(calls).toHaveLength(1);
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
    expect(getAccessToken()).toBeNull();
  });

  it("does not retry a 403 or a 500 — a 4xx does not fix itself, and a write is not known to be safe to repeat", async () => {
    setAccessToken(operatorToken());
    const { calls } = stubFetch({
      "GET /admin/usage": () => problem(403, "This action needs one of these operator roles: billing_ops, owner", { type: "forbidden" }),
      "POST /admin/overrides": () => problem(503, "subscription-service is unavailable", { type: "upstream-unavailable" }),
    });

    await expect(operatorRequest("/admin/usage")).rejects.toMatchObject({ status: 403 });
    await expect(operatorRequest("/admin/overrides", { method: "POST", body: {} })).rejects.toMatchObject({
      status: 503,
    });

    expect(calls).toHaveLength(2);
    // A 403 is a role, not a stale session: the operator stays signed in.
    expect(getAccessToken()).not.toBeNull();
  });
});
