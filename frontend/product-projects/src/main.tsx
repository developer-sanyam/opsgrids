import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, RequireAuth, configureAuthOrigin, configureSession } from "@opsgrids/client";
import { NotFoundScreen, ThemeProvider } from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { OverviewPage } from "./routes/Overview";
import { PortfolioPage } from "./routes/Portfolio";
import { TimelinePage } from "./routes/Timeline";
import { BoardPage } from "./routes/Board";
import { MilestonesPage } from "./routes/Milestones";
import { TeamPage } from "./routes/Team";
import { LoginPage } from "./routes/Login";
import { LogoMark } from "./ui/LogoMark";

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
            appName="OpsGrids Projects"
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
          { index: true, element: <OverviewPage /> },
          { path: "timeline", element: <TimelinePage /> },
          { path: "board", element: <BoardPage /> },
          { path: "portfolio", element: <PortfolioPage /> },
          { path: "milestones", element: <MilestonesPage /> },
          { path: "team", element: <TeamPage /> },
          { path: "*", element: <NotFoundScreen /> },
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
