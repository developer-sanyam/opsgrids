/**
 * The step-up flow end to end, at the wire: a real `apiRequest` for the reveal call, a real
 * `authApi.stepUp`, and `fetch` stubbed with what the auth service and a reveal endpoint would say.
 * The dialog is a bare stand-in that exposes what `dialogProps` says — `@opsgrids/client` cannot
 * import `@opsgrids/ui`, and this suite is about the hook, not about pixels.
 *
 * What the tests hold the hook to, in order of how much a mistake would cost:
 * a lapsed step-up token must never sign the person out; the token must never leave memory; a
 * wrong code must leave the dialog open on the same field; and a second reveal inside the token's
 * lifetime must not ask again.
 */

import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ApiError, apiRequest } from "../http";
import { getAccessToken, setAccessToken, setUnauthorizedHandler } from "../session";
import { isStepUpCancelled, StepUpCancelledError } from "./cancelled";
import { clearStepUpToken, getStepUpToken } from "./holder";
import { revealRequest } from "./reveal";
import { useStepUp } from "./useStepUp";
import type { StepUpPromptProps } from "./useStepUp";

const SESSION = "session-access-token";
const RENEWED_SESSION = "renewed-session-access-token";
const STEP_UP = "step-up-access-token";
const SECOND_STEP_UP = "second-step-up-access-token";

interface Call {
  url: string;
  method: string;
  authorization: string | undefined;
  body: unknown;
}

let calls: Call[];
let routes: Record<string, (call: Call) => Response | Promise<Response>>;
let unauthorized: ReturnType<typeof vi.fn>;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

function problem(status: number, slug: string, detail: string): Response {
  return json({ type: `https://errors.opsgrids.dev/${slug}`, title: detail, status, detail }, status);
}

function tokenOut(accessToken: string, expiresIn = 180): Response {
  return json({
    status: "complete",
    access_token: accessToken,
    token_type: "Bearer",
    expires_in: expiresIn,
    organization_id: "org-1",
    membership_id: "mem-1",
  });
}

const STEP_UP_URL = "/api/v1/auth/mfa/step-up";
const PHONE_URL = "/api/v1/contacts/c-1/reveal/phone";
const EMAIL_URL = "/api/v1/contacts/c-1/reveal/email";

function stubFetch(): void {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const call: Call = {
        url: String(input),
        method: init?.method ?? "GET",
        authorization: headers.Authorization,
        body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
      };
      calls.push(call);
      const route = routes[call.url];
      if (!route) throw new Error(`No stub for ${call.method} ${call.url}`);
      return Promise.resolve(route(call));
    }),
  );
}

/** Leave `url` unanswered until the test says what the server said. */
function holdOpen(url: string): { respond: (response: Response) => void } {
  const held: { respond: (response: Response) => void } = { respond: () => undefined };
  routes[url] = () =>
    new Promise<Response>((resolve) => {
      held.respond = resolve;
    });
  return held;
}

function callsTo(url: string): Call[] {
  return calls.filter((call) => call.url === url);
}

/** A bare stand-in for `StepUpDialog`: exactly the props, nothing the hook did not ask for. */
function FakeDialog(props: StepUpPromptProps) {
  const [code, setCode] = useState("");
  if (!props.open) return null;
  return (
    <div role="dialog" aria-label="Step-up" data-busy={props.busy}>
      <p data-testid="mfa-state">{props.mfaState}</p>
      <p data-testid="dialog-error">{props.error ?? ""}</p>
      {props.mfaState === "ready" ? (
        <input aria-label="Code" value={code} onChange={(event) => setCode(event.target.value)} />
      ) : null}
      <a href={props.securityHref}>Security page</a>
      <button type="button" onClick={() => props.onSubmit(code)}>
        Verify
      </button>
      <button type="button" onClick={props.onCancel}>
        Cancel
      </button>
    </div>
  );
}

