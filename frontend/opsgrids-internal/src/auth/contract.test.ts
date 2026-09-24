/**
 * The operator sign-in, held to the contract the backend publishes.
 *
 * `services/auth` merged the operator flow (Q1) after this console's auth types were written, and
 * its own docstrings say they were "not yet reconciled field-for-field". They are now, and this is
 * what keeps them that way: each wire type here is compared with the committed OpenAPI document,
 * and each call the console makes is checked against the route, method, request body and status the
 * document describes. A field renamed on either side fails this file, not a sign-in.
 */

import { beforeEach, describe, expect, it } from "vitest";

import { setAccessToken } from "@opsgrids/client";

import { hasOperation, openApi, operationOf, refName, schemaOf } from "@/contracts";
import type { OpenApiDocument, SchemaObject } from "@/contracts";
import { json, operatorToken, stubFetch } from "@/testing";
import * as authApi from "./api";
import type {
  MfaEnrolStart,
  OperatorMe,
  OperatorMfaEnrollmentRequired,
  OperatorMfaEnrolled,
  OperatorMfaRequired,
  OperatorTokenOut,
} from "./types";

const auth: OpenApiDocument = openApi("auth");

function propertyNames(schema: SchemaObject): string[] {
  return Object.keys(schema.properties ?? {}).sort();
}

/** A value typed as the wire type: the compiler refuses a missing or an extra key. */
function keysOfSample(sample: object): string[] {
  return Object.keys(sample).sort();
}

const TOKEN = operatorToken();

describe("the operator wire types", () => {
  it("OperatorMfaRequired", () => {
    const sample: OperatorMfaRequired = { status: "mfa_required", pre_auth_token: "p" };
    const schema = schemaOf(auth, "OperatorMfaRequired");
    expect(keysOfSample(sample)).toEqual(propertyNames(schema));
    expect(schema.properties?.status?.const).toBe(sample.status);
    expect(schema.required).toEqual(["pre_auth_token"]);
  });

  it("OperatorMfaEnrollmentRequired", () => {
    const sample: OperatorMfaEnrollmentRequired = { status: "mfa_enrollment_required", pre_auth_token: "p" };
    const schema = schemaOf(auth, "OperatorMfaEnrollmentRequired");
    expect(keysOfSample(sample)).toEqual(propertyNames(schema));
    expect(schema.properties?.status?.const).toBe(sample.status);
  });

  it("MfaEnrolStart", () => {
    const sample: MfaEnrolStart = { factor_id: "f", secret: "s", otpauth_uri: "otpauth://totp/x" };
    const schema = schemaOf(auth, "MfaEnrolStart");
    expect(keysOfSample(sample)).toEqual(propertyNames(schema));
    expect([...(schema.required ?? [])].sort()).toEqual(keysOfSample(sample));
  });

  it("OperatorTokenOut", () => {
    const sample: OperatorTokenOut = {
      status: "complete",
      access_token: "t",
      token_type: "Bearer",
      expires_in: 900,
      operator_roles: ["owner"],
    };
    const schema = schemaOf(auth, "OperatorTokenOut");
    expect(keysOfSample(sample)).toEqual(propertyNames(schema));
    expect(schema.properties?.status?.const).toBe("complete");
    expect(schema.properties?.token_type?.const).toBe("Bearer");
  });

  it("OperatorMfaEnrolled — the token and the recovery codes, together and once", () => {
    const sample: OperatorMfaEnrolled = {
      status: "complete",
      access_token: "t",
      token_type: "Bearer",
      expires_in: 900,
      operator_roles: ["owner"],
      recovery_codes: ["aaaa-bbbb-cccc"],
    };
    expect(keysOfSample(sample)).toEqual(propertyNames(schemaOf(auth, "OperatorMfaEnrolled")));
  });

  it("OperatorMeOut", () => {
    const sample: OperatorMe = {
      staff_id: "s",
      email: "owner@opsgrids.local",
      display_name: "Owner",
      operator_roles: ["owner"],
      amr: ["pwd", "otp"],
    };
    const schema = schemaOf(auth, "OperatorMeOut");
    expect(keysOfSample(sample)).toEqual(propertyNames(schema));
    expect([...(schema.required ?? [])].sort()).toEqual(keysOfSample(sample));
  });

  it("a login answers with one of exactly two steps, and never a completed sign-in", () => {
    const operation = operationOf(auth, "post", "/api/v1/auth/operator/login");
    const success = operation.responses["200"]?.content?.["application/json"]?.schema;
    const members = (success?.anyOf ?? success?.oneOf ?? []).map(refName).sort();
    expect(members).toEqual(["OperatorMfaEnrollmentRequired", "OperatorMfaRequired"]);
  });
});

