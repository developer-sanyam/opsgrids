import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import { LifeBuoyIcon, SettingsIcon, ThemeProvider } from "@opsgrids/ui";

import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { DeliveryFallback } from "./delivery/DeliveryFallback";
import { ComingSoonPage } from "./routes/ComingSoon";
import { EditorFallback } from "./routes/EditorFallback";
import { GalleryPage } from "./routes/Gallery";
import { LiveStorePage } from "./routes/LiveStore";
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";
import { StorefrontPage } from "./routes/Storefront";

/**
 * The editor is the app's heaviest route and the only one nothing else imports — a merchant who
 * only ever looks at the gallery and the preview should not download a drag-and-drop canvas. It is
 * therefore the one split point in this app; everything else is on the critical path anyway.
 */
const EditorPage = lazy(() => import("./routes/editor"));

/**
 * The delivery admin is the app's second split point, for the same reason: a merchant editing
 * their theme should not download a CSV reader, a postal-code parser and a demand table for a
 * screen they may never open (ADR-0010 slice 1).
 */
const DeliveryPage = lazy(() => import("./delivery"));

/**
 * The routed screens under the app shell.
 *
 * `/store/*` is the buyer-facing preview, framed by the console. One component and one splat, the
 * same shape `/live/*` uses: the address after the base *is* the `PageDoc.route`, so every page in
 * the store has a preview address — the three a theme ships and the thirty-five a merchant adds.
 * It used to be three fixed entries, which meant a page a merchant had just created answered its
 * own preview address with the console's 404 while working perfectly on `/live`.
 *
 * A route the store has no page for is not a 404 here either: the renderer draws "this store has
 * no page at that address" inside the frame, which is what `/live` already does and is the true
 * answer — the address is a store address, the store just has nothing at it. The `*` catch-all
 * below still owns everything that is not a store address at all.
 */
const shellRoutes = [
  { index: true, element: <GalleryPage /> },
  {
    path: "editor",
    element: (
      <Suspense fallback={<EditorFallback />}>
        <EditorPage />
      </Suspense>
    ),
  },
  { path: "store/*", element: <StorefrontPage /> },
  {
    path: "delivery",
    element: (
      <Suspense fallback={<DeliveryFallback />}>
        <DeliveryPage />
      </Suspense>
    ),
  },
  {
    path: "settings",
    element: (
      <ComingSoonPage
        icon={<SettingsIcon className="size-6" />}
        title="Settings"
        description="Domain, brand details and publishing controls will live here. Brand name and tagline currently come from the theme you adopted."
      />
    ),
  },
  {
    path: "help",
    element: (
      <ComingSoonPage
        icon={<LifeBuoyIcon className="size-6" />}
        title="Help & Getting Started"
        description="Guides for composing a store, writing product copy and going live are on the way."
      />
    ),
  },
  { path: "*", element: <NotFoundPage /> },
];

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
        /**
         * The buyer's store, outside the shell on purpose.
         *
         * `/store` frames the same document in the console — sidebar, page header, device toggle.
         * `/live` is what a dealer would see at the store's own domain: full viewport, the store's
         * own header and footer as the only chrome, and its `maxWidth` token deciding the content
         * column. The splat carries the page: `/live`, `/live/catalogue`, `/live/product`, and any
         * route a theme adds later.
         *
         * Still behind `RequireAuth`: this is a merchant previewing their own store, and the
         * catalogue behind it is their data, which the gateway will not serve anonymously either
         * way. The chrome is what is dropped here, not the authorization.
         */
        path: "/live/*",
        element: (
          <RequireAuth>
            <LiveStorePage />
          </RequireAuth>
        ),
      },
      {
        path: "/",
        element: (
          <RequireAuth>
            <AppShell />
          </RequireAuth>
        ),
        children: shellRoutes,
      },
    ],
  },
]);

// Name this app's session slot before anything can read a token, so two OpsGrids apps open
// in one browser never share a session.
configureSession("opsgrids.storefront.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.storefront.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
