/**
 * Sign-in, through `useAuth()` — the same session `AuthProvider` gives every other screen. Mirrors
 * the coverage `opsgrids-dashboard`'s own `Login.test.tsx` holds for the shared client, scoped to
 * what this app's own screen adds: the form, the one-message credential error, the no-double-
 * submit guard, and the multi-org hop.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, getAccessToken, setAccessToken } from "@opsgrids/client";

import { LoginPage } from "./Login";

const ORG_A = { organization_id: "org-a", slug: "acme-north", name: "Acme North DC", roles: ["fulfilment_worker"] };
const ORG_B = { organization_id: "org-b", slug: "acme-south", name: "Acme South DC", roles: ["fulfilment_worker"] };

const COMPLETE = {
  status: "complete",
  access_token: "access-token-value",
  token_type: "Bearer",
  expires_in: 900,
  organization_id: ORG_A.organization_id,
  membership_id: "mem-1",
};

const ME = {
  user_id: "u-1",
  email: "asha.picker@acme.example.com",
  full_name: "Asha Rao",
  organization_id: ORG_A.organization_id,
  membership_id: "mem-1",
  roles: ["fulfilment_worker"],
  amr: ["pwd"],
  must_change_password: false,
  mfa_enabled: false,
  organizations: [ORG_A],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": status >= 400 ? "application/problem+json" : "application/json" },
  });
}

interface Call {
  url: string;
  body: Record<string, unknown>;
}

let calls: Call[] = [];
let routes: Record<string, () => Response>;

function stubFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const raw = typeof init?.body === "string" ? init.body : "{}";
      calls.push({ url, body: JSON.parse(raw) as Record<string, unknown> });
      const route = routes[url];
      if (!route) throw new Error(`No stub for ${url}`);
      return Promise.resolve(route());
    }),
  );
}

function renderPage() {
  return render(
    <MemoryRouter initialEntries={["/login"]}>
      <AuthProvider>
        <LoginPage />
      </AuthProvider>
    </MemoryRouter>,
  );
}

async function signIn(user: ReturnType<typeof userEvent.setup>) {
  await user.type(screen.getByLabelText("Work email"), "asha.picker@acme.example.com");
  await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

beforeEach(() => {
  calls = [];
  routes = {};
  setAccessToken(null);
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
});

it("signs in and leaves the sign-in form", async () => {
  const user = userEvent.setup();
  routes = {
    "/api/v1/auth/login": () => json(COMPLETE),
    "/api/v1/auth/me": () => json(ME),
  };
  renderPage();

  await signIn(user);

  await waitFor(() => expect(getAccessToken()).toBe("access-token-value"));
  await waitFor(() => expect(screen.queryByRole("heading", { name: "Sign in to Worker" })).toBeNull());
});

it("shows one message for a bad email or password, never which half was wrong, and keeps the email", async () => {
  const user = userEvent.setup();
  routes = {
    "/api/v1/auth/login": () => json({ title: "Unauthorized", status: 401, detail: "Wrong password" }, 401),
  };
  renderPage();

  await signIn(user);

  expect((await screen.findByText("That email or password is incorrect.")).textContent).toBe(
    "That email or password is incorrect.",
  );
  expect(screen.getByLabelText("Work email")).toHaveProperty("value", "asha.picker@acme.example.com");
});

it("disables the button and blocks a second submit while the request is in flight", async () => {
  const user = userEvent.setup();
  let resolveLogin: ((response: Response) => void) | undefined;
  const pendingLogin = new Promise<Response>((resolve) => {
    resolveLogin = resolve;
  });
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      calls.push({ url, body: {} });
      if (url === "/api/v1/auth/login") return pendingLogin;
      if (url === "/api/v1/auth/me") return Promise.resolve(json(ME));
      throw new Error(`No stub for ${url}`);
    }),
  );
  renderPage();

  await signIn(user);
  const button = screen.getByRole("button", { name: "Sign in" }) as HTMLButtonElement;
  expect(button.disabled).toBe(true);
  await user.click(button); // A click on a disabled button reaches nothing.

  expect(calls.filter((c) => c.url === "/api/v1/auth/login")).toHaveLength(1);
  resolveLogin?.(json(COMPLETE));
  await waitFor(() => expect(getAccessToken()).toBe("access-token-value"));
});

describe("more than one organization", () => {
  it("offers a picker and finishes sign-in with the chosen one", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () =>
        json({ status: "org_selection_required", pre_auth_token: "pre-auth", organizations: [ORG_A, ORG_B] }),
      "/api/v1/auth/select-org": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderPage();

    await signIn(user);

    await screen.findByRole("heading", { name: "Choose an organization" });
    await user.click(screen.getByRole("button", { name: /Acme South DC/ }));

    await waitFor(() => expect(getAccessToken()).toBe("access-token-value"));
    const select = calls.find((c) => c.url === "/api/v1/auth/select-org");
    expect(select?.body).toEqual({ pre_auth_token: "pre-auth", organization_id: ORG_B.organization_id });
  });
});
