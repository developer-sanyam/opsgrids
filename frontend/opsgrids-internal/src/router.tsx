import { lazy, Suspense } from "react";
import type { ReactNode } from "react";
import { Outlet } from "react-router-dom";
import type { RouteObject } from "react-router-dom";

import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireOperator } from "./auth/RequireOperator";
import { AccountsPage } from "./routes/Accounts";
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";
import { OverviewPage } from "./routes/Overview";
import { RouteFallback } from "./routes/RouteFallback";
import { RequireCapability } from "./ui/PermissionDenied";
import { RouteError } from "./ui/RouteError";
import type { Capability } from "./auth/roles";

/**
 * Sign-in, the overview and the accounts list are where a session starts, so they stay on the
 * critical path. The rest — an account's own page with its override dialogs, subscriptions, the
 * catalog, the credit ledger and the audit log — is real weight a session that only checks the
 * overview should never download; each gets its own chunk, loaded when its route is first visited.
 */
const AccountDetailPage = lazy(() =>
  import("./routes/AccountDetail").then((m) => ({ default: m.AccountDetailPage })),
);
const SubscriptionsPage = lazy(() =>
  import("./routes/Subscriptions").then((m) => ({ default: m.SubscriptionsPage })),
);
const PlansPage = lazy(() => import("./routes/Plans").then((m) => ({ default: m.PlansPage })));
const GrantsPage = lazy(() => import("./routes/Grants").then((m) => ({ default: m.GrantsPage })));
const AuditPage = lazy(() => import("./routes/Audit").then((m) => ({ default: m.AuditPage })));

/**
 * A screen behind a role. The guard hides UI and says why; every endpoint behind the screen
 * refuses the call on its own, and a 403 that still arrives is rendered where the read failed.
 */
function guarded(capability: Capability, what: string, element: ReactNode): ReactNode {
  return (
    <RequireCapability capability={capability} what={what}>
      <Suspense fallback={<RouteFallback />}>{element}</Suspense>
    </RequireCapability>
  );
}

/**
 * The console's routes, apart from the browser so a test can mount the same tree in memory. The
 * auth provider sits inside the router so it can use navigation and the location.
 */
export const routes: RouteObject[] = [
  {
    element: (
      <AuthProvider>
        <Outlet />
      </AuthProvider>
    ),
    children: [
      { path: "/login", element: <LoginPage /> },
      {
        path: "/",
        element: (
          <RequireOperator>
            <AppShell />
          </RequireOperator>
        ),
        children: [
          {
            // A screen that throws while rendering lands here, inside the shell, so the navigation
            // is still there to leave by.
            errorElement: <RouteError />,
            children: [
              { index: true, element: <OverviewPage /> },
              { path: "accounts", element: <AccountsPage /> },
              {
                path: "accounts/:orgId",
                element: guarded("organizations", "account details", <AccountDetailPage />),
              },
              {
                path: "subscriptions",
                element: guarded("subscriptions", "subscriptions", <SubscriptionsPage />),
              },
              { path: "plans", element: guarded("catalog", "plans and pricing", <PlansPage />) },
              {
                path: "grants",
                element: guarded("creditLedger", "grants and credits", <GrantsPage />),
              },
              { path: "audit", element: guarded("audit", "the audit log", <AuditPage />) },
              { path: "*", element: <NotFoundPage /> },
            ],
          },
        ],
      },
    ],
  },
];
