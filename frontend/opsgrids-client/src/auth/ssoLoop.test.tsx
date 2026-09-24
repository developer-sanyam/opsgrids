/**
 * Adopted from the security review's loop probes (fix round 1, B1) — the full pipeline this
 * time: a real `<AuthProvider>`, `<RequireAuth>` and `<RedirectToCentralLogin>` wired through an
 * actual router, the same shape a product app's `main.tsx` builds. The bug: `RequireAuth` kept
 * the wanted page only in router state; `RedirectToCentralLogin` built `next` from
 * `window.location.href` — which, by the time it renders, is already `/login` — and fired
 * regardless of status, so a refresh that succeeded still bounced to the dashboard, which
 * bounced straight back.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { setAccessToken, setUnauthorizedHandler } from "../session";
import { AuthProvider } from "./AuthProvider";
import { RequireAuth } from "./RequireAuth";
import { RedirectToCentralLogin } from "./RedirectToCentralLogin";
import { configureAuthOrigin } from "./origin";

function respond(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}
function fakeToken(claims: Record<string, unknown>): string {
  const seg = (v: unknown) => btoa(JSON.stringify(v));
  return `${seg({ alg: "none" })}.${seg(claims)}.sig`;
}

const ME_BODY = { user_id: "u", email: "e", full_name: "n", organizations: [], roles: [], amr: [] };

const realLocation = window.location;
let assign: ReturnType<typeof vi.fn>;

function stubLocation(pathname: string): void {
  const fake = {
    ...realLocation,
    pathname,
    href: `http://localhost:3000${pathname}`,
    assign,
  };
  Object.defineProperty(window, "location", { configurable: true, value: fake });
}

beforeEach(() => {
  assign = vi.fn();
  setAccessToken(null);
  configureAuthOrigin(null);
  sessionStorage.clear();
  window.history.replaceState(null, "", "/");
});

afterEach(() => {
  Object.defineProperty(window, "location", { configurable: true, value: realLocation });
  vi.unstubAllGlobals();
  vi.useRealTimers();
  setUnauthorizedHandler(null);
  setAccessToken(null);
  configureAuthOrigin(null);
});

function productRouter() {
  return createBrowserRouter([
    {
      element: (
        <AuthProvider>
          <Outlet />
        </AuthProvider>
      ),
      children: [
        {
          path: "/login",
          element: (
            <RedirectToCentralLogin authOrigin="https://app.opsgrids.com" logo={<span />} appName="People" />
          ),
        },
        {
          path: "/",
          element: (
            <RequireAuth logo={<span />}>
              <div>app shell</div>
            </RequireAuth>
          ),
          children: [{ path: "employees/:id", element: <div>employee page</div> }],
        },
      ],
    },
  ]);
}

describe("no session anywhere: the browser lands on central login with the wanted page, once", () => {
  it("carries the page the user actually wanted as next, not the /login URL it bounced through", async () => {
    configureAuthOrigin("https://app.opsgrids.com");
    window.history.replaceState(null, "", "/employees/42");
    stubLocation("/employees/42");
    vi.stubGlobal("fetch", vi.fn(() => Promise.resolve(respond(401, {}))));

    render(<RouterProvider router={productRouter()} />);

    await waitFor(() => expect(assign).toHaveBeenCalledTimes(1));
    expect(assign).toHaveBeenCalledWith(
      "https://app.opsgrids.com/login?next=" + encodeURIComponent("http://localhost:3000/employees/42"),
    );
  });
});

describe("a session already exists: the silent refresh on /login succeeds", () => {
  it("lands on the wanted page inside this app — never bounces to the dashboard at all", async () => {
    configureAuthOrigin("https://app.opsgrids.com");
    window.history.replaceState(null, "", "/login");
    stubLocation("/login");
    const exp = Math.floor(Date.now() / 1000) + 900;
    vi.stubGlobal(
      "fetch",
      vi.fn((input: RequestInfo | URL) => {
        const url = String(input);
        if (url.endsWith("/auth/refresh")) {
          return Promise.resolve(respond(200, { access_token: fakeToken({ exp }), expires_in: 900 }));
        }
        return Promise.resolve(respond(200, ME_BODY));
      }),
    );

    render(<RouterProvider router={productRouter()} />);

    await waitFor(() => expect(screen.getByText("app shell")).toBeTruthy());
    expect(assign).not.toHaveBeenCalled();
  });
});
