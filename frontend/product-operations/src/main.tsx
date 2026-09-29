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
import { LoginPage } from "./routes/Login";
import { NotFoundPage } from "./routes/NotFound";
import { RouteFallback } from "./routes/RouteFallback";
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
const WarehouseLocationsPage = lazy(() =>
  import("./routes/WarehouseLocations").then((m) => ({ default: m.WarehouseLocationsPage })),
);
const WarehouseLocationLabelsPage = lazy(() =>
  import("./routes/WarehouseLocationLabels").then((m) => ({ default: m.WarehouseLocationLabelsPage })),
);
const WarehouseFloorsPage = lazy(() =>
  import("./routes/WarehouseFloors").then((m) => ({ default: m.WarehouseFloorsPage })),
);
const FloorLayoutEditorPage = lazy(() =>
  import("./routes/FloorLayoutEditor").then((m) => ({ default: m.FloorLayoutEditorPage })),
);
const Warehouse3DPage = lazy(() => import("./routes/Warehouse3D").then((m) => ({ default: m.Warehouse3DPage })));
const OrdersPage = lazy(() => import("./routes/Orders").then((m) => ({ default: m.OrdersPage })));
const OrderFormPage = lazy(() => import("./routes/OrderForm").then((m) => ({ default: m.OrderFormPage })));
const OrderDetailPage = lazy(() => import("./routes/OrderDetail").then((m) => ({ default: m.OrderDetailPage })));
const FulfilmentTasksPage = lazy(() =>
  import("./routes/FulfilmentTasks").then((m) => ({ default: m.FulfilmentTasksPage })),
);
const FulfilmentWorkersPage = lazy(() =>
  import("./routes/FulfilmentWorkers").then((m) => ({ default: m.FulfilmentWorkersPage })),
);
const PaymentSettingsPage = lazy(() =>
  import("./routes/PaymentSettings").then((m) => ({ default: m.PaymentSettingsPage })),
);
const DispatchShipmentsPage = lazy(() =>
  import("./routes/DispatchShipments").then((m) => ({ default: m.DispatchShipmentsPage })),
);
const CountSessionsPage = lazy(() =>
  import("./routes/CountSessions").then((m) => ({ default: m.CountSessionsPage })),
);
const CountSessionDetailPage = lazy(() =>
  import("./routes/CountSessionDetail").then((m) => ({ default: m.CountSessionDetailPage })),
);
const AutomationPage = lazy(() => import("./routes/Automation").then((m) => ({ default: m.AutomationPage })));

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
            appName="OpsGrids Inventory"
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
          lazyRoute("warehouses/:id/locations", <WarehouseLocationsPage />),
          lazyRoute("warehouses/:id/floors", <WarehouseFloorsPage />),
          lazyRoute("warehouses/:id/floors/:floorId", <FloorLayoutEditorPage />),
          lazyRoute("warehouses/:id/3d", <Warehouse3DPage />),
          lazyRoute("orders", <OrdersPage />),
          lazyRoute("orders/new", <OrderFormPage />),
          lazyRoute("orders/:id", <OrderDetailPage />),
          lazyRoute("fulfilment/tasks", <FulfilmentTasksPage />),
          lazyRoute("fulfilment/workers", <FulfilmentWorkersPage />),
          lazyRoute("dispatch", <DispatchShipmentsPage />),
          lazyRoute("counting", <CountSessionsPage />),
          lazyRoute("counting/:id", <CountSessionDetailPage />),
          lazyRoute("automation", <AutomationPage />),
          lazyRoute("settings/payments", <PaymentSettingsPage />),
          { path: "*", element: <NotFoundPage /> },
        ],
      },
      // Its own route, outside `AppShell`: a printed label sheet has no use for a sidebar and top
      // bar, and this sidesteps fighting them with print stylesheets. Still behind `RequireAuth` —
      // it reads the same permissioned location data the tree screen does.
      {
        path: "/warehouses/:id/locations/labels",
        element: (
          <RequireAuth>
            <Suspense fallback={<RouteFallback />}>
              <WarehouseLocationLabelsPage />
            </Suspense>
          </RequireAuth>
        ),
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
