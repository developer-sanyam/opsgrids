/**
 * Test-only harness for the screens that reveal a sealed value: a fake gateway that answers what
 * the auth service and the reveal endpoints would, the providers a screen needs, and the moves a
 * person makes on the step-up dialog. The real `useStepUp`, the real `StepUpDialog` and the real
 * api client run under it, so what a test sees is what crosses the wire and what is on screen.
 */

import type { ReactElement } from "react";
import { render, screen } from "@testing-library/react";
import type { UserEvent } from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { vi } from "vitest";

import { PermissionProvider } from "@opsgrids/client";

import { DirectoryContext, EMPTY_DIRECTORY } from "@/data/directoryContext";
import type { Directory } from "@/data/directoryContext";
import { GoTo, LocationProbe } from "./testProbes";

export const SESSION_TOKEN = "session-access-token";
export const STEP_UP_TOKEN = "step-up-access-token";

export const STEP_UP = "POST /api/v1/auth/mfa/step-up";
export const ME = "GET /api/v1/auth/me";
export const REFRESH = "POST /api/v1/auth/refresh";

export interface Call {
  key: string;
  authorization: string | undefined;
  body: unknown;
}

export type Answer = (call: Call) => Response | Promise<Response>;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

/** An RFC 7807 answer the way the gateway writes one: `type` names the case, `detail` is the sentence. */
export function problem(status: number, detail: string, slug = "problem"): Response {
  return json({ type: `https://errors.opsgrids.dev/${slug}`, title: detail, status, detail }, status);
}

/** What `/auth/mfa/step-up` answers when the code is good. */
export function stepUpToken(accessToken: string = STEP_UP_TOKEN, expiresIn = 180): Response {
  return json({
    status: "complete",
    access_token: accessToken,
    token_type: "Bearer",
    expires_in: expiresIn,
    organization_id: "org-1",
    membership_id: "mem-1",
  });
}

export interface Gateway {
  calls: Call[];
  /** Every call made to `key` ("POST /api/v1/leads/lead-1:reveal"), in order. */
  callsTo: (key: string) => Call[];
}

/**
 * Stubs `fetch` with `routes`, keyed "METHOD /api/v1/path" (the query string is not part of the
 * key). A call to a route that was not declared fails the test at once. Pair it with
 * `vi.unstubAllGlobals()`.
 */
export function stubGateway(routes: Record<string, Answer>): Gateway {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const headers = (init?.headers ?? {}) as Record<string, string>;
      const key = `${init?.method ?? "GET"} ${new URL(String(input), "http://localhost").pathname}`;
      const call: Call = {
        key,
        authorization: headers.Authorization,
        body: typeof init?.body === "string" ? (JSON.parse(init.body) as unknown) : undefined,
      };
      calls.push(call);
      const route = routes[key];
      if (route === undefined) throw new Error(`No stub for ${key}`);
      return Promise.resolve(route(call));
    }),
  );
  return { calls, callsTo: (key) => calls.filter((call) => call.key === key) };
}

/** A response the test answers later — a reveal that is still on its way. */
export function held(): { route: Answer; respond: (response: Response) => void } {
  const parts: { respond: (response: Response) => void } = { respond: () => undefined };
  return {
    route: () =>
      new Promise<Response>((resolve) => {
        parts.respond = resolve;
      }),
    respond: (response) => parts.respond(response),
  };
}

export interface ScreenOptions {
  /** Where the router starts, e.g. "/leads/lead-1". */
  at: string;
  /** The route pattern the screen is mounted at, e.g. "/leads/:id". */
  path: string;
  grants: string[];
  directory?: Partial<Directory>;
  /** Paths the test may navigate to without unmounting the screen. */
  links?: string[];
}

export function renderScreen(element: ReactElement, options: ScreenOptions) {
  return render(
    <MemoryRouter initialEntries={[options.at]}>
      <PermissionProvider grants={options.grants}>
        <DirectoryContext.Provider value={{ ...EMPTY_DIRECTORY, status: "success", ...options.directory }}>
          <LocationProbe />
          {(options.links ?? []).map((link) => (
            <GoTo key={link} path={link} />
          ))}
          <Routes>
            <Route path={options.path} element={element} />
          </Routes>
        </DirectoryContext.Provider>
      </PermissionProvider>
    </MemoryRouter>,
  );
}

export function codeField(): HTMLElement {
  return screen.getByRole("textbox", { name: /6-digit code/ });
}

/** Types a code into the dialog. The sixth digit submits it, as it does for a person. */
export async function enterCode(user: UserEvent, code = "123456"): Promise<void> {
  await user.type(codeField(), code);
}

export function stepUpDialog(): HTMLElement {
  return screen.getByRole("dialog", { name: "Confirm it's you" });
}

/** Everything the page has written to web storage, as one string a test can look through. */
export function storedText(): string {
  return [localStorage, sessionStorage]
    .flatMap((store) =>
      Array.from({ length: store.length }, (_, index) => {
        const key = store.key(index) ?? "";
        return `${key}=${store.getItem(key) ?? ""}`;
      }),
    )
    .join("|");
}
