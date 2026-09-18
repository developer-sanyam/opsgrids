import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import {
  FileTextIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  SparkIcon,
  ThemeProvider,
} from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { CampaignsPage } from "./routes/Campaigns";
import { CampaignDetailPage } from "./routes/CampaignDetail";
import { AudiencesPage } from "./routes/Audiences";
import { ChannelsPage } from "./routes/Channels";
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
          { path: "campaigns", element: <CampaignsPage /> },
          { path: "campaigns/:id", element: <CampaignDetailPage /> },
          { path: "audiences", element: <AudiencesPage /> },
          { path: "channels", element: <ChannelsPage /> },
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<FileTextIcon className="size-6" />}
                title="Reports"
                description="Exportable revenue-attribution, cohort and channel reports are on the roadmap. The Overview already rolls up the headline numbers."
              />
            ),
          },
          {
            path: "automations",
            element: (
              <ComingSoonPage
                icon={<SparkIcon className="size-6" />}
                title="Automations"
                description="Trigger-based journeys — welcome sequences, re-engagement, budget guardrails — will be built and monitored here."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Ad platforms, your CRM and the web analytics that close the revenue loop will connect here."
              />
            ),
          },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Workspace preferences, attribution windows and team roles will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides for launching your first revenue-attributed campaign are on the way."
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
configureSession("opsgrids.marketing.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.marketing.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
