/**
 * The operator session, through the context: sign-in with a mandatory second factor, enrolment for
 * an account with none, and the rules that matter more than any single screen.
 *
 * - A password alone never earns a session.
 * - **A pre-auth token is one factor away from a session and never goes into storage.**
 * - A wrong code keeps the pending sign-in; a lockout ends it; a token for another audience is
 *   refused before it is kept.
 * - After enrolment the session is minted but not *live* until the recovery codes — shown once —
 *   have been saved.
 *
 * The contract pinned here is `services/auth/app/api/routes.py`, checked field for field in
 * `contract.test.ts`.
 */

import type { ReactNode } from "react";
import { describe, expect, it, vi } from "vitest";
import { act, renderHook, waitFor } from "@testing-library/react";

import { ApiError, getAccessToken, setAccessToken } from "@opsgrids/client";

import { json, operatorToken, problem, stubFetch, tenantToken } from "@/testing";
import { OPERATOR_SESSION_KEY } from "./keys";
import { OperatorAuthProvider } from "./OperatorAuthProvider";
import { useOperatorAuth } from "./context";
import type { OperatorMe } from "./types";

const PRE_AUTH = "pre-auth-token-value-do-not-store";
const ENROL_PRE_AUTH = "enrol-pre-auth-token-value-do-not-store";

const ME: OperatorMe = {
  staff_id: "8f21a0c4-5b1e-4d3a-9c77-2e6a4b0f1d10",
  email: "owner@opsgrids.local",
  display_name: "OpsGrids Owner (dev)",
  operator_roles: ["owner", "billing_ops", "support"],
  amr: ["pwd", "otp"],
};

function complete(token: string) {
  return { status: "complete", access_token: token, token_type: "Bearer", expires_in: 900, operator_roles: ME.operator_roles };
}

function wrapper({ children }: { children: ReactNode }) {
  return <OperatorAuthProvider>{children}</OperatorAuthProvider>;
}

function renderAuth() {
  return renderHook(() => useOperatorAuth(), { wrapper });
}

/** Everything a page could read back from this tab's storage, as one string. */
function storageDump(): string {
  return JSON.stringify({ ...sessionStorage, ...localStorage });
}

describe("boot", () => {
  it("lands signed out, without a request, when there is no stored token", async () => {
    const { calls } = stubFetch({});
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    expect(calls).toHaveLength(0);
  });

  it("verifies a stored operator token, sending it, and signs in as who it names", async () => {
    const token = operatorToken();
    setAccessToken(token);
    const { calls } = stubFetch({ "GET /auth/operator/me": () => json(ME) });

    const { result } = renderAuth();

    await waitFor(() => expect(result.current.status).toBe("authenticated"));
    expect(result.current.operator).toEqual(ME);
    expect(calls).toHaveLength(1);
    expect(calls[0]?.headers.Authorization).toBe(`Bearer ${token}`);
  });

  it("never sends a tenant token left in the operator's slot: it is dropped and the console lands signed out", async () => {
    setAccessToken(tenantToken());
    const { calls } = stubFetch({});

    const { result } = renderAuth();

    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    expect(calls).toHaveLength(0);
    expect(getAccessToken()).toBeNull();
    expect(sessionStorage.getItem(OPERATOR_SESSION_KEY)).toBeNull();
  });

  it("drops a stored token the gateway no longer accepts", async () => {
    setAccessToken(operatorToken());
    stubFetch({ "GET /auth/operator/me": () => problem(401, "That session is no longer valid.", { type: "unauthorized" }) });

    const { result } = renderAuth();

    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    expect(getAccessToken()).toBeNull();
  });

  it("renders straight in with a fixture operator, with no gateway at all", async () => {
    const { calls } = stubFetch({});
    const { result } = renderHook(() => useOperatorAuth(), {
      wrapper: ({ children }: { children: ReactNode }) => (
        <OperatorAuthProvider fixtureOperator={ME}>{children}</OperatorAuthProvider>
      ),
    });
    await waitFor(() => expect(result.current.status).toBe("authenticated"));
    expect(result.current.operator).toEqual(ME);
    expect(calls).toHaveLength(0);
  });
});

