/**
 * Sign-in through the context, including the second factor.
 *
 * MFA is the path that was missing: `login()` answered `mfa_required` and there was nowhere for
 * the code to go, so the one app that needed it reached around the context and reloaded the page
 * to get a session. The contract pinned here is `services/auth/app/api/routes.py` — `POST
 * /auth/login`, `POST /auth/mfa/verify`, `POST /auth/select-org`, all answering the same
 * `complete | mfa_required | org_selection_required` union — plus the rule that matters more than
 * any of it: **the pre-auth token is one factor away from a session and never goes into storage.**
 */

import { useState } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { problemMessage } from "../problem";
import { getAccessToken, setAccessToken } from "../session";
import { AuthProvider } from "./AuthProvider";
import { useAuth } from "./context";
import type { OrganizationChoice } from "./types";

const PRE_AUTH = "pre-auth-token-value-do-not-store";
const ACCESS = "access-token-value";

const ORG_A: OrganizationChoice = {
  organization_id: "org-a",
  slug: "acme-india",
  name: "Acme India",
  roles: ["finance_admin"],
};
const ORG_B: OrganizationChoice = {
  organization_id: "org-b",
  slug: "acme-uk",
  name: "Acme UK",
  roles: ["finance_viewer"],
};

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

/** Every request the suite made, in order, with its parsed body. */
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
      if (!route) {
        // The provider now tries a silent refresh on boot, before any test's own stubs are
        // relevant (no token exists yet in any of these tests) — the same "no session cookie"
        // 401 a real gateway answers with. A test that cares can still override this by
        // stubbing `/api/v1/auth/refresh` itself.
        if (url === "/api/v1/auth/refresh") return Promise.resolve(json({}, 401));
        throw new Error(`No stub for ${url}`);
      }
      return Promise.resolve(route());
    }),
  );
}

/** The sign-in screen, reduced to the four moves it makes. */
function SignIn() {
  const { status, user, login, verifyMfa, selectOrg } = useAuth();
  const [step, setStep] = useState("none");
  const [error, setError] = useState("");
  const [orgs, setOrgs] = useState<OrganizationChoice[]>([]);

  async function run(next: () => Promise<{ kind: string; organizations?: OrganizationChoice[] }>) {
    setError("");
    try {
      const result = await next();
      setStep(result.kind);
      setOrgs(result.organizations ?? []);
    } catch (caught) {
      setError(problemMessage(caught));
    }
  }

  return (
    <div>
      <p data-testid="status">{status}</p>
      <p data-testid="step">{step}</p>
      <p data-testid="user">{user?.email ?? ""}</p>
      <p data-testid="error">{error}</p>
      <button type="button" onClick={() => void run(() => login("priya@acme.example.com", "pw"))}>
        Sign in
      </button>
      <button type="button" onClick={() => void run(() => verifyMfa("000000"))}>
        Wrong code
      </button>
      <button type="button" onClick={() => void run(() => verifyMfa("123456"))}>
        Right code
      </button>
      {orgs.map((org) => (
        <button
          key={org.organization_id}
          type="button"
          onClick={() =>
            void run(async () => {
              await selectOrg(org.organization_id);
              return { kind: "complete" };
            })
          }
        >
          {org.name}
        </button>
      ))}
    </div>
  );
}

function renderSignIn() {
  return render(
    <AuthProvider>
      <SignIn />
    </AuthProvider>,
  );
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
  it("still completes in one call and hydrates the session", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderSignIn();

    // The provider's own boot-time refresh attempt finds no session and settles first, so the
    // click below is the one that starts the sign-in this test is actually about.
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));

    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(screen.getByTestId("step").textContent).toBe("complete");
    expect(screen.getByTestId("user").textContent).toBe("priya@acme.example.com");
    expect(getAccessToken()).toBe(ACCESS);
    expect(calls.map((call) => call.url)).toEqual([
      "/api/v1/auth/refresh",
      "/api/v1/auth/login",
      "/api/v1/auth/me",
    ]);
  });

  it("still auto-selects the only organization a user belongs to", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () =>
        json({ status: "org_selection_required", pre_auth_token: PRE_AUTH, organizations: [ORG_A] }),
      "/api/v1/auth/select-org": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderSignIn();

    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(screen.getByTestId("step").textContent).toBe("complete");
  });
});

