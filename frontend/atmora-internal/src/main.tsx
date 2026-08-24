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

const router = createBrowserRouter([
  {
    // The auth provider sits inside the router so it can use navigation and the location.
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
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          { index: true, element: <OverviewPage /> },
          { path: "accounts", element: <AccountsPage /> },
          { path: "accounts/:orgId", element: <AccountDetailPage /> },
          { path: "subscriptions", element: <SubscriptionsPage /> },
          { path: "plans", element: <PlansPage /> },
          { path: "grants", element: <GrantsPage /> },
          { path: "audit", element: <AuditPage /> },
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