describe("password step", () => {
  it("goes to the second factor for an account that has one, and holds no session", async () => {
    const { calls } = stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
    });
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));

    let step: string | undefined;
    await act(async () => {
      step = await result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });

    expect(step).toBe("mfa");
    expect(result.current.status).toBe("unauthenticated");
    expect(getAccessToken()).toBeNull();
    expect(calls[0]?.body).toEqual({ email: "owner@opsgrids.local", password: "a-long-enough-password" }); // pragma: allowlist secret
    expect(calls[0]?.headers.Authorization).toBeUndefined();
  });

  it("goes to enrolment for an account that has no confirmed factor", async () => {
    stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_enrollment_required", pre_auth_token: ENROL_PRE_AUTH }),
    });
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));

    let step: string | undefined;
    await act(async () => {
      step = await result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });

    expect(step).toBe("enrol");
    expect(result.current.status).toBe("unauthenticated");
  });

  it("never puts the pre-auth token in storage", async () => {
    stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
    });
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));

    await act(async () => {
      await result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });

    expect(storageDump()).not.toContain(PRE_AUTH);
    expect(sessionStorage.length + localStorage.length).toBe(0);
  });

  it("refuses an answer that completes the sign-in from a password alone", async () => {
    stubFetch({
      "POST /auth/operator/login": () => json(complete(operatorToken())),
    });
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));

    await act(async () => {
      await expect(result.current.login("owner@opsgrids.local", "a-long-enough-password")).rejects.toThrow(
        /did not ask for a second factor/,
      );
    });
    expect(result.current.status).toBe("unauthenticated");
    expect(getAccessToken()).toBeNull();
  });

  it("carries a wrong password out as the server's own 401, and does not sign the console out over it", async () => {
    stubFetch({
      "POST /auth/operator/login": () => problem(401, "Those credentials are not valid", { type: "unauthorized" }),
    });
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));

    await act(async () => {
      await expect(result.current.login("owner@opsgrids.local", "wrong-password")).rejects.toMatchObject({
        status: 401,
      });
    });
  });
});

