import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, configureAuthOrigin, configureSession } from "@opsgrids/client";
import { ThemeProvider, Toaster } from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { LeadsPage } from "./routes/Leads";
import { LeadDetailPage } from "./routes/LeadDetail";
import { DealsPage } from "./routes/Deals";
import { DealDetailPage } from "./routes/DealDetail";
import { CompaniesPage } from "./routes/Companies";
import { CompanyDetailPage } from "./routes/CompanyDetail";
import { ContactsPage } from "./routes/Contacts";
import { ActivitiesPage } from "./routes/Activities";
import { TeamPage } from "./routes/Team";
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";
import { LogoMark } from "./ui/LogoMark";

// ADR-0003 addendum: sign-in only ever happens on the dashboard, the one host the refresh
// cookie lives on. Set in production builds only (`publish-web.sh`); unset in dev, where this
// app's own `/login` form still works the same as it always has, same-origin.
const AUTH_ORIGIN = import.meta.env.VITE_AUTH_ORIGIN as string | undefined;
if (AUTH_ORIGIN) configureAuthOrigin(AUTH_ORIGIN);

const router = createBrowserRouter([
  {
    // The auth provider sits inside the router so it can use navigation and the location.
    element: (
      <AuthProvider>
        <Outlet />
      </AuthProvider>
    ),
    children: [
      {
        path: "/login",
        element: AUTH_ORIGIN ? (
          <RedirectToCentralLogin
            authOrigin={AUTH_ORIGIN}
            logo={<LogoMark className="size-11" />}
            appName="OpsGrids Revenue"
          />
        ) : (
          <LoginPage />
        ),
      },
      {
        path: "/",
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: [
          { index: true, element: <OverviewPage /> },
          { path: "leads", element: <LeadsPage /> },
          { path: "leads/:id", element: <LeadDetailPage /> },
          { path: "deals", element: <DealsPage /> },
          { path: "deals/:id", element: <DealDetailPage /> },
          { path: "companies", element: <CompaniesPage /> },
          { path: "companies/:id", element: <CompanyDetailPage /> },
          { path: "contacts", element: <ContactsPage /> },
          { path: "activities", element: <ActivitiesPage /> },
          { path: "team", element: <TeamPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

// Name this app's session slot before anything can read a token, so two OpsGrids apps open
// in one browser never share a session.
configureSession("opsgrids.crm.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.crm.theme">
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
