/**
 * Sign-in, entirely through `useAuth()` — including the second factor and the org-selection hop
 * that can follow it. This used to reach around the context for a raw `pre_auth_token` and end
 * every completed sign-in with a full page reload; both are gone. `AuthProvider` itself proves
 * the pre-auth token is never stored (`@opsgrids/client`'s own `auth.test.tsx`); what belongs here
 * is that the screen renders every step correctly and never reloads to get there.
 */
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider, getAccessToken, setAccessToken } from "@opsgrids/client";

import { LoginPage } from "./Login";

const PRE_AUTH = "pre-auth-token-value";
const ACCESS = "access-token-value";

const ORG_A = { organization_id: "org-a", slug: "acme-india", name: "Acme India", roles: ["finance_admin"] };
const ORG_B = { organization_id: "org-b", slug: "acme-uk", name: "Acme UK", roles: ["finance_viewer"] };

const COMPLETE = {
  status: "complete",
  access_token: ACCESS,
  token_type: "Bearer",
  expires_in: 900,
  organization_id: ORG_A.organization_id,
  membership_id: "mem-1",
};

const ME = {
  user_id: "u-1",
  email: "priya@acme.example.com",
  full_name: "Priya Nair",
  organization_id: ORG_A.organization_id,
  membership_id: "mem-1",
  roles: ["finance_admin"],
  amr: ["pwd", "otp"],
  must_change_password: false,
  mfa_enabled: true,
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
  await user.type(screen.getByLabelText("Work email"), "priya@acme.example.com");
  await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

/** Every value either web storage holds right now. */
function storedValues(): string[] {
  const values: string[] = [];
  for (const store of [localStorage, sessionStorage]) {
    for (let index = 0; index < store.length; index += 1) {
      const key = store.key(index);
      if (key !== null) values.push(`${key}=${store.getItem(key) ?? ""}`);
    }
  }
  return values;
}

beforeEach(() => {
  calls = [];
  routes = {};
  setAccessToken(null);
  localStorage.clear();
  sessionStorage.clear();
  stubFetch();
});

afterEach(() => {
  vi.unstubAllGlobals();
  setAccessToken(null);
  localStorage.clear();
  sessionStorage.clear();
});

describe("password-only sign-in", () => {
  it("signs in and hydrates the session in place, with no reload", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderPage();

    await signIn(user);

    await waitFor(() => expect(getAccessToken()).toBe(ACCESS));
    // The password form is gone — the provider's own `status` flip is what moved us on, in the
    // same document: nothing here calls `window.location.assign` any more.
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Sign in to OpsGrids" })).toBeNull());
  });

  it("shows one message for a bad email or password, never which half was wrong", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () => json({ title: "Unauthorized", status: 401, detail: "Wrong password" }, 401),
    };
    renderPage();

    await signIn(user);

    expect((await screen.findByRole("alert")).textContent).toContain("That email or password is incorrect.");
    expect(screen.getByLabelText("Work email")).toHaveProperty("value", "priya@acme.example.com");
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
    const button = screen.getByRole("button", { name: "Signing in…" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    await user.click(button); // A click on a disabled button reaches nothing.

    expect(calls.filter((c) => c.url === "/api/v1/auth/login")).toHaveLength(1);
    resolveLogin?.(json(COMPLETE));
    await waitFor(() => expect(getAccessToken()).toBe(ACCESS));
  });
});

describe("sign-in with a second factor", () => {
  it("asks for a code, then hydrates the session — no reload", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "/api/v1/auth/mfa/verify": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderPage();

    await signIn(user);

    const heading = await screen.findByRole("heading", { name: "Enter your code" });
    expect(heading).toBeTruthy();
    // The pre-auth token never touched this component's state or either web storage.
    expect(storedValues().join("|")).not.toContain(PRE_AUTH);

    await user.type(screen.getByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await waitFor(() => expect(getAccessToken()).toBe(ACCESS));
    // The code form is gone — hydrated in the same document, nothing reloaded to get there.
    await waitFor(() => expect(screen.queryByRole("heading", { name: "Enter your code" })).toBeNull());
    const verify = calls.find((c) => c.url === "/api/v1/auth/mfa/verify");
    expect(verify?.body).toEqual({ pre_auth_token: PRE_AUTH, code: "123456" });
  });

  it("shows the server's sentence for a wrong code and lets the next attempt through", async () => {
    const user = userEvent.setup();
    let attempt = 0;
    routes = {
      "/api/v1/auth/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "/api/v1/auth/mfa/verify": () => {
        attempt += 1;
        return attempt === 1
          ? json({ title: "Unauthorized", status: 401, detail: "That code is not valid" }, 401)
          : json(COMPLETE);
      },
      "/api/v1/auth/me": () => json(ME),
    };
    renderPage();

    await signIn(user);
    await screen.findByRole("heading", { name: "Enter your code" });

    await user.type(screen.getByLabelText("Code"), "000000");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    expect((await screen.findByRole("alert")).textContent).toContain("That code is not valid");
    // The field survives the failure — nothing was cleared, and there's still a code to fix.
    expect(screen.getByLabelText("Code")).toHaveProperty("value", "000000");

    await user.clear(screen.getByLabelText("Code"));
    await user.type(screen.getByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await waitFor(() => expect(getAccessToken()).toBe(ACCESS));
  });

  it("offers a way back to the password form from the code screen", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
    };
    renderPage();

    await signIn(user);
    await screen.findByRole("heading", { name: "Enter your code" });

    await user.click(screen.getByRole("button", { name: "Back to sign in" }));

    expect(await screen.findByRole("heading", { name: "Sign in to OpsGrids" })).toBeTruthy();
  });

  it("takes the organisation-choice hop when the code opens more than one workspace", async () => {
    const user = userEvent.setup();
    const afterMfaToken = "pre-auth-token-for-org-choice";
    routes = {
      "/api/v1/auth/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "/api/v1/auth/mfa/verify": () =>
        json({ status: "org_selection_required", pre_auth_token: afterMfaToken, organizations: [ORG_A, ORG_B] }),
      "/api/v1/auth/select-org": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderPage();

    await signIn(user);
    await screen.findByRole("heading", { name: "Enter your code" });
    await user.type(screen.getByLabelText("Code"), "123456");
    await user.click(screen.getByRole("button", { name: "Verify" }));

    await screen.findByRole("heading", { name: "Choose a workspace" });
    expect(screen.getByRole("button", { name: "Acme India" })).toBeTruthy();
    await user.click(screen.getByRole("button", { name: "Acme UK" }));

    await waitFor(() => expect(getAccessToken()).toBe(ACCESS));
    const select = calls.find((c) => c.url === "/api/v1/auth/select-org");
    // The org hop carries the token MFA minted, not the one `login` originally answered with.
    expect(select?.body).toEqual({ pre_auth_token: afterMfaToken, organization_id: ORG_B.organization_id });
  });
});
