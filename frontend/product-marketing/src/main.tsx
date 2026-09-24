import { lazy, StrictMode, Suspense } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, configureAuthOrigin, configureSession } from "@opsgrids/client";
import {
  FileTextIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  SparkIcon,
  ThemeProvider,
  Toaster,
} from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { LoginPage } from "./routes/Login";
import { ComingSoonPage } from "./routes/ComingSoon";
import { RouteFallback } from "./routes/RouteFallback";
import { LogoMark } from "./ui/LogoMark";

// ADR-0003 addendum: sign-in only ever happens on the dashboard, the one host the refresh
// cookie lives on. Set in production builds only (`publish-web.sh`); unset in dev, where this
// app's own `/login` form still works the same as it always has, same-origin.
const AUTH_ORIGIN = import.meta.env.VITE_AUTH_ORIGIN as string | undefined;
if (AUTH_ORIGIN) configureAuthOrigin(AUTH_ORIGIN);

/**
 * Everything but the landing page is a split point. Overview is where a session lands after
 * sign-in, so it (and `Login`) stay on the critical path; the campaign table, its create/edit
 * dialogs, the campaign detail chart and the audience cards are real weight a person who only
 * ever checks the Overview should never download.
 */
const CampaignsPage = lazy(() =>
  import("./routes/Campaigns").then((m) => ({ default: m.CampaignsPage })),
);
const CampaignDetailPage = lazy(() =>
  import("./routes/CampaignDetail").then((m) => ({ default: m.CampaignDetailPage })),
);
const AudiencesPage = lazy(() =>
  import("./routes/Audiences").then((m) => ({ default: m.AudiencesPage })),
);
const ChannelsPage = lazy(() =>
  import("./routes/Channels").then((m) => ({ default: m.ChannelsPage })),
);

function lazyRoute(path: string, element: ReactNode) {
  return { path, element: <Suspense fallback={<RouteFallback />}>{element}</Suspense> };
}

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
          <RedirectToCentralLogin authOrigin={AUTH_ORIGIN} logo={<LogoMark className="size-11" />} />
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
          lazyRoute("campaigns", <CampaignsPage />),
          lazyRoute("campaigns/:id", <CampaignDetailPage />),
          lazyRoute("audiences", <AudiencesPage />),
          lazyRoute("channels", <ChannelsPage />),
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<FileTextIcon className="size-6" />}
                title="Reports"
                description="Exportable revenue-attribution, cohort and channel reports are on the roadmap. The Overview already rolls up the headline numbers."
              />
            ),
          },
          {
            path: "automations",
            element: (
              <ComingSoonPage
                icon={<SparkIcon className="size-6" />}
                title="Automations"
                description="Trigger-based journeys — welcome sequences, re-engagement, budget guardrails — will be built and monitored here."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Ad platforms, your CRM and the web analytics that close the revenue loop will connect here."
              />
            ),
          },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Workspace preferences, attribution windows and team roles will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides for launching your first revenue-attributed campaign are on the way."
              />
            ),
          },
          {
            path: "*",
            element: (
              <ComingSoonPage
                eyebrow="404"
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
configureSession("opsgrids.marketing.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.marketing.theme">
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
