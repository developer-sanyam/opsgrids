import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@atmora/client";
import { LifeBuoyIcon, PlugIcon, SettingsIcon, ThemeProvider } from "@atmora/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { InvoicesPage } from "./routes/Invoices";
import { InvoiceDetailPage } from "./routes/InvoiceDetail";
import { PaymentsPage } from "./routes/Payments";
import { LedgerPage } from "./routes/Ledger";
import { CustomersPage } from "./routes/Customers";
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
          { path: "invoices", element: <InvoicesPage /> },
          { path: "invoices/:id", element: <InvoiceDetailPage /> },
          { path: "payments", element: <PaymentsPage /> },
          { path: "ledger", element: <LedgerPage /> },
          { path: "customers", element: <CustomersPage /> },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="Bank feeds, GST filing and payment-gateway reconciliation connectors will plug in here."
              />
            ),
          },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Invoice numbering, tax rates, payment terms and workspace roles will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides for setting up your billing workspace and closing the books are on the way."
              />
            ),
          },
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

// Name this app's session slot before anything can read a token, so two Atmora apps open
// in one browser never share a session.
configureSession("atmora.finance.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="atmora.finance.theme">
      <RouterProvider router={router} />
    </ThemeProvider>
  </StrictMode>,
);
