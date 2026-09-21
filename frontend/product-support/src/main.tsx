import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import { ThemeProvider, Toaster } from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { QueuePage } from "./routes/Queue";
import { TicketDetailPage } from "./routes/TicketDetail";
import { AgentsPage } from "./routes/Agents";
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";

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
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

// Name this app's session slot before anything can read a token, so two OpsGrids apps open
// in one browser never share a session.
configureSession("opsgrids.support.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.support.theme">
      {/* Mounted once, above the router: a toast raised by a screen must outlive the dialog
       *  (or the navigation) that raised it. */}
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
