import { lazy, StrictMode, Suspense } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { RedirectToCentralLogin, configureAuthOrigin, configureSession } from "@opsgrids/client";
import { ThemeProvider, Toaster } from "@opsgrids/ui";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { InvoicesPage } from "./routes/Invoices";
import { InvoiceDetailPage } from "./routes/InvoiceDetail";
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
 * Overview, Invoices and an invoice's own detail are where a session spends most of its time, so
 * they stay on the critical path along with Login. Everything else — the multi-line issue form,
 * the compliance registers, purchases, GST returns and settings — is real weight a person who
 * only ever checks today's invoices should never download; each gets its own chunk, loaded once
 * its route is visited.
 */
const InvoiceNewPage = lazy(() =>
  import("./routes/InvoiceNew").then((m) => ({ default: m.InvoiceNewPage })),
);
const ImsPage = lazy(() => import("./routes/Ims").then((m) => ({ default: m.ImsPage })));
const ImsDetailPage = lazy(() =>
  import("./routes/ImsDetail").then((m) => ({ default: m.ImsDetailPage })),
);
const PaymentsPage = lazy(() =>
  import("./routes/Payments").then((m) => ({ default: m.PaymentsPage })),
);
const LedgerPage = lazy(() => import("./routes/Ledger").then((m) => ({ default: m.LedgerPage })));
const CustomersPage = lazy(() =>
  import("./routes/Customers").then((m) => ({ default: m.CustomersPage })),
);
const PurchasesPage = lazy(() =>
  import("./routes/Purchases").then((m) => ({ default: m.PurchasesPage })),
);
const GstReturnsPage = lazy(() =>
  import("./routes/GstReturns").then((m) => ({ default: m.GstReturnsPage })),
);
const GstReturnDetailPage = lazy(() =>
  import("./routes/GstReturnDetail").then((m) => ({ default: m.GstReturnDetailPage })),
);
const EwayBillsPage = lazy(() =>
  import("./routes/EwayBills").then((m) => ({ default: m.EwayBillsPage })),
);
const WithholdingPage = lazy(() =>
  import("./routes/Withholding").then((m) => ({ default: m.WithholdingPage })),
);
const SettingsPage = lazy(() =>
  import("./routes/Settings").then((m) => ({ default: m.SettingsPage })),
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
          <RedirectToCentralLogin
            authOrigin={AUTH_ORIGIN}
            logo={<LogoMark className="size-11" />}
            appName="OpsGrids Finance"
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
          { path: "invoices", element: <InvoicesPage /> },
          lazyRoute("invoices/new", <InvoiceNewPage />),
          { path: "invoices/:id", element: <InvoiceDetailPage /> },
          lazyRoute("payments", <PaymentsPage />),
          lazyRoute("compliance/ims", <ImsPage />),
          lazyRoute("compliance/ims/:id", <ImsDetailPage />),
          lazyRoute("compliance/returns", <GstReturnsPage />),
          lazyRoute("compliance/returns/:id", <GstReturnDetailPage />),
          lazyRoute("compliance/eway", <EwayBillsPage />),
          lazyRoute("compliance/withholding", <WithholdingPage />),
          lazyRoute("ledger", <LedgerPage />),
          lazyRoute("customers", <CustomersPage />),
          lazyRoute("purchases", <PurchasesPage />),
          lazyRoute("settings", <SettingsPage />),
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
configureSession("opsgrids.finance.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.finance.theme">
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
