import { lazy, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, RequireAuth, configureAuthOrigin, configureSession } from "@opsgrids/client";
import { BuildingIcon, MapPinIcon, ThemeProvider, UserPlusIcon, lazyRoute } from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { DashboardPage } from "./routes/Dashboard";
import { LoginPage } from "./routes/Login";
import { LinkOutPage } from "./routes/LinkOut";
import { NotFoundPage } from "./routes/NotFound";
import { LogoMark } from "./ui/LogoMark";

// The dashboard (the landing screen after sign-in) and login stay on the critical path.
// Everything else — the directory, attendance, shifts, leave and the whole approvals group —
// is real weight a session that only ever checks today's dashboard should never download, so
// each feature group gets its own chunk, loaded once its route is visited.
const EmployeesPage = lazy(() =>
  import("./routes/Employees").then((m) => ({ default: m.EmployeesPage })),
);
const EmployeeDetailPage = lazy(() =>
  import("./routes/EmployeeDetail").then((m) => ({ default: m.EmployeeDetailPage })),
);
const AttendancePage = lazy(() =>
  import("./routes/Attendance").then((m) => ({ default: m.AttendancePage })),
);
const ShiftsPage = lazy(() => import("./routes/Shifts").then((m) => ({ default: m.ShiftsPage })));
const LeavePage = lazy(() => import("./routes/Leave").then((m) => ({ default: m.LeavePage })));
const LeaveTypesPage = lazy(() =>
  import("./routes/LeaveTypes").then((m) => ({ default: m.LeaveTypesPage })),
);
const ApprovalsPage = lazy(() =>
  import("./routes/Approvals").then((m) => ({ default: m.ApprovalsPage })),
);
const RequestDetailPage = lazy(() =>
  import("./routes/RequestDetail").then((m) => ({ default: m.RequestDetailPage })),
);
const MyRequestsPage = lazy(() =>
  import("./routes/MyRequests").then((m) => ({ default: m.MyRequestsPage })),
);
const DelegationsPage = lazy(() =>
  import("./routes/Delegations").then((m) => ({ default: m.DelegationsPage })),
);
const ApprovalSettingsPage = lazy(() =>
  import("./routes/ApprovalSettings").then((m) => ({ default: m.ApprovalSettingsPage })),
);


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
            appName="OpsGrids People"
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
          { index: true, element: <DashboardPage /> },
          lazyRoute("employees", <EmployeesPage />),
          lazyRoute("employees/:employeeId", <EmployeeDetailPage />),
          lazyRoute("attendance", <AttendancePage />),
          lazyRoute("shifts", <ShiftsPage />),
          lazyRoute("leave", <LeavePage />),
          lazyRoute("leave/types", <LeaveTypesPage />),
          lazyRoute("approvals", <ApprovalsPage />),
          lazyRoute("approvals/mine", <MyRequestsPage />),
          lazyRoute("approvals/requests/:requestId", <RequestDetailPage />),
          lazyRoute("approvals/delegations", <DelegationsPage />),
          lazyRoute("approvals/settings", <ApprovalSettingsPage />),
          // Departments, Locations and Team Members are administered in the admin console
          // (`opsgrids-admin`) — these are honest link-outs, not a rebuild of that CRUD inside
          // this app (`frontend/CLAUDE.md`: no forking). Every other placeholder the audit found
          // (Notifications, Tasks, Reports, Integrations, Settings, Help) had no backend and no
          // link-out target, so it is gone from both the nav and the router.
          {
            path: "departments",
            element: (
              <LinkOutPage
                icon={<BuildingIcon className="size-6" />}
                eyebrow="Records"
                title="Departments"
                description="Org units, headcount and team leads are administered in the OpsGrids admin console. The dashboard here still breaks headcount down by department."
                href="https://app.opsgrids.com/admin/org-units"
                linkLabel="Open in admin console"
              />
            ),
          },
          {
            path: "locations",
            element: (
              <LinkOutPage
                icon={<MapPinIcon className="size-6" />}
                eyebrow="Records"
                title="Locations"
                description="Sites, regions and geo-fences are administered in the OpsGrids admin console. Attendance and shifts here already reference their location codes."
                href="https://app.opsgrids.com/admin/locations"
                linkLabel="Open in admin console"
              />
            ),
          },
          {
            path: "team",
            element: (
              <LinkOutPage
                icon={<UserPlusIcon className="size-6" />}
                eyebrow="Workspace"
                title="Team Members"
                description="Invite and manage the people who administer this HR workspace from the OpsGrids admin console."
                href="https://app.opsgrids.com/admin/members"
                linkLabel="Open in admin console"
              />
            ),
          },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

// Name this app's session slot before anything can read a token, so two OpsGrids apps open
// in one browser never share a session.
configureSession("opsgrids.hrms.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.hrms.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
