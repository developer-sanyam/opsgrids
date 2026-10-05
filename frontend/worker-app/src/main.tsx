import { Suspense, lazy, StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, configureAuthOrigin, configureSession } from "@opsgrids/client";
import { ThemeProvider, Toaster } from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { HomePage } from "./routes/Home";
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";
import { RouteFallback } from "./routes/RouteFallback";
import { LogoMark } from "./ui/LogoMark";
import { WorkerProvider } from "./worker/WorkerProvider";

// ADR-0003 addendum: sign-in only ever happens on the dashboard, the one host the refresh
// cookie lives on. Set in production builds only (`publish-web.sh`); unset in dev, where this
// app's own `/login` form still works the same as it always has, same-origin.
const AUTH_ORIGIN = import.meta.env.VITE_AUTH_ORIGIN as string | undefined;
if (AUTH_ORIGIN) configureAuthOrigin(AUTH_ORIGIN);

/**
 * Home and Login stay on the critical path (Home is where a session lands after sign-in and is
 * the app's whole reason to exist). Everything reached by drilling into a task — detail, scan,
 * pick and pack confirmation — is a lazy split point: none of it is needed until a worker taps
 * into a task, and the barcode-scanning code in particular (`lib/useBarcodeScanner.ts`) is worth
 * keeping off the boot bundle for a phone on a warehouse's own signal. Counting (ADR-0026, plan
 * F8) is the same story — a worker never touches a count session on a normal picking shift.
 */
const ProfilePage = lazy(() => import("./routes/Profile").then((m) => ({ default: m.ProfilePage })));
const TaskDetailPage = lazy(() =>
  import("./routes/TaskDetail").then((m) => ({ default: m.TaskDetailPage })),
);
const ScanPage = lazy(() => import("./routes/Scan").then((m) => ({ default: m.ScanPage })));
const PickConfirmPage = lazy(() =>
  import("./routes/PickConfirm").then((m) => ({ default: m.PickConfirmPage })),
);
const PackPage = lazy(() => import("./routes/Pack").then((m) => ({ default: m.PackPage })));
const CountsPage = lazy(() => import("./routes/Counts").then((m) => ({ default: m.CountsPage })));
const CountSessionLinesPage = lazy(() =>
  import("./routes/CountSessionLines").then((m) => ({ default: m.CountSessionLinesPage })),
);
const CountLineEntryPage = lazy(() =>
  import("./routes/CountLineEntry").then((m) => ({ default: m.CountLineEntryPage })),
);
// Storage put-away is the same story as counting: a picker never opens it on a normal shift, and it
// is where the camera scanner, the geolocation call and the stock picker get pulled in.
const PutawayPage = lazy(() => import("./routes/Putaway").then((m) => ({ default: m.PutawayPage })));

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
          <RedirectToCentralLogin authOrigin={AUTH_ORIGIN} logo={<LogoMark className="size-11" />} appName="OpsGrids Worker" />
        ) : (
          <LoginPage />
        ),
      },
      {
        path: "/",
        element: (
          <RequireAuth>
            <WorkerProvider>
              <AppShell />
            </WorkerProvider>
          </RequireAuth>
        ),
        children: [
          { index: true, element: <HomePage /> },
          {
            path: "counts",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <CountsPage />
              </Suspense>
            ),
          },
          {
            path: "counts/:sessionId",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <CountSessionLinesPage />
              </Suspense>
            ),
          },
          {
            path: "counts/:sessionId/lines/:lineId",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <CountLineEntryPage />
              </Suspense>
            ),
          },
          {
            path: "putaway",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <PutawayPage />
              </Suspense>
            ),
          },
          {
            path: "profile",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <ProfilePage />
              </Suspense>
            ),
          },
          {
            path: "tasks/:taskId",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <TaskDetailPage />
              </Suspense>
            ),
          },
          {
            path: "tasks/:taskId/scan",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <ScanPage />
              </Suspense>
            ),
          },
          {
            path: "tasks/:taskId/lines/:lineId/pick",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <PickConfirmPage />
              </Suspense>
            ),
          },
          {
            path: "orders/:orderId/pack",
            element: (
              <Suspense fallback={<RouteFallback />}>
                <PackPage />
              </Suspense>
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
configureSession("opsgrids.worker.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.worker.theme">
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