function Screen({ keep }: { keep?: boolean | undefined }) {
  const stepUp = useStepUp();
  const [outcomes, setOutcomes] = useState<Record<string, string>>({});

  async function reveal(field: "phone" | "email") {
    try {
      const result = await stepUp.run(
        (headers) =>
          apiRequest<{ value: string }>(`/contacts/c-1/reveal/${field}`, {
            method: "POST",
            ...revealRequest(headers),
          }),
        { keep },
      );
      setOutcomes((previous) => ({ ...previous, [field]: result.value }));
    } catch (error) {
      const label = isStepUpCancelled(error)
        ? "cancelled"
        : error instanceof ApiError
          ? `error ${error.status}`
          : "error";
      setOutcomes((previous) => ({ ...previous, [field]: label }));
    }
  }

  return (
    <div>
      <button type="button" onClick={() => void reveal("phone")}>
        Reveal phone
      </button>
      <button type="button" onClick={() => void reveal("email")}>
        Reveal email
      </button>
      <button type="button" onClick={stepUp.clear}>
        Close drawer
      </button>
      <p data-testid="phone">{outcomes.phone ?? ""}</p>
      <p data-testid="email">{outcomes.email ?? ""}</p>
      <p data-testid="state">{stepUp.state}</p>
      <FakeDialog {...stepUp.dialogProps} />
    </div>
  );
}

async function enterCode(user: ReturnType<typeof userEvent.setup>, code: string) {
  await user.type(screen.getByLabelText("Code"), code);
  await user.click(screen.getByRole("button", { name: "Verify" }));
}

beforeEach(() => {
  calls = [];
  routes = {
    [STEP_UP_URL]: () => tokenOut(STEP_UP),
    [PHONE_URL]: () => json({ value: "+91 98765 43210" }),
    [EMAIL_URL]: () => json({ value: "priya@acme.example" }),
    "/api/v1/auth/me": () => json({ user_id: "u-1" }),
  };
  setAccessToken(SESSION);
  clearStepUpToken();
  unauthorized = vi.fn();
  setUnauthorizedHandler(unauthorized);
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  clearStepUpToken();
  localStorage.clear();
  sessionStorage.clear();
});

describe("useStepUp: asking for a code", () => {
  it("opens the dialog when there is no token, and runs the reveal with the step-up bearer once a code is accepted", async () => {
    const user = userEvent.setup();
    render(<Screen />);
    expect(screen.queryByRole("dialog")).toBeNull();

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    expect(await screen.findByRole("dialog", { name: "Step-up" })).toBeTruthy();
    expect(screen.getByTestId("state").textContent).toBe("asking");
    // Nothing was sent to the reveal endpoint before the person proved anything.
    expect(callsTo(PHONE_URL)).toHaveLength(0);

    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("phone").textContent).toBe("+91 98765 43210"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("state").textContent).toBe("idle");

    const [verify] = callsTo(STEP_UP_URL);
    expect(verify?.body).toEqual({ code: "123456" });
    expect(verify?.authorization).toBe(`Bearer ${SESSION}`);

    const [reveal] = callsTo(PHONE_URL);
    expect(reveal?.authorization).toBe(`Bearer ${STEP_UP}`);
  });

  it("asks once for a phone and then an email inside the token's lifetime", async () => {
    const user = userEvent.setup();
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(screen.getByTestId("phone").textContent).not.toBe(""));

    await user.click(screen.getByRole("button", { name: "Reveal email" }));

    await waitFor(() => expect(screen.getByTestId("email").textContent).toBe("priya@acme.example"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(callsTo(STEP_UP_URL)).toHaveLength(1);
    expect(callsTo(EMAIL_URL)[0]?.authorization).toBe(`Bearer ${STEP_UP}`);
  });

  it("drops the token right after use when the caller says not to keep it", async () => {
    const user = userEvent.setup();
    render(<Screen keep={false} />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(screen.getByTestId("phone").textContent).not.toBe(""));
    expect(getStepUpToken()).toBeNull();

    await user.click(screen.getByRole("button", { name: "Reveal email" }));
    expect(await screen.findByRole("dialog", { name: "Step-up" })).toBeTruthy();
  });

  it("asks again after clear()", async () => {
    const user = userEvent.setup();
    render(<Screen />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(getStepUpToken()).toBe(STEP_UP));

    await user.click(screen.getByRole("button", { name: "Close drawer" }));
    expect(getStepUpToken()).toBeNull();

    await user.click(screen.getByRole("button", { name: "Reveal email" }));
    expect(await screen.findByRole("dialog", { name: "Step-up" })).toBeTruthy();
  });

  it("puts one dialog up for two reveals that both need a code, and one code serves both", async () => {
    const user = userEvent.setup();
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await user.click(screen.getByRole("button", { name: "Reveal email" }));
    expect(screen.getAllByRole("dialog")).toHaveLength(1);

    await enterCode(user, "123456");

    await waitFor(() => {
      expect(screen.getByTestId("phone").textContent).not.toBe("");
      expect(screen.getByTestId("email").textContent).not.toBe("");
    });
    expect(callsTo(STEP_UP_URL)).toHaveLength(1);
    expect(callsTo(PHONE_URL)[0]?.authorization).toBe(`Bearer ${STEP_UP}`);
    expect(callsTo(EMAIL_URL)[0]?.authorization).toBe(`Bearer ${STEP_UP}`);
  });

  it("sends a code once however many times submit fires before the answer lands", async () => {
    const server = holdOpen(STEP_UP_URL);
    const { result } = renderHook(() => useStepUp());

    let revealed: Promise<string> | undefined;
    act(() => {
      revealed = result.current.run(() => Promise.resolve("done"));
    });
    act(() => {
      result.current.dialogProps.onSubmit("123456");
      result.current.dialogProps.onSubmit("123456");
      result.current.dialogProps.onSubmit("123456");
    });
    expect(callsTo(STEP_UP_URL)).toHaveLength(1);
    expect(result.current.state).toBe("verifying");

    await act(async () => {
      server.respond(tokenOut(STEP_UP));
      await revealed;
    });
    expect(await revealed).toBe("done");
  });
});

