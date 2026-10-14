import { useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";

import { ApiError } from "../http";
import { useAuth } from "./context";
import type { OrganizationChoice } from "./types";

/**
 * The seeded dev-demo password the standalone product apps fill in for a demo sign-in. A dev-only
 * convenience: an app reaches for it only inside an `import.meta.env.DEV` branch, so a production
 * bundle carries neither this string nor the button that uses it.
 */
export const DEMO_PASSWORD = "Demo-opsgrids-2026"; // pragma: allowlist secret — seeded dev-demo credential, not a real secret

/** Never leak which half was wrong — one message covers a bad email, bad password, or a 4xx. */
const CREDENTIAL_ERROR = "That email or password is incorrect.";

interface LocationState {
  from?: string;
}

function detailOf(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong.";
}

export interface ProductLogin {
  /** Already signed in (the person opened `/login` directly): go to `destination` instead. */
  signedIn: boolean;
  /** Where the person was headed before `RequireAuth` sent them here, or `/`. */
  destination: string;
  step: "credentials" | "organizations";
  organizations: readonly OrganizationChoice[];
  email: string;
  setEmail: (value: string) => void;
  password: string;
  setPassword: (value: string) => void;
  submitting: boolean;
  error: string | null;
  submit: () => Promise<void>;
  pickOrganization: (organizationId: string) => Promise<void>;
  /** Fill the fields with a demo identity and clear any error. */
  fill: (email: string, password: string) => void;
}

/**
 * The state behind a standalone product app's dev sign-in screen: credentials, the organization
 * picker for a person who belongs to more than one, and the errors each step can raise. The screen
 * itself is `ProductLoginScreen` in `@opsgrids/ui`; the app wires the two together.
 *
 * This is the dev-mode form only. In production a product's `/login` hands off to the dashboard,
 * the one host the refresh cookie lives on (`RedirectToCentralLogin`).
 */
export function useProductLogin(): ProductLogin {
  const { status, login, selectOrg } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();

  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [organizations, setOrganizations] = useState<readonly OrganizationChoice[] | null>(null);

  const destination = (location.state as LocationState | null)?.from ?? "/";

  async function submit() {
    setError(null);
    setSubmitting(true);
    try {
      const next = await login(email.trim(), password);
      if (next.kind === "complete") {
        navigate(destination, { replace: true });
      } else if (next.kind === "org_selection") {
        setOrganizations(next.organizations);
      } else {
        setError("This account needs a second factor, which isn't supported here yet.");
      }
    } catch (err) {
      // A 4xx is bad credentials; anything else is a real fault worth surfacing plainly.
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        setError(CREDENTIAL_ERROR);
      } else {
        setError(`Couldn't sign in. ${detailOf(err)}`);
      }
    } finally {
      setSubmitting(false);
    }
  }

  async function pickOrganization(organizationId: string) {
    setError(null);
    setSubmitting(true);
    try {
      await selectOrg(organizationId);
      navigate(destination, { replace: true });
    } catch (err) {
      setError(`Couldn't finish signing in. ${detailOf(err)}`);
      setSubmitting(false);
    }
  }

  function fill(demoEmail: string, demoPassword: string) {
    setEmail(demoEmail);
    setPassword(demoPassword);
    setError(null);
  }

  return {
    signedIn: status === "authenticated",
    destination,
    step: organizations === null ? "credentials" : "organizations",
    organizations: organizations ?? [],
    email,
    setEmail,
    password,
    setPassword,
    submitting,
    error,
    submit,
    pickOrganization,
    fill,
  };
}
