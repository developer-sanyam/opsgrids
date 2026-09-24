import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { LoginPage } from "./routes/Login";
import { OverviewPage } from "./routes/Overview";
import { AccountsPage } from "./routes/Accounts";
import { AccountDetailPage } from "./routes/AccountDetail";
import { SubscriptionsPage } from "./routes/Subscriptions";
import { PlansPage } from "./routes/Plans";
import { GrantsPage } from "./routes/Grants";
import { AuditPage } from "./routes/Audit";
import { NotFoundPage } from "./routes/NotFound";
import { RouteErrorCard } from "./routes/RouteError";

const router = createBrowserRouter([
  {
    // The auth provider sits inside the router so it can use navigation and the location.
    element: (
      <AuthProvider>
        <Outlet />
      </AuthProvider>
    ),
    // Catches anything thrown above the shell (auth wiring, the router itself) with a full-page
    // card rather than React Router's own blank "Unexpected Application Error!".
    errorElement: <RouteErrorCard />,
    children: [
      { path: "/login", element: <LoginPage />, errorElement: <RouteErrorCard /> },
      {
        path: "/",
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        errorElement: <RouteErrorCard />,
        children: [
          { index: true, element: <OverviewPage />, errorElement: <RouteErrorCard /> },
          { path: "accounts", element: <AccountsPage />, errorElement: <RouteErrorCard /> },
          { path: "accounts/:orgId", element: <AccountDetailPage />, errorElement: <RouteErrorCard /> },
          { path: "subscriptions", element: <SubscriptionsPage />, errorElement: <RouteErrorCard /> },
          { path: "plans", element: <PlansPage />, errorElement: <RouteErrorCard /> },
          { path: "grants", element: <GrantsPage />, errorElement: <RouteErrorCard /> },
          { path: "audit", element: <AuditPage />, errorElement: <RouteErrorCard /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