describe("second factor", () => {
  async function pendingMfa() {
    const stub = stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "POST /auth/operator/mfa/verify": () => json(complete(operatorToken())),
      "GET /auth/operator/me": () => json(ME),
    });
    const hook = renderAuth();
    await waitFor(() => expect(hook.result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await hook.result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });
    return { ...stub, ...hook };
  }

  it("completes the sign-in with a code: token kept for a reload, identity loaded, pre-auth token sent in the body", async () => {
    const { calls, result } = await pendingMfa();

    await act(async () => {
      await result.current.verifyMfa("123456");
    });

    expect(result.current.status).toBe("authenticated");
    expect(result.current.operator).toEqual(ME);
    expect(getAccessToken()).not.toBeNull();
    expect(sessionStorage.getItem(OPERATOR_SESSION_KEY)).toBe(getAccessToken());
    const verify = calls.find((c) => c.path === "/auth/operator/mfa/verify");
    expect(verify?.body).toEqual({ pre_auth_token: PRE_AUTH, code: "123456" });
    expect(verify?.headers.Authorization).toBeUndefined();
    expect(storageDump()).not.toContain(PRE_AUTH);
  });

  it("keeps the pending sign-in through a wrong code, so the next attempt goes through the same call", async () => {
    let attempts = 0;
    const { result } = renderAuth();
    stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "POST /auth/operator/mfa/verify": () => {
        attempts += 1;
        return attempts === 1 ? problem(401, "That code is not valid", { type: "unauthorized" }) : json(complete(operatorToken()));
      },
      "GET /auth/operator/me": () => json(ME),
    });
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });

    await act(async () => {
      await expect(result.current.verifyMfa("000000")).rejects.toMatchObject({ status: 401, message: "That code is not valid" });
    });
    expect(result.current.status).toBe("unauthenticated");

    await act(async () => {
      await result.current.verifyMfa("123456");
    });
    expect(result.current.status).toBe("authenticated");
    expect(attempts).toBe(2);
  });

  it("ends the pending sign-in on a lockout: the session behind it was revoked, so there is nothing to retry", async () => {
    const { result } = renderAuth();
    const { calls } = stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "POST /auth/operator/mfa/verify": () =>
        problem(403, "This operator account is temporarily locked after several failed attempts. Try again shortly.", { type: "forbidden" }),
    });
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });

    await act(async () => {
      await expect(result.current.verifyMfa("123456")).rejects.toMatchObject({ status: 403 });
    });
    // A second try does not even reach the network: there is no sign-in left to present a code to.
    await act(async () => {
      await expect(result.current.verifyMfa("123456")).rejects.toThrow(/No sign-in is waiting/);
    });
    expect(calls.filter((c) => c.path === "/auth/operator/mfa/verify")).toHaveLength(1);
  });

  it("keeps the pending sign-in through a re-seal-pending 503, which is not the operator's code and is not counted", async () => {
    let attempts = 0;
    const { result } = renderAuth();
    stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "POST /auth/operator/mfa/verify": () => {
        attempts += 1;
        return attempts === 1
          ? problem(503, "Multi-factor re-seal pending — use a recovery code to sign in.", {
              type: "mfa-reseal-pending",
              title: "Multi-factor re-seal pending",
            })
          : json(complete(operatorToken()));
      },
      "GET /auth/operator/me": () => json(ME),
    });
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });

    await act(async () => {
      await expect(result.current.verifyMfa("123456")).rejects.toMatchObject({ status: 503 });
    });
    // The recovery code goes through the same call, on the same pending sign-in.
    await act(async () => {
      await result.current.verifyMfa("abcd-efgh-ijkl");
    });
    expect(result.current.status).toBe("authenticated");
  });

  it("refuses a token for another audience, and keeps nothing of it", async () => {
    const { result } = renderAuth();
    const { calls } = stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "POST /auth/operator/mfa/verify": () => json(complete(tenantToken())),
    });
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });

    await act(async () => {
      await expect(result.current.verifyMfa("123456")).rejects.toThrow(/another audience/);
    });

    expect(result.current.status).toBe("unauthenticated");
    expect(getAccessToken()).toBeNull();
    expect(calls.some((c) => c.path === "/auth/operator/me")).toBe(false);
  });

  it("will not present a code before a password has been accepted", async () => {
    stubFetch({});
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await expect(result.current.verifyMfa("123456")).rejects.toThrow(/No sign-in is waiting/);
    });
  });

  it("forgets a half-finished sign-in when asked to, so the way back to the password step is clean", async () => {
    const { result } = renderAuth();
    stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
    });
    await waitFor(() => expect(result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });

    act(() => result.current.cancelSignIn());

    await act(async () => {
      await expect(result.current.verifyMfa("123456")).rejects.toThrow(/No sign-in is waiting/);
    });
  });
});

