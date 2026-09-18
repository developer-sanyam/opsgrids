import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import {
  BarChartIcon,
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
import { PortfolioPage } from "./routes/Portfolio";
import { TimelinePage } from "./routes/Timeline";
import { BoardPage } from "./routes/Board";
import { MilestonesPage } from "./routes/Milestones";
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
          { path: "timeline", element: <TimelinePage /> },
          { path: "board", element: <BoardPage /> },
          { path: "portfolio", element: <PortfolioPage /> },
          { path: "milestones", element: <MilestonesPage /> },
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<BarChartIcon className="size-6" />}
                title="Reports"
                description="Exportable delivery, velocity and budget reports across the portfolio are on the roadmap."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Connectors for source control, issue trackers and calendars will plug in here."
              />
            ),
          },
          {
            path: "team",
            element: (
              <ComingSoonPage
                icon={<UserPlusIcon className="size-6" />}
                title="Team Members"
                description="Invite and manage the people who plan and deliver in this workspace."
              />
            ),
          },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Workspace preferences, workflow stages and delivery policies will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides and support for setting up your delivery workspace are on the way."
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
configureSession("opsgrids.projects.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.projects.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