describe("useStepUp: an expired or refused step-up token", () => {
  it("does not sign the person out when a held token turns out to be dead, and asks for a fresh code", async () => {
    const user = userEvent.setup();
    let phoneCalls = 0;
    routes[PHONE_URL] = () => {
      phoneCalls += 1;
      return phoneCalls === 1
        ? problem(401, "unauthorized", "Token has expired")
        : json({ value: "+91 98765 43210" });
    };
    routes[STEP_UP_URL] = (() => {
      let issued = 0;
      return () => {
        issued += 1;
        return tokenOut(issued === 1 ? STEP_UP : SECOND_STEP_UP);
      };
    })();
    render(<Screen />);

    // The first reveal earns a token; the phone endpoint then refuses it as expired.
    await user.click(screen.getByRole("button", { name: "Reveal email" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(screen.getByTestId("email").textContent).not.toBe(""));

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    // A dead token is not a dead session: no sign-out, the session token is untouched, and the
    // person is simply asked for a code again.
    expect(await screen.findByRole("dialog", { name: "Step-up" })).toBeTruthy();
    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe(SESSION);
    expect(getStepUpToken()).toBeNull();
    expect(callsTo("/api/v1/auth/refresh")).toHaveLength(0);

    await enterCode(user, "654321");
    await waitFor(() => expect(screen.getByTestId("phone").textContent).toBe("+91 98765 43210"));
    expect(callsTo(PHONE_URL)[1]?.authorization).toBe(`Bearer ${SECOND_STEP_UP}`);
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("does not sign the person out, and does not loop, when a fresh token is refused", async () => {
    const user = userEvent.setup();
    routes[PHONE_URL] = () => problem(401, "unauthorized", "Token has expired");
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("phone").textContent).toBe("error 401"));
    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe(SESSION);
    expect(callsTo(PHONE_URL)).toHaveLength(1);
    expect(callsTo(STEP_UP_URL)).toHaveLength(1);
    expect(getStepUpToken()).toBeNull();
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("hands every other refusal of the reveal back to the caller untouched", async () => {
    const user = userEvent.setup();
    routes[PHONE_URL] = () => problem(403, "forbidden", "You cannot reveal this");
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("phone").textContent).toBe("error 403"));
    expect(unauthorized).not.toHaveBeenCalled();
    // A 403 says nothing about the token, so it is kept for the next field.
    expect(getStepUpToken()).toBe(STEP_UP);
  });
});

describe("useStepUp: refusals of the code", () => {
  it("keeps the dialog open on a wrong code, without signing the person out, and accepts the next one", async () => {
    const user = userEvent.setup();
    let attempts = 0;
    routes[STEP_UP_URL] = () => {
      attempts += 1;
      return attempts === 1 ? problem(401, "unauthorized", "That code is not valid") : tokenOut(STEP_UP);
    };
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "000000");

    await waitFor(() => expect(screen.getByTestId("dialog-error").textContent).toBe("wrong-code"));
    expect(screen.getByRole("dialog", { name: "Step-up" }).getAttribute("data-busy")).toBe("false");
    expect(screen.getByTestId("mfa-state").textContent).toBe("ready");
    expect(screen.getByTestId("state").textContent).toBe("asking");
    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe(SESSION);
    expect(callsTo(PHONE_URL)).toHaveLength(0);

    await user.clear(screen.getByLabelText("Code"));
    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("phone").textContent).toBe("+91 98765 43210"));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("clears the last error while the next code is being checked", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = () => problem(401, "unauthorized", "That code is not valid");
    render(<Screen />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "000000");
    await waitFor(() => expect(screen.getByTestId("dialog-error").textContent).toBe("wrong-code"));

    const server = holdOpen(STEP_UP_URL);
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await waitFor(() => expect(screen.getByRole("dialog").getAttribute("data-busy")).toBe("true"));
    expect(screen.getByTestId("dialog-error").textContent).toBe("");
    expect(screen.getByTestId("state").textContent).toBe("verifying");
    await act(async () => {
      server.respond(problem(401, "unauthorized", "That code is not valid"));
    });
  });

  it("reports a lockout as locked, and stays open", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = () => problem(429, "rate-limited", "Too many attempts");
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("dialog-error").textContent).toBe("locked"));
    expect(screen.getByTestId("mfa-state").textContent).toBe("ready");
    expect(unauthorized).not.toHaveBeenCalled();
  });

  it("reports a dropped connection as unavailable, and stays open", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = () => {
      throw new TypeError("Failed to fetch");
    };
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("dialog-error").textContent).toBe("unavailable"));
    expect(screen.getByRole("dialog").getAttribute("data-busy")).toBe("false");
  });

  it("turns the dialog into the not-enrolled state, with the Security page to go to", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = () => problem(409, "mfa-not-enrolled", "No second factor");
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("mfa-state").textContent).toBe("not-enrolled"));
    expect(screen.getByTestId("dialog-error").textContent).toBe("");
    expect(screen.getByRole("link", { name: "Security page" }).getAttribute("href")).toBe("/account/security");
    expect(unauthorized).not.toHaveBeenCalled();
    expect(callsTo(PHONE_URL)).toHaveLength(0);
  });

  it("turns the dialog into the single-sign-on state", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = () => problem(409, "step-up-sso-session", "Not for SSO sessions");
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("mfa-state").textContent).toBe("sso"));
    expect(callsTo(PHONE_URL)).toHaveLength(0);
  });

  it("opens ready again for the next reveal after a not-enrolled dialog was closed", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = () => problem(409, "mfa-not-enrolled", "No second factor");
    render(<Screen />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(screen.getByTestId("mfa-state").textContent).toBe("not-enrolled"));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));

    expect(await screen.findByTestId("mfa-state")).toBeTruthy();
    expect(screen.getByTestId("mfa-state").textContent).toBe("ready");
  });
});

