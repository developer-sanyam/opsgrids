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
import { IncidentsPage } from "./routes/Incidents";
import { IncidentDetailPage } from "./routes/IncidentDetail";
import { RequestsPage } from "./routes/Requests";
import { RequestDetailPage } from "./routes/RequestDetail";
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
          { path: "requests/:id", element: <RequestDetailPage /> },
          { path: "assets", element: <AssetsPage /> },
          { path: "assets/:tag", element: <AssetDetailPage /> },
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
configureSession("opsgrids.itsm.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.itsm.theme">
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