describe("enrolment", () => {
  const RECOVERY = ["aaaa-bbbb-cccc", "dddd-eeee-ffff"];

  async function pendingEnrolment(overrides: Record<string, () => Response> = {}) {
    const stub = stubFetch({
      "POST /auth/operator/login": () => json({ status: "mfa_enrollment_required", pre_auth_token: ENROL_PRE_AUTH }),
      "POST /auth/operator/mfa/enrol": () =>
        json({ factor_id: "factor-1", secret: "JBSWY3DPEHPK3PXP", otpauth_uri: "otpauth://totp/OpsGrids:owner?secret=JBSWY3DPEHPK3PXP" }),
      "POST /auth/operator/mfa/enrol/confirm": () => json({ ...complete(operatorToken()), recovery_codes: RECOVERY }),
      "GET /auth/operator/me": () => json(ME),
      ...overrides,
    });
    const hook = renderAuth();
    await waitFor(() => expect(hook.result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await hook.result.current.login("owner@opsgrids.local", "a-long-enough-password");
    });
    return { ...stub, ...hook };
  }

  it("starts the first factor with the out-of-band code, and hands back what an authenticator needs", async () => {
    const { calls, result } = await pendingEnrolment();

    let setup: { secret: string; otpauthUri: string } | undefined;
    await act(async () => {
      setup = await result.current.beginEnrolment("abcd-efgh-ijkl");
    });

    expect(setup).toEqual({
      secret: "JBSWY3DPEHPK3PXP", // pragma: allowlist secret
      otpauthUri: "otpauth://totp/OpsGrids:owner?secret=JBSWY3DPEHPK3PXP",
    });
    const begin = calls.find((c) => c.path === "/auth/operator/mfa/enrol");
    expect(begin?.body).toEqual({ pre_auth_token: ENROL_PRE_AUTH, enrollment_code: "abcd-efgh-ijkl" });
    expect(begin?.headers.Authorization).toBeUndefined();
  });

  it("returns the recovery codes but does not make the session live until they have been saved", async () => {
    const { calls, result } = await pendingEnrolment();
    await act(async () => {
      await result.current.beginEnrolment("abcd-efgh-ijkl");
    });

    let codes: string[] = [];
    await act(async () => {
      codes = await result.current.confirmEnrolment("123456");
    });

    expect(codes).toEqual(RECOVERY);
    // The token exists on the server, but this console has neither kept it nor loaded who it is:
    // the redirect a live session triggers would take the codes off the screen before they were read.
    expect(result.current.status).toBe("unauthenticated");
    expect(getAccessToken()).toBeNull();
    expect(calls.some((c) => c.path === "/auth/operator/me")).toBe(false);
    const confirm = calls.find((c) => c.path === "/auth/operator/mfa/enrol/confirm");
    expect(confirm?.body).toEqual({ pre_auth_token: ENROL_PRE_AUTH, factor_id: "factor-1", code: "123456" });

    await act(async () => {
      await result.current.finishEnrolment();
    });
    expect(result.current.status).toBe("authenticated");
    expect(getAccessToken()).not.toBeNull();
    expect(storageDump()).not.toContain(ENROL_PRE_AUTH);
  });

  it("lets the operator try again if loading who they are fails after the codes were saved", async () => {
    let meCalls = 0;
    const { result } = await pendingEnrolment({
      "GET /auth/operator/me": () => {
        meCalls += 1;
        return meCalls === 1 ? problem(503, "auth-service is restarting", { type: "upstream-unavailable" }) : json(ME);
      },
    });
    await act(async () => {
      await result.current.beginEnrolment("abcd-efgh-ijkl");
      await result.current.confirmEnrolment("123456");
    });

    await act(async () => {
      await expect(result.current.finishEnrolment()).rejects.toBeInstanceOf(ApiError);
    });
    expect(result.current.status).toBe("unauthenticated");
    expect(getAccessToken()).toBeNull();

    await act(async () => {
      await result.current.finishEnrolment();
    });
    expect(result.current.status).toBe("authenticated");
  });

  it("keeps the pending enrolment through a spent enrolment code, so a fresh one can be tried", async () => {
    let attempts = 0;
    const { result } = await pendingEnrolment({
      "POST /auth/operator/mfa/enrol": () => {
        attempts += 1;
        return attempts === 1
          ? problem(401, "That enrolment code is not valid or has expired.", { type: "unauthorized" })
          : json({ factor_id: "factor-1", secret: "S", otpauth_uri: "otpauth://x" });
      },
    });

    await act(async () => {
      await expect(result.current.beginEnrolment("wrong-code-1")).rejects.toMatchObject({ status: 401 });
    });
    await act(async () => {
      await result.current.beginEnrolment("fresh-code-2");
    });
    expect(attempts).toBe(2);
  });

  it("ends the pending sign-in on a lockout during enrolment", async () => {
    const { result } = await pendingEnrolment({
      "POST /auth/operator/mfa/enrol": () =>
        problem(403, "This operator account is temporarily locked after several failed attempts. Try again shortly.", { type: "forbidden" }),
    });

    await act(async () => {
      await expect(result.current.beginEnrolment("abcd-efgh-ijkl")).rejects.toMatchObject({ status: 403 });
    });
    await act(async () => {
      await expect(result.current.beginEnrolment("abcd-efgh-ijkl")).rejects.toThrow(/No sign-in is waiting/);
    });
  });

  it("will not confirm a factor that was never started, nor start one for an account that has an authenticator", async () => {
    const { result } = await pendingEnrolment();
    await act(async () => {
      await expect(result.current.confirmEnrolment("123456")).rejects.toThrow(/No enrolment is waiting/);
    });

    // An account with a factor is on the `mfa` step, whose pre-auth token the server would refuse here.
    const mfa = renderAuth();
    stubFetch({ "POST /auth/operator/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }) });
    await waitFor(() => expect(mfa.result.current.status).toBe("unauthenticated"));
    await act(async () => {
      await mfa.result.current.login("owner@opsgrids.local", "a-long-enough-password");
      await expect(mfa.result.current.beginEnrolment("abcd-efgh-ijkl")).rejects.toThrow(/No sign-in is waiting/);
    });
  });
});

describe("signing out", () => {
  it("ends the session server-side by its cookie, and locally either way", async () => {
    setAccessToken(operatorToken());
    const { calls } = stubFetch({
      "GET /auth/operator/me": () => json(ME),
      "POST /auth/operator/logout": () => new Response(null, { status: 204 }),
    });
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("authenticated"));

    await act(async () => {
      await result.current.logout();
    });

    expect(result.current.status).toBe("unauthenticated");
    expect(result.current.operator).toBeNull();
    expect(getAccessToken()).toBeNull();
    const logout = calls.find((c) => c.path === "/auth/operator/logout");
    expect(logout?.headers.Authorization).toBeUndefined();
  });

  it("drops the local session even when the server call fails", async () => {
    setAccessToken(operatorToken());
    stubFetch({
      "GET /auth/operator/me": () => json(ME),
      "POST /auth/operator/logout": () => problem(500, "boom", { type: "internal" }),
    });
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("authenticated"));

    await act(async () => {
      await result.current.logout();
    });

    expect(result.current.status).toBe("unauthenticated");
    expect(getAccessToken()).toBeNull();
  });

  it("signs the console out when any gateway call answers 401", async () => {
    setAccessToken(operatorToken());
    stubFetch({ "GET /auth/operator/me": () => json(ME) });
    const { result } = renderAuth();
    await waitFor(() => expect(result.current.status).toBe("authenticated"));

    // The client's handler is what a screen's own 401 fires; the provider registered it.
    const { notifyUnauthorized } = await import("@opsgrids/client");
    act(() => notifyUnauthorized());

    expect(result.current.status).toBe("unauthenticated");
    expect(result.current.operator).toBeNull();
  });
});

describe("what the roles admit", () => {
  it("answers `can` from the operator's roles, and denies everything before there is one", async () => {
    setAccessToken(operatorToken(["support"]));
    stubFetch({
      "GET /auth/operator/me": () => json({ ...ME, operator_roles: ["support"] }),
    });
    const { result } = renderAuth();
    expect(result.current.can("organizations")).toBe(false);
    await waitFor(() => expect(result.current.status).toBe("authenticated"));
    expect(result.current.can("organizations")).toBe(true);
    expect(result.current.can("audit")).toBe(true);
    expect(result.current.can("subscriptions")).toBe(false);
    expect(result.current.can("overrides")).toBe(false);
  });
});

describe("outside the provider", () => {
  it("is a wiring bug, not a runtime path", () => {
    const error = vi.spyOn(console, "error").mockImplementation(() => undefined);
    expect(() => renderHook(() => useOperatorAuth())).toThrow(/OperatorAuthProvider/);
    error.mockRestore();
  });
});
