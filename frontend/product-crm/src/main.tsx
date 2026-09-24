import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, configureAuthOrigin, configureSession } from "@opsgrids/client";
import { FileTextIcon, LifeBuoyIcon, PlugIcon, SettingsIcon, ThemeProvider, Toaster } from "@opsgrids/ui";
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
import { ComingSoonPage } from "./routes/ComingSoon";
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
          <RedirectToCentralLogin authOrigin={AUTH_ORIGIN} logo={<LogoMark className="size-11" />} />
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
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<FileTextIcon className="size-6" />}
                title="Reports"
                description="Exportable pipeline, forecast and win/loss reports are on the roadmap. The Overview already breaks pipeline down by stage."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Email, calendar and telephony connectors will plug in here to log activity automatically."
              />
            ),
          },
          { path: "team", element: <TeamPage /> },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Pipeline stages, deal fields and workspace preferences will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides and support for setting up your revenue workspace are on the way."
              />
            ),
          },
          {
            path: "*",
            element: (
              <ComingSoonPage
                eyebrow="Not found"
                title="That page doesn't exist"
                description="Check the address, or head back to the overview."
              />
            ),
          },
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
