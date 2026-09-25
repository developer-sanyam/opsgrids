import { lazy, StrictMode, Suspense } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession, RequirePermission } from "@opsgrids/client";
import { Skeleton, ThemeProvider, Toaster } from "@opsgrids/ui";

import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { AcceptInvitationPage } from "./routes/AcceptInvitation";
import { ForgotPasswordPage } from "./routes/ForgotPassword";
import { HelpPage } from "./routes/Help";
import { HomePage } from "./routes/Home";
import { InvoicesPage } from "./routes/Invoices";
import { LoginPage } from "./routes/Login";
import { MarketplacePage } from "./routes/Marketplace";
import { NotFoundPage } from "./routes/NotFound";
import { ResetPasswordPage } from "./routes/ResetPassword";
import { SignupPage } from "./routes/Signup";
import { SubscriptionsPage } from "./routes/Subscriptions";

// The administration surface and the security page are lazy: a customer who only ever opens the
// marketplace never pays for the members table, the permission picker or the QR renderer in
// their first-load bundle. `@/ui`'s own barrel is left eager — everything below still uses it.
const SecurityPage = lazy(() => import("./routes/account/Security").then((m) => ({ default: m.SecurityPage })));
const MembersPage = lazy(() => import("./routes/admin/Members").then((m) => ({ default: m.MembersPage })));
const RolesPage = lazy(() => import("./routes/admin/Roles").then((m) => ({ default: m.RolesPage })));
const OrganizationPage = lazy(() => import("./routes/admin/Organization").then((m) => ({ default: m.OrganizationPage })));
const OrgUnitsPage = lazy(() => import("./routes/admin/OrgUnits").then((m) => ({ default: m.OrgUnitsPage })));
const LocationsPage = lazy(() => import("./routes/admin/Locations").then((m) => ({ default: m.LocationsPage })));
const DomainsPage = lazy(() => import("./routes/admin/Domains").then((m) => ({ default: m.DomainsPage })));

function lazyFallback() {
  return <Skeleton className="h-80 w-full rounded-card" />;
}

/**
 * A permission-gated, lazily-loaded admin route. Hides nothing that isn't also refused
 * server-side (ADR-0003 §3) — the fallback exists so a customer who follows a stale link or a
 * bookmark past a permission they lost sees an explanation instead of a blank screen.
 */
function admin(perm: string, element: ReactNode) {
  return (
    <RequirePermission
      perm={perm}
      scope="OWN"
      fallback={
        <div className="flex flex-col gap-2 py-12 text-center">
          <p className="text-base font-bold">You don't have access to this page</p>
          <p className="text-sm text-muted">Ask your organisation's owner for access, or check your connection and reload.</p>
        </div>
      }
    >
      <Suspense fallback={lazyFallback()}>{element}</Suspense>
    </RequirePermission>
  );
}

const router = createBrowserRouter([
  {
    // The auth provider sits inside the router so it can use navigation and the location.
    element: (
      <AuthProvider>
        <Outlet />
      </AuthProvider>
    ),
    children: [
      // The doors a person can use with no session (ADR-0013 §3).
      { path: "/signup", element: <SignupPage /> },
      { path: "/login", element: <LoginPage /> },
      { path: "/forgot-password", element: <ForgotPasswordPage /> },
      { path: "/reset-password", element: <ResetPasswordPage /> },
      { path: "/accept-invitation", element: <AcceptInvitationPage /> },
      {
        path: "/",
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          // Home is the Owner/Executive dashboard (dashboard audit §5.4). The Marketplace it
          // replaced here moved to `/products`; the sidebar's "Products" entry and the search
          // box's `to` both still point at `/`, which is shell/layout territory owned by
          // `ui/p3-shell` — they need to move to `/products` when that branch lands.
          { index: true, element: <HomePage /> },
          { path: "products", element: <MarketplacePage /> },
          { path: "subscriptions", element: <SubscriptionsPage /> },
          { path: "invoices", element: <InvoicesPage /> },
          { path: "help", element: <HelpPage /> },
          { path: "account/security", element: <Suspense fallback={lazyFallback()}><SecurityPage /></Suspense> },
          { path: "admin/members", element: admin("core.identity.membership.read", <MembersPage />) },
          { path: "admin/roles", element: admin("core.identity.role.read", <RolesPage />) },
          { path: "admin/organization", element: admin("core.org.organization.read", <OrganizationPage />) },
          { path: "admin/org-units", element: admin("core.org.unit.read", <OrgUnitsPage />) },
          { path: "admin/locations", element: admin("core.org.location.read", <LocationsPage />) },
          { path: "admin/domains", element: admin("core.identity.domain.read", <DomainsPage />) },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

// Name this app's session slot before anything can read a token, so two OpsGrids apps open
// in one browser never share a session.
configureSession("opsgrids.dashboard.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.dashboard.theme">
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
