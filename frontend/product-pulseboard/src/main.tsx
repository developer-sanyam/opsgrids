import { lazy, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, RequireAuth, configureAuthOrigin, configureSession } from "@opsgrids/client";
import { ComingSoonPage, ThemeProvider, lazyRoute } from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { OverviewPage } from "./routes/Overview";
import { LoginPage } from "./routes/Login";
import { LogoMark } from "./ui/LogoMark";

// ADR-0003 addendum: sign-in only ever happens on the dashboard, the one host the refresh
// cookie lives on. Set in production builds only (`publish-web.sh`); unset in dev, where this
// app's own `/login` form still works the same as it always has, same-origin.
const AUTH_ORIGIN = import.meta.env.VITE_AUTH_ORIGIN as string | undefined;
if (AUTH_ORIGIN) configureAuthOrigin(AUTH_ORIGIN);

/**
 * Everything but the landing page is a split point.
 *
 * Overview is where a session lands after sign-in, so it (and `Login`) stay on the critical path.
 * Every other screen — the board's drag-and-drop, the weekly time grid and its entry dialog, the
 * roster and standup forms — is real weight a person who only ever checks the Overview should
 * never download. Each gets its own chunk, loaded once its route is actually visited.
 */
const ProjectsPage = lazy(() => import("./routes/Projects").then((m) => ({ default: m.ProjectsPage })));
const BoardPage = lazy(() => import("./routes/Board").then((m) => ({ default: m.BoardPage })));
const TimesheetsPage = lazy(() => import("./routes/Timesheets").then((m) => ({ default: m.TimesheetsPage })));
const StandupsPage = lazy(() => import("./routes/Standups").then((m) => ({ default: m.StandupsPage })));
const PeoplePage = lazy(() => import("./routes/People").then((m) => ({ default: m.PeoplePage })));


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
            appName="PulseBoard"
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
          lazyRoute("projects", <ProjectsPage />),
          lazyRoute("board", <BoardPage />),
          lazyRoute("timesheets", <TimesheetsPage />),
          lazyRoute("standups", <StandupsPage />),
          lazyRoute("people", <PeoplePage />),
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
configureSession("opsgrids.pulseboard.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.pulseboard.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