describe("useStepUp: a session token that lapsed under the dialog", () => {
  it("renews the session once and retries the code, instead of calling a right code wrong", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = (call) =>
      call.authorization === `Bearer ${SESSION}`
        ? problem(401, "unauthorized", "Token has expired")
        : tokenOut(STEP_UP);
    routes["/api/v1/auth/me"] = (call) =>
      call.authorization === `Bearer ${SESSION}`
        ? problem(401, "unauthorized", "Token has expired")
        : json({ user_id: "u-1" });
    routes["/api/v1/auth/refresh"] = () => tokenOut(RENEWED_SESSION, 900);
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");

    await waitFor(() => expect(screen.getByTestId("phone").textContent).toBe("+91 98765 43210"));
    const attempts = callsTo(STEP_UP_URL);
    expect(attempts.map((call) => call.authorization)).toEqual([
      `Bearer ${SESSION}`,
      `Bearer ${RENEWED_SESSION}`,
    ]);
    expect(unauthorized).not.toHaveBeenCalled();
    expect(getAccessToken()).toBe(RENEWED_SESSION);
  });

  it("does not spend a second attempt on a wrong code when the session is fine", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = () => problem(401, "unauthorized", "That code is not valid");
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "000000");

    await waitFor(() => expect(screen.getByTestId("dialog-error").textContent).toBe("wrong-code"));
    // One attempt at the code, one look at the session, no second attempt.
    expect(callsTo(STEP_UP_URL)).toHaveLength(1);
    expect(callsTo("/api/v1/auth/me")).toHaveLength(1);
  });

  it("lets the ordinary sign-out happen when the session really is over", async () => {
    const user = userEvent.setup();
    routes[STEP_UP_URL] = () => problem(401, "unauthorized", "Token has expired");
    routes["/api/v1/auth/me"] = () => problem(401, "unauthorized", "Token has expired");
    routes["/api/v1/auth/refresh"] = () => problem(401, "unauthorized", "No session");
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");

    await waitFor(() => expect(unauthorized).toHaveBeenCalledTimes(1));
    expect(getAccessToken()).toBeNull();
  });
});

