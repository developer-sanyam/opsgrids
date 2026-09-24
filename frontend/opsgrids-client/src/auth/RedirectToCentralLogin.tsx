import type { ReactNode } from "react";
import { useEffect } from "react";

export interface RedirectToCentralLoginProps {
  /** The dashboard's origin, no trailing slash — `https://app.opsgrids.com` in production. */
  authOrigin: string;
  /** The product's mark, shown while the browser is sent on its way. */
  logo: ReactNode;
  message?: string | undefined;
}

/**
 * A product app's own `/login` route, everywhere except the dashboard itself and the storefront
 * buyer bundle (ADR-0003 addendum): sign-in only ever happens on the dashboard, which is the
 * one host the refresh cookie lives on. Rather than rendering a form nobody should be able to
 * submit here, this sends the browser straight there, carrying this app's own current URL as
 * `?next=` — the dashboard's own login screen redirects back to it once sign-in completes,
 * after checking it against its allow-list (`isAllowedRedirectTarget`).
 *
 * A full navigation (`window.location.assign`), never `<Navigate>`: the target is a different
 * origin, which React Router's client-side router cannot and must not try to reach.
 */
export function RedirectToCentralLogin({
  authOrigin,
  logo,
  message = "Taking you to sign in…",
}: RedirectToCentralLoginProps) {
  useEffect(() => {
    const next = encodeURIComponent(window.location.href);
    window.location.assign(`${authOrigin}/login?next=${next}`);
  }, [authOrigin]);

  return (
    <div className="min-h-dvh grid place-items-center bg-ground">
      <div className="flex flex-col items-center gap-4" role="status" aria-live="polite">
        {logo}
        <span className="text-sm font-medium text-muted">{message}</span>
      </div>
    </div>
  );
}
