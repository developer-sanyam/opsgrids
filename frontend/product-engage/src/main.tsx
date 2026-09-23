import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import { ThemeProvider } from "@opsgrids/ui";

import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { ConnectionPage } from "./routes/Connection";
import { ContactsPage } from "./routes/Contacts";
import { DomainsPage } from "./routes/Domains";
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";
import { OverviewPage } from "./routes/Overview";
import { SendersPage } from "./routes/Senders";
import { SuppressionsPage } from "./routes/Suppressions";
import { WhatsAppConnectionPage } from "./routes/WhatsAppConnection";
import { WhatsAppTemplatesPage } from "./routes/WhatsAppTemplates";

/**
 * Eight screens: the six sending-identity screens Phase 1 shipped with, plus the audience —
 * contacts and suppressions. There is no "coming soon" route for campaigns or reporting: the
 * sidebar does not offer them, so nothing routes to them, and a URL somebody guesses lands on the
 * 404 that tells the truth rather than on a panel that promises.
 *
 * `/connection` is singular because the resource is: one organization gets one connection, and the
 * service refuses a second. The navigation says "Connections" — the customer is looking for where
 * connections live — while the address says what is actually there.
 */
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
          { path: "connection", element: <ConnectionPage /> },
          { path: "domains", element: <DomainsPage /> },
          { path: "senders", element: <SendersPage /> },
          { path: "contacts", element: <ContactsPage /> },
          { path: "suppressions", element: <SuppressionsPage /> },
          { path: "whatsapp/connection", element: <WhatsAppConnectionPage /> },
          { path: "whatsapp/templates", element: <WhatsAppTemplatesPage /> },
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

// Name this app's session slot before anything can read a token, so two OpsGrids apps open
// in one browser never share a session.
configureSession("opsgrids.engage.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.engage.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