describe("useStepUp: closing and leaving", () => {
  it("rejects the reveal with a cancellation, and never calls it, when the dialog is closed", async () => {
    const user = userEvent.setup();
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await user.click(screen.getByRole("button", { name: "Cancel" }));

    await waitFor(() => expect(screen.getByTestId("phone").textContent).toBe("cancelled"));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("state").textContent).toBe("idle");
    expect(callsTo(STEP_UP_URL)).toHaveLength(0);
    expect(callsTo(PHONE_URL)).toHaveLength(0);
  });

  it("refuses to close while a code is with the server", async () => {
    const user = userEvent.setup();
    const server = holdOpen(STEP_UP_URL);
    render(<Screen />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(screen.getByRole("dialog").getAttribute("data-busy")).toBe("true"));

    await user.click(screen.getByRole("button", { name: "Cancel" }));

    expect(screen.getByRole("dialog")).toBeTruthy();
    await act(async () => {
      server.respond(problem(429, "rate-limited", "Too many attempts"));
    });
  });

  it("rejects a waiting reveal and drops the token when the screen goes away", async () => {
    const { result, unmount } = renderHook(() => useStepUp());
    let waiting: Promise<string> | undefined;
    act(() => {
      waiting = result.current.run(() => Promise.resolve("never"));
    });
    expect(result.current.dialogProps.open).toBe(true);

    unmount();

    await expect(waiting).rejects.toBeInstanceOf(StepUpCancelledError);
    expect(getStepUpToken()).toBeNull();
  });

  it("drops a held token when the screen that earned it unmounts", async () => {
    const user = userEvent.setup();
    const { unmount } = render(<Screen />);
    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(getStepUpToken()).toBe(STEP_UP));

    unmount();

    expect(getStepUpToken()).toBeNull();
  });
});

describe("useStepUp: where the token is allowed to live", () => {
  it("never writes the step-up token to web storage, and never makes it the session", async () => {
    const user = userEvent.setup();
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(screen.getByTestId("phone").textContent).not.toBe(""));

    expect(getAccessToken()).toBe(SESSION);
    const stored = [localStorage, sessionStorage].flatMap((store) =>
      Array.from({ length: store.length }, (_, index) => {
        const key = store.key(index) ?? "";
        return `${key}=${store.getItem(key) ?? ""}`;
      }),
    );
    expect(stored.join("|")).not.toContain(STEP_UP);
    expect(localStorage.length).toBe(0);
  });

  it("presents the step-up token, and only that, to the reveal call", async () => {
    const user = userEvent.setup();
    render(<Screen />);

    await user.click(screen.getByRole("button", { name: "Reveal phone" }));
    await enterCode(user, "123456");
    await waitFor(() => expect(screen.getByTestId("phone").textContent).not.toBe(""));

    for (const call of callsTo(PHONE_URL)) {
      expect(call.authorization).toBe(`Bearer ${STEP_UP}`);
      expect(call.authorization).not.toContain(SESSION);
    }
  });
});
