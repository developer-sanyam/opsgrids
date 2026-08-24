import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { DashboardPage } from "./routes/Dashboard";
import { EmployeesPage } from "./routes/Employees";
import { AttendancePage } from "./routes/Attendance";
import { LoginPage } from "./routes/Login";
import { ComingSoonPage } from "./routes/ComingSoon";
import {
  BellIcon,
  BuildingIcon,
  ClipboardCheckIcon,
  FileTextIcon,
  LifeBuoyIcon,
  MapPinIcon,
  PlugIcon,
  SettingsIcon,
  UserPlusIcon,
} from "./ui/icons";

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
          { index: true, element: <DashboardPage /> },
          { path: "employees", element: <EmployeesPage /> },
          { path: "attendance", element: <AttendancePage /> },
          {
            path: "notifications",
            element: (
              <ComingSoonPage
                icon={<BellIcon className="size-6" />}
                title="Notifications"
                description="Alerts for approvals, joiners and attendance exceptions land here in the next slice."
              />
            ),
          },
          {
            path: "tasks",
            element: (
              <ComingSoonPage
                icon={<ClipboardCheckIcon className="size-6" />}
                title="Tasks"
                description="Your people-ops to-dos, assignable and trackable. Today's list already previews on the dashboard."
              />
            ),
          },
          {
            path: "departments",
            element: (
              <ComingSoonPage
                icon={<BuildingIcon className="size-6" />}
                title="Departments"
                description="Org units, headcount and team leads. The dashboard already breaks headcount down by department."
              />
            ),
          },
          {
            path: "locations",
            element: (
              <ComingSoonPage
                icon={<MapPinIcon className="size-6" />}
                title="Locations"
                description="Sites, regions and who works where. Attendance already tags field, remote and office punches."
              />
            ),
          },
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<FileTextIcon className="size-6" />}
                title="Reports"
                description="Exportable headcount, attendance and workforce reports are on the roadmap."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Payroll, identity and calendar connectors will plug in here."
              />
            ),
          },
          {
            path: "team",
            element: (
              <ComingSoonPage
                icon={<UserPlusIcon className="size-6" />}
                title="Team Members"
                description="Invite and manage the people who administer this HR workspace."
              />
            ),
          },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Workspace preferences, roles and policies will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides and support for setting up your HR workspace are on the way."
              />
            ),
          },
          {
            path: "*",
            element: (
              <ComingSoonPage
                title="Not found"
                description="That page doesn't exist. Head back to the dashboard."
              />
            ),
          },
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
