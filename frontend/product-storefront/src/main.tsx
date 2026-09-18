import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import { LifeBuoyIcon, SettingsIcon, ThemeProvider } from "@opsgrids/ui";

import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { ComingSoonPage } from "./routes/ComingSoon";
import { EditorFallback } from "./routes/EditorFallback";
import { GalleryPage } from "./routes/Gallery";
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
 * The routed screens under the app shell.
 *
 * `/store/*` is the buyer-facing preview: one component, one `route` prop per store page, so a new
 * page in a theme is a new entry here and nothing else. The `*` catch-all stays last.
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
  {
    path: "store",
    element: (
      <StorefrontPage
        route="/"
        title="Your storefront"
        description="The home page a dealer lands on, drawn from your store document. Switch the preview width to see how the 12-column grid stacks."
      />
    ),
  },
  {
    path: "store/catalogue",
    element: (
      <StorefrontPage
        route="/catalogue"
        title="Catalogue page"
        description="Every line you carry, composed from the same section library as the home page."
      />
    ),
  },
  {
    path: "store/product",
    element: (
      <StorefrontPage
        route="/product"
        title="Line detail page"
        description="One catalogue line, with its specification, related prices and the enquiry form."
      />
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
