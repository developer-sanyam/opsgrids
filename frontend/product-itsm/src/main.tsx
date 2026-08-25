import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@atmora/client";
import {
  BellIcon,
  FileTextIcon,
  KeyIcon,
  LifeBuoyIcon,
  PlugIcon,
  RefreshIcon,
  SettingsIcon,
  ThemeProvider,
  UserPlusIcon,
} from "@atmora/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { IncidentsPage } from "./routes/Incidents";
import { IncidentDetailPage } from "./routes/IncidentDetail";
import { RequestsPage } from "./routes/Requests";
import { AssetsPage } from "./routes/Assets";
import { AssetDetailPage } from "./routes/AssetDetail";
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
          { path: "incidents", element: <IncidentsPage /> },
          { path: "incidents/:id", element: <IncidentDetailPage /> },
          { path: "requests", element: <RequestsPage /> },
          // The catalog lives on the requests screen; the nav item lands there too.
          { path: "catalog", element: <RequestsPage /> },
          { path: "assets", element: <AssetsPage /> },
          { path: "assets/:tag", element: <AssetDetailPage /> },
          {
            path: "notifications",
            element: (
              <ComingSoonPage
                icon={<BellIcon className="size-6" />}
                title="Notifications"
                description="Alerts for SLA breaches, approvals and new P1 incidents will collect here in the next slice."
              />
            ),
          },
          {
            path: "changes",
            element: (
              <ComingSoonPage
                icon={<RefreshIcon className="size-6" />}
                title="Change management"
                description="Change requests, risk assessment and the maintenance-window calendar are on the roadmap."
              />
            ),
          },
          {
            path: "licenses",
            element: (
              <ComingSoonPage
                icon={<KeyIcon className="size-6" />}
                title="Software licenses"
                description="Seat pools, renewals and reclaim of unused licenses. The CMDB already tracks license assets today."
              />
            ),
          },
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<FileTextIcon className="size-6" />}
                title="Reports"
                description="Exportable SLA, backlog and asset-lifecycle reports are on the way."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Identity, monitoring and endpoint-management connectors will plug in here."
              />
            ),
          },
          {
            path: "team",
            element: (
              <ComingSoonPage
                icon={<UserPlusIcon className="size-6" />}
                title="Team Members"
                description="Invite and manage the agents who staff this service desk."
              />
            ),
          },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Workspace preferences, SLA policies and approval matrices will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides and support for running your service desk are on the way."
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

// Name this app's session slot before anything can read a token, so two Atmora apps open
// in one browser never share a session.
configureSession("atmora.itsm.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="atmora.itsm.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