describe("MFA sign-in", () => {
  it("holds the pre-auth token, verifies the code, and hydrates the session", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "/api/v1/auth/mfa/verify": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderSignIn();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByTestId("step").textContent).toBe("mfa_required"));
    // Not signed in yet, and nothing has been fetched with a bearer token.
    expect(screen.getByTestId("status").textContent).toBe("unauthenticated");
    expect(getAccessToken()).toBeNull();

    await user.click(screen.getByRole("button", { name: "Right code" }));

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(screen.getByTestId("step").textContent).toBe("complete");
    expect(screen.getByTestId("user").textContent).toBe("priya@acme.example.com");
    // The screen never held the token: the provider sent the one it was keeping.
    const verify = calls.find((call) => call.url === "/api/v1/auth/mfa/verify");
    expect(verify?.body).toEqual({ pre_auth_token: PRE_AUTH, code: "123456" });
  });

  it("surfaces the server's sentence for a wrong code, and lets the next one through", async () => {
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
    renderSignIn();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByTestId("step").textContent).toBe("mfa_required"));

    await user.click(screen.getByRole("button", { name: "Wrong code" }));
    await waitFor(() =>
      expect(screen.getByTestId("error").textContent).toBe("That code is not valid"),
    );
    // A rejected code is not a lost session: still unauthenticated, still holding the token.
    expect(screen.getByTestId("status").textContent).toBe("unauthenticated");

    await user.click(screen.getByRole("button", { name: "Right code" }));
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(screen.getByTestId("error").textContent).toBe("");
  });

  it("takes the org-selection hop when the second factor opens several organizations", async () => {
    const user = userEvent.setup();
    const afterMfaToken = "pre-auth-token-for-org-choice";
    routes = {
      "/api/v1/auth/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "/api/v1/auth/mfa/verify": () =>
        json({
          status: "org_selection_required",
          pre_auth_token: afterMfaToken,
          organizations: [ORG_A, ORG_B],
        }),
      "/api/v1/auth/select-org": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderSignIn();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByTestId("step").textContent).toBe("mfa_required"));
    await user.click(screen.getByRole("button", { name: "Right code" }));
    await waitFor(() => expect(screen.getByTestId("step").textContent).toBe("org_selection"));

    await user.click(screen.getByRole("button", { name: "Acme UK" }));
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    // The org hop must carry the token MFA minted, not the one login answered with.
    const select = calls.find((call) => call.url === "/api/v1/auth/select-org");
    expect(select?.body).toEqual({
      pre_auth_token: afterMfaToken,
      organization_id: ORG_B.organization_id,
    });
  });

  it("auto-selects a single organization offered after the second factor", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "/api/v1/auth/mfa/verify": () =>
        json({ status: "org_selection_required", pre_auth_token: PRE_AUTH, organizations: [ORG_A] }),
      "/api/v1/auth/select-org": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderSignIn();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByTestId("step").textContent).toBe("mfa_required"));
    await user.click(screen.getByRole("button", { name: "Right code" }));

    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));
    expect(screen.getByTestId("step").textContent).toBe("complete");
  });

  it("never writes the pre-auth token to localStorage or sessionStorage", async () => {
    const user = userEvent.setup();
    routes = {
      "/api/v1/auth/login": () => json({ status: "mfa_required", pre_auth_token: PRE_AUTH }),
      "/api/v1/auth/mfa/verify": () => json(COMPLETE),
      "/api/v1/auth/me": () => json(ME),
    };
    renderSignIn();

    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(screen.getByTestId("step").textContent).toBe("mfa_required"));
    expect(storedValues().join("|")).not.toContain(PRE_AUTH);
    expect(localStorage.length).toBe(0);

    await user.click(screen.getByRole("button", { name: "Right code" }));
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("authenticated"));

    // The access token is mirrored to sessionStorage by design; the pre-auth token is not, and
    // localStorage holds nothing at all.
    expect(storedValues().join("|")).not.toContain(PRE_AUTH);
    expect(localStorage.length).toBe(0);
  });

  it("refuses a code when no sign-in is waiting for one", async () => {
    const user = userEvent.setup();
    routes = {};
    renderSignIn();
    await waitFor(() => expect(screen.getByTestId("status").textContent).toBe("unauthenticated"));

    await user.click(screen.getByRole("button", { name: "Right code" }));

    await waitFor(() =>
      expect(screen.getByTestId("error").textContent).toBe("Something went wrong. Try again."),
    );
    // Nothing beyond the provider's own boot-time refresh attempt — `verifyMfa` never called out.
    expect(calls.map((call) => call.url)).toEqual(["/api/v1/auth/refresh"]);
  });
});