/** Every property the schema names is one the body carries, and the body carries nothing else. */
function expectBodyMatches(body: Record<string, unknown> | null, schemaName: string) {
  const schema = schemaOf(auth, schemaName);
  expect(body).not.toBeNull();
  expect(Object.keys(body ?? {}).sort()).toEqual(propertyNames(schema));
  for (const required of schema.required ?? []) expect(body).toHaveProperty(required);
}

describe("the calls the console makes", () => {
  beforeEach(() => {
    setAccessToken(TOKEN);
  });

  it("POST /auth/operator/login", async () => {
    const { calls } = stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: "p" }),
    });
    await authApi.login("owner@opsgrids.local", "a-long-enough-password");
    expect(hasOperation(auth, "post", "/api/v1/auth/operator/login")).toBe(true);
    expectBodyMatches(calls[0]?.body ?? null, "OperatorLoginIn");
  });

  it("POST /auth/operator/mfa/enrol", async () => {
    const { calls } = stubFetch({
      "POST /auth/operator/mfa/enrol": () => json({ factor_id: "f", secret: "s", otpauth_uri: "otpauth://x" }),
    });
    await authApi.beginEnrolment("pre", "abcd-efgh-ijkl");
    expectBodyMatches(calls[0]?.body ?? null, "OperatorMfaEnrolIn");
    expect(operationOf(auth, "post", "/api/v1/auth/operator/mfa/enrol").responses).toHaveProperty("201");
  });

  it("POST /auth/operator/mfa/enrol/confirm", async () => {
    const { calls } = stubFetch({
      "POST /auth/operator/mfa/enrol/confirm": () =>
        json({
          status: "complete",
          access_token: TOKEN,
          token_type: "Bearer",
          expires_in: 900,
          operator_roles: ["owner"],
          recovery_codes: [],
        }),
    });
    await authApi.confirmEnrolment("pre", "factor-1", "123456");
    expectBodyMatches(calls[0]?.body ?? null, "OperatorMfaEnrolConfirmIn");
  });

  it("POST /auth/operator/mfa/verify", async () => {
    const { calls } = stubFetch({
      "POST /auth/operator/mfa/verify": () =>
        json({ status: "complete", access_token: TOKEN, token_type: "Bearer", expires_in: 900, operator_roles: [] }),
    });
    await authApi.verifyMfa("pre", "123456");
    expectBodyMatches(calls[0]?.body ?? null, "OperatorMfaVerifyIn");
  });

  it("GET /auth/operator/me and POST /auth/operator/logout exist as the console calls them", async () => {
    stubFetch({
      "GET /auth/operator/me": () =>
        json({ staff_id: "s", email: "o@x.test", display_name: "O", operator_roles: [], amr: [] }),
      "POST /auth/operator/logout": () => new Response(null, { status: 204 }),
    });
    await authApi.me();
    await authApi.logout();
    expect(hasOperation(auth, "get", "/api/v1/auth/operator/me")).toBe(true);
    expect(operationOf(auth, "post", "/api/v1/auth/operator/logout").responses).toHaveProperty("204");
  });

  it("every operator route the console reaches is one the contract publishes", () => {
    const called = [
      ["post", "/auth/operator/login"],
      ["post", "/auth/operator/mfa/enrol"],
      ["post", "/auth/operator/mfa/enrol/confirm"],
      ["post", "/auth/operator/mfa/verify"],
      ["get", "/auth/operator/me"],
      ["post", "/auth/operator/logout"],
    ] as const;
    for (const [method, path] of called) {
      expect(hasOperation(auth, method, `/api/v1${path}`), `${method} ${path}`).toBe(true);
    }
  });
});
