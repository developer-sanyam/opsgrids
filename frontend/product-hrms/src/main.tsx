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
import { ClipboardCheckIcon, WalletIcon } from "./ui/icons";

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
            path: "leave",
            element: (
              <ComingSoonPage
                icon={<ClipboardCheckIcon className="size-6" />}
                title="Leave"
                description="Balances, requests and the approval flow land in the next slice. Attendance already tracks who's out today."
              />
            ),
          },
          {
            path: "payroll",
            element: (
              <ComingSoonPage
                icon={<WalletIcon className="size-6" />}
                title="Payroll"
                description="Runs, payslips and the salary ledger are on the roadmap. Money will be integer minor units, append-only — no floats."
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
