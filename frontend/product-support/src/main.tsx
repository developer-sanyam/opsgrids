import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@atmora/client";
import {
  BuildingUserIcon,
  FileTextIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  TagIcon,
  ThemeProvider,
  UserPlusIcon,
} from "@atmora/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { QueuePage } from "./routes/Queue";
import { TicketDetailPage } from "./routes/TicketDetail";
import { AgentsPage } from "./routes/Agents";
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
          { path: "queue", element: <QueuePage /> },
          { path: "queue/:id", element: <TicketDetailPage /> },
          { path: "agents", element: <AgentsPage /> },
          {
            path: "customers",
            element: (
              <ComingSoonPage
                icon={<BuildingUserIcon className="size-6" />}
                title="Customers"
                description="Company profiles, contacts and their whole ticket history will live here. The queue already groups tickets by requester and company."
              />
            ),
          },
          {
            path: "topics",
            element: (
              <ComingSoonPage
                icon={<TagIcon className="size-6" />}
                title="Topics & Tags"
                description="Curate the tags that route and report on tickets. Every ticket already carries tags you can filter by in the queue."
              />
            ),
          },
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<FileTextIcon className="size-6" />}
                title="Reports"
                description="Exportable SLA-attainment, volume and CSAT reports are on the roadmap. The overview already trends the headline numbers."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Email, chat, telephony and status-page connectors will plug in here to feed the one queue."
              />
            ),
          },
          {
            path: "team",
            element: (
              <ComingSoonPage
                icon={<UserPlusIcon className="size-6" />}
                title="Team Members"
                description="Invite agents, set teams and manage who works the queue. Load and CSAT per agent already show under SLA & Agents."
              />
            ),
          },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Business hours, SLA policies and routing rules will be editable here. The active policy is summarised under SLA & Agents."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides and support for running your support desk are on the way."
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
configureSession("atmora.support.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="atmora.support.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
