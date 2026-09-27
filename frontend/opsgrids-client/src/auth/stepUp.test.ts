/**
 * `authApi.stepUp` against the contract of `POST /auth/mfa/step-up`: what it sends, what it
 * accepts, and how every refusal is typed. The failures are the point — the dialog only ever
 * reads `failure`, so a wrong mapping here is a wrong sentence on the screen — and the property
 * that matters most is that none of them can sign the person out.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAccessToken, setAccessToken, setUnauthorizedHandler } from "../session";
import { stepUp } from "./api";
import { StepUpError, classifyStepUpFailure } from "./stepUpError";
import type { StepUpFailure } from "./stepUpError";

const SESSION = "session-access-token";
const STEP_UP = "step-up-access-token";

const TOKEN_OUT = {
  status: "complete",
  access_token: STEP_UP,
  token_type: "Bearer",
  expires_in: 180,
  organization_id: "org-1",
  membership_id: "mem-1",
};

function problem(status: number, slug: string, detail: string): Response {
  return new Response(
    JSON.stringify({
      type: `https://errors.opsgrids.dev/${slug}`,
      title: detail,
      status,
      detail,
    }),
    { status, headers: { "content-type": "application/problem+json" } },
  );
}

function ok(body: unknown): Response {
  return new Response(JSON.stringify(body), { status: 200, headers: { "content-type": "application/json" } });
}

let unauthorized: ReturnType<typeof vi.fn>;

beforeEach(() => {
  setAccessToken(SESSION);
  unauthorized = vi.fn();
  setUnauthorizedHandler(unauthorized);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  sessionStorage.clear();
});

async function failureOf(response: Response): Promise<StepUpFailure> {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(response));
  const error = await stepUp("123456").catch((caught: unknown) => caught);
  expect(error).toBeInstanceOf(StepUpError);
  return (error as StepUpError).failure;
}

describe("authApi.stepUp", () => {
  it("posts the code with the session bearer and returns the token", async () => {
    const fetchMock = vi.fn().mockResolvedValue(ok(TOKEN_OUT));
    vi.stubGlobal("fetch", fetchMock);

    const token = await stepUp("123456");

    expect(token.access_token).toBe(STEP_UP);
    expect(token.expires_in).toBe(180);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/auth/mfa/step-up");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({ code: "123456" });
    expect((init.headers as Record<string, string>).Authorization).toBe(`Bearer ${SESSION}`);
  });

  it("never touches the session: the step-up token is not stored anywhere", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok(TOKEN_OUT)));

    await stepUp("123456");

    expect(getAccessToken()).toBe(SESSION);
    expect(sessionStorage.getItem("opsgrids.access_token")).toBe(SESSION);
    expect(JSON.stringify({ ...sessionStorage })).not.toContain(STEP_UP);
  });

  it("types a wrong or replayed code, on 401 or on 422, and does not sign the person out", async () => {
    expect(await failureOf(problem(401, "unauthorized", "That code is not valid"))).toBe("wrong-code");
    expect(await failureOf(problem(422, "validation", "That code is not valid"))).toBe("wrong-code");

    // The default handling of a 401 would have refreshed the session and then ended it.
    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe(SESSION);
  });

  it("types the second-factor refusals by problem type, not by wording", async () => {
    expect(await failureOf(problem(409, "mfa-not-enrolled", "Anything at all"))).toBe("not-enrolled");
    expect(await failureOf(problem(403, "mfa-unavailable-for-sso", "Anything at all"))).toBe("sso");
    expect(await failureOf(problem(403, "mfa-reauthentication-required", "Anything at all"))).toBe("reauthenticate");
  });

  it("does not mistake a single sign-on session for a lockout, though both are a 403", async () => {
    expect(await failureOf(problem(403, "mfa-unavailable-for-sso", "Locked, it says, in the wording"))).toBe("sso");
    expect(await failureOf(problem(403, "mfa-locked", "Too many wrong codes"))).toBe("locked");
  });

  it("types a lockout however the service chooses to signal it", async () => {
    expect(await failureOf(problem(429, "rate-limited", "Slow down"))).toBe("locked");
    expect(await failureOf(problem(403, "forbidden", "Locked for now"))).toBe("locked");
    expect(await failureOf(problem(423, "locked", "Locked for now"))).toBe("locked");
    expect(await failureOf(problem(401, "mfa-locked", "Locked for now"))).toBe("locked");
    expect(await failureOf(problem(403, "password-locked", "Too many wrong passwords"))).toBe("locked");
  });

  it("types a code the server refused as wrong, whichever status carries it", async () => {
    expect(await failureOf(problem(422, "mfa-code-invalid", "That code is not valid"))).toBe("wrong-code");
    expect(await failureOf(problem(422, "mfa-code-replayed", "Wait for the next code"))).toBe("wrong-code");
  });

  it("does not call an enrolment refusal a wrong code: those are not the prompt's to fix", async () => {
    // Both are 422, which alone would read as "retype the digits".
    expect(await failureOf(problem(422, "mfa-current-code-required", "Enter a current code"))).toBe("unavailable");
    expect(await failureOf(problem(422, "password-incorrect", "That password is not right"))).toBe("unavailable");
  });

  it("types everything else as unavailable, and says nothing about the code", async () => {
    expect(await failureOf(problem(500, "internal", "Boom"))).toBe("unavailable");
    expect(await failureOf(problem(503, "unavailable", "Down"))).toBe("unavailable");
    expect(await failureOf(problem(503, "mfa-reseal-pending", "Being migrated"))).toBe("unavailable");
    expect(await failureOf(problem(409, "conflict", "Something else conflicts"))).toBe("unavailable");
    expect(await failureOf(problem(404, "not-found", "Gone"))).toBe("unavailable");
  });

  it("types a dropped connection as unavailable, with no status", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));

    const error = await stepUp("123456").catch((caught: unknown) => caught);

    expect(error).toBeInstanceOf(StepUpError);
    expect((error as StepUpError).failure).toBe("unavailable");
    expect((error as StepUpError).status).toBeNull();
  });

  it("refuses an answer that is not a usable token", async () => {
    for (const body of [{}, { access_token: "", expires_in: 180 }, { access_token: STEP_UP }, { access_token: STEP_UP, expires_in: 0 }]) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(ok(body)));
      const error = await stepUp("123456").catch((caught: unknown) => caught);
      expect((error as StepUpError).failure).toBe("unavailable");
    }
  });

  it("gives up on a request that never answers, so the dialog can be used again", async () => {
    vi.useFakeTimers();
    vi.stubGlobal(
      "fetch",
      vi.fn(
        (_url: string, init?: RequestInit) =>
          new Promise<Response>((_resolve, reject) => {
            init?.signal?.addEventListener("abort", () => reject(new DOMException("Aborted", "AbortError")));
          }),
      ),
    );

    const pending = stepUp("123456").catch((caught: unknown) => caught);
    await vi.advanceTimersByTimeAsync(10_000);

    const error = await pending;
    expect(error).toBeInstanceOf(StepUpError);
    expect((error as StepUpError).failure).toBe("unavailable");
  });

  it("carries the server's own sentence on the error", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(problem(401, "unauthorized", "That code is not valid")));

    const error = (await stepUp("123456").catch((caught: unknown) => caught)) as StepUpError;

    expect(error.message).toBe("That code is not valid");
    expect(error.status).toBe(401);
    expect(error.problem?.type).toBe("https://errors.opsgrids.dev/unauthorized");
  });
});

describe("classifyStepUpFailure", () => {
  it("reads the slug off the last segment of the type URI, in any case", () => {
    expect(classifyStepUpFailure(409, { type: "https://errors.opsgrids.dev/MFA-Not-Enrolled" })).toBe("not-enrolled");
    expect(classifyStepUpFailure(409, { type: "mfa-not-enrolled" })).toBe("not-enrolled");
  });

  it("does not guess from a 409 with no problem document", () => {
    expect(classifyStepUpFailure(409, null)).toBe("unavailable");
  });
});
