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
import { StockReadGate } from "./movements/StockReadGate";
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
const PurchaseRequisitionsPage = lazy(() =>
  import("./routes/PurchaseRequisitions").then((m) => ({ default: m.PurchaseRequisitionsPage })),
);
const PurchaseRequisitionFormPage = lazy(() =>
  import("./routes/PurchaseRequisitionForm").then((m) => ({ default: m.PurchaseRequisitionFormPage })),
);
const PurchaseRequisitionDetailPage = lazy(() =>
  import("./routes/PurchaseRequisitionDetail").then((m) => ({ default: m.PurchaseRequisitionDetailPage })),
);
const ReceiveGoodsPage = lazy(() => import("./routes/ReceiveGoods").then((m) => ({ default: m.ReceiveGoodsPage })));
const GoodsReceiptsPage = lazy(() =>
  import("./routes/GoodsReceipts").then((m) => ({ default: m.GoodsReceiptsPage })),
);
const GoodsReceiptDetailPage = lazy(() =>
  import("./routes/GoodsReceiptDetail").then((m) => ({ default: m.GoodsReceiptDetailPage })),
);
const InventoryPage = lazy(() =>
  import("./routes/Inventory").then((m) => ({ default: m.InventoryPage })),
);
const InventoryItemDetailPage = lazy(() =>
  import("./routes/InventoryItemDetail").then((m) => ({ default: m.InventoryItemDetailPage })),
);
const InventoryLotsPage = lazy(() =>
  import("./routes/InventoryLots").then((m) => ({ default: m.InventoryLotsPage })),
);
const LotDetailPage = lazy(() => import("./routes/LotDetail").then((m) => ({ default: m.LotDetailPage })));
const InventoryRollsPage = lazy(() =>
  import("./routes/InventoryRolls").then((m) => ({ default: m.InventoryRollsPage })),
);
const RollDetailPage = lazy(() => import("./routes/RollDetail").then((m) => ({ default: m.RollDetailPage })));
const InventoryStockPage = lazy(() =>
  import("./routes/InventoryStock").then((m) => ({ default: m.InventoryStockPage })),
);
const InventoryLedgerPage = lazy(() =>
  import("./routes/InventoryLedger").then((m) => ({ default: m.InventoryLedgerPage })),
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
const TransfersPage = lazy(() => import("./routes/Transfers").then((m) => ({ default: m.TransfersPage })));
const TransferFormPage = lazy(() => import("./routes/TransferForm").then((m) => ({ default: m.TransferFormPage })));
const TransferDetailPage = lazy(() =>
  import("./routes/TransferDetail").then((m) => ({ default: m.TransferDetailPage })),
);
const ReservationsPage = lazy(() => import("./routes/Reservations").then((m) => ({ default: m.ReservationsPage })));
const AdjustmentsPage = lazy(() => import("./routes/Adjustments").then((m) => ({ default: m.AdjustmentsPage })));
const ApprovalSettingsPage = lazy(() =>
  import("./routes/ApprovalSettings").then((m) => ({ default: m.ApprovalSettingsPage })),
);
const AutomationPage = lazy(() => import("./routes/Automation").then((m) => ({ default: m.AutomationPage })));
const ChannelsPage = lazy(() => import("./routes/Channels").then((m) => ({ default: m.ChannelsPage })));

function lazyRoute(path: string, element: ReactNode) {
  return { path, element: <Suspense fallback={<RouteFallback />}>{element}</Suspense> };
}

/** A movements screen: lazy, and behind the stock-read permission every read on it needs. */
function stockRoute(path: string, element: ReactNode) {
  return lazyRoute(path, <StockReadGate>{element}</StockReadGate>);
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
          lazyRoute("purchase-orders/:id/receive", <ReceiveGoodsPage />),
          lazyRoute("purchase-requisitions", <PurchaseRequisitionsPage />),
          lazyRoute("purchase-requisitions/new", <PurchaseRequisitionFormPage />),
          lazyRoute("purchase-requisitions/:id", <PurchaseRequisitionDetailPage />),
          lazyRoute("purchase-requisitions/:id/edit", <PurchaseRequisitionFormPage />),
          lazyRoute("goods-receipts", <GoodsReceiptsPage />),
          lazyRoute("goods-receipts/:id", <GoodsReceiptDetailPage />),
          lazyRoute("inventory", <InventoryPage />),
          lazyRoute("inventory/categories", <ItemCategoriesPage />),
          // Static segments are registered before `inventory/:id`; they would otherwise be read as an item id.
          lazyRoute("inventory/lots", <InventoryLotsPage />),
          lazyRoute("inventory/lots/:id", <LotDetailPage />),
          lazyRoute("inventory/rolls", <InventoryRollsPage />),
          lazyRoute("inventory/rolls/:id", <RollDetailPage />),
          lazyRoute("inventory/stock", <InventoryStockPage />),
          lazyRoute("inventory/ledger", <InventoryLedgerPage />),
          stockRoute("inventory/transfers", <TransfersPage />),
          stockRoute("inventory/transfers/new", <TransferFormPage />),
          stockRoute("inventory/transfers/:id", <TransferDetailPage />),
          stockRoute("inventory/transfers/:id/edit", <TransferFormPage />),
          stockRoute("inventory/reservations", <ReservationsPage />),
          stockRoute("inventory/adjustments", <AdjustmentsPage />),
          stockRoute("inventory/settings/approvals", <ApprovalSettingsPage />),
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
          lazyRoute("channels", <ChannelsPage />),
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
