import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import {
  FileTextIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  ThemeProvider,
  UserPlusIcon,
} from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { DealsPage } from "./routes/Deals";
import { DealDetailPage } from "./routes/DealDetail";
import { CompaniesPage } from "./routes/Companies";
import { CompanyDetailPage } from "./routes/CompanyDetail";
import { ContactsPage } from "./routes/Contacts";
import { ActivitiesPage } from "./routes/Activities";
import { LoginPage } from "./routes/Login";
import { ComingSoonPage } from "./routes/ComingSoon";

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
          {
            path: "team",
            element: (
              <ComingSoonPage
                icon={<UserPlusIcon className="size-6" />}
                title="Team Members"
                description="Invite and manage the reps who work this pipeline, and set their territories."
              />
            ),
          },
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
                title="Not found"
                description="That page doesn't exist. Head back to the overview."
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
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
