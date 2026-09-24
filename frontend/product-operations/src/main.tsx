import { lazy, StrictMode, Suspense } from "react";
import type { ReactNode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import { configureSession } from "@opsgrids/client";
import { ThemeProvider, Toaster } from "@opsgrids/ui";
import "@opsgrids/ui/fonts";
import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";
import { RouteFallback } from "./routes/RouteFallback";

/**
 * Everything but the landing page is a split point.
 *
 * Overview is where a session lands after sign-in, so it (and `Login`) stay on the critical path.
 * Every other screen — the purchase-order line editor and its comboboxes, the inventory and
 * supplier detail screens, the warehouse directory — is real weight a person who only ever checks
 * the Overview should never download. Each gets its own chunk, loaded once its route is visited.
 */
const PurchaseOrdersPage = lazy(() =>
  import("./routes/PurchaseOrders").then((m) => ({ default: m.PurchaseOrdersPage })),
);
const PurchaseOrderDetailPage = lazy(() =>
  import("./routes/PurchaseOrderDetail").then((m) => ({ default: m.PurchaseOrderDetailPage })),
);
const PurchaseOrderFormPage = lazy(() =>
  import("./routes/PurchaseOrderForm").then((m) => ({ default: m.PurchaseOrderFormPage })),
);
const InventoryPage = lazy(() =>
  import("./routes/Inventory").then((m) => ({ default: m.InventoryPage })),
);
const InventoryItemDetailPage = lazy(() =>
  import("./routes/InventoryItemDetail").then((m) => ({ default: m.InventoryItemDetailPage })),
);
const ItemCategoriesPage = lazy(() =>
  import("./routes/ItemCategories").then((m) => ({ default: m.ItemCategoriesPage })),
);
const SuppliersPage = lazy(() =>
  import("./routes/Suppliers").then((m) => ({ default: m.SuppliersPage })),
);
const SupplierDetailPage = lazy(() =>
  import("./routes/SupplierDetail").then((m) => ({ default: m.SupplierDetailPage })),
);
const WarehousesPage = lazy(() =>
  import("./routes/Warehouses").then((m) => ({ default: m.WarehousesPage })),
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
          lazyRoute("purchase-orders", <PurchaseOrdersPage />),
          lazyRoute("purchase-orders/new", <PurchaseOrderFormPage />),
          lazyRoute("purchase-orders/:id", <PurchaseOrderDetailPage />),
          lazyRoute("purchase-orders/:id/edit", <PurchaseOrderFormPage />),
          lazyRoute("inventory", <InventoryPage />),
          lazyRoute("inventory/categories", <ItemCategoriesPage />),
          lazyRoute("inventory/:id", <InventoryItemDetailPage />),
          lazyRoute("suppliers", <SuppliersPage />),
          lazyRoute("suppliers/:id", <SupplierDetailPage />),
          lazyRoute("warehouses", <WarehousesPage />),
          { path: "*", element: <NotFoundPage /> },
        ],
      },
    ],
  },
]);

// Name this app's session slot before anything can read a token, so two OpsGrids apps open
// in one browser never share a session.
configureSession("opsgrids.operations.access_token");

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <ThemeProvider storageKey="opsgrids.operations.theme">
      <Toaster>
        <RouterProvider router={router} />
      </Toaster>
    </ThemeProvider>
  </StrictMode>,
);
