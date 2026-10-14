import { Navigate } from "react-router-dom";

import { DEMO_PASSWORD, useProductLogin } from "@opsgrids/client";
import { ProductLoginScreen } from "@opsgrids/ui";

import { LogoMark } from "@/ui/LogoMark";

/** Dev-build only: Vite replaces `import.meta.env.DEV` with a literal, so production bundles carry
 *  neither the demo identity nor the button that fills it. */
const DEMO_EMAIL = import.meta.env.DEV ? "admin@opsgrids-demo.example.com" : null;

/**
 * The dev sign-in screen. In production `/login` hands off to the dashboard (`RedirectToCentralLogin`
 * in `main.tsx`); the state lives in `useProductLogin` and the screen in `ProductLoginScreen`.
 */
export function LoginPage() {
  const login = useProductLogin();

  // Already signed in (e.g. hit /login directly) — go where they were headed.
  if (login.signedIn) return <Navigate to={login.destination} replace />;

  return (
    <ProductLoginScreen
      brand="OpsGrids Support"
      logo={<LogoMark className="size-9" />}
      tagline="One queue for customers and staff, with SLAs that mean something."
      step={login.step}
      organizations={login.organizations}
      email={login.email}
      onEmailChange={login.setEmail}
      password={login.password}
      onPasswordChange={login.setPassword}
      submitting={login.submitting}
      error={login.error}
      onSubmit={login.submit}
      onPickOrganization={login.pickOrganization}
      demo={
        DEMO_EMAIL === null
          ? undefined
          : { email: DEMO_EMAIL, onFill: () => login.fill(DEMO_EMAIL, DEMO_PASSWORD) }
      }
    />
  );
}
