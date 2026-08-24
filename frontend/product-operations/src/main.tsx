import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter, Outlet, RouterProvider } from "react-router-dom";

import "./index.css";
import { AppShell } from "./App";
import { AuthProvider } from "./auth/AuthProvider";
import { RequireAuth } from "./auth/RequireAuth";
import { OverviewPage } from "./routes/Overview";
import { PurchaseOrdersPage } from "./routes/PurchaseOrders";
import { PurchaseOrderDetailPage } from "./routes/PurchaseOrderDetail";
import { InventoryPage } from "./routes/Inventory";
import { SuppliersPage } from "./routes/Suppliers";
import { SupplierDetailPage } from "./routes/SupplierDetail";
import { LoginPage } from "./routes/Login";
import { ComingSoonPage } from "./routes/ComingSoon";
import {
  FileTextIcon,
  LifeBuoyIcon,
  PlugIcon,
  SettingsIcon,
  TruckIcon,
  UserPlusIcon,
  WarehouseIcon,
} from "./ui/icons";

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
          { path: "purchase-orders", element: <PurchaseOrdersPage /> },
          { path: "purchase-orders/:id", element: <PurchaseOrderDetailPage /> },
          { path: "inventory", element: <InventoryPage /> },
          { path: "suppliers", element: <SuppliersPage /> },
          { path: "suppliers/:id", element: <SupplierDetailPage /> },
          {
            path: "receiving",
            element: (
              <ComingSoonPage
                icon={<TruckIcon className="size-6" />}
                title="Receiving"
                description="Log goods-receipt notes against open purchase orders and reconcile short-shipments. Today you can already receive an order from its detail page."
              />
            ),
          },
          {
            path: "warehouses",
            element: (
              <ComingSoonPage
                icon={<WarehouseIcon className="size-6" />}
                title="Warehouses"
                description="Bins, zones and stock transfers between sites. Inventory already tags every item with its warehouse."
              />
            ),
          },
          {
            path: "reports",
            element: (
              <ComingSoonPage
                icon={<FileTextIcon className="size-6" />}
                title="Reports"
                description="Exportable spend, supplier-performance and stock-ageing reports are on the roadmap."
              />
            ),
          },
          {
            path: "integrations",
            element: (
              <ComingSoonPage
                icon={<PlugIcon className="size-6" />}
                title="Integrations"
                description="ERP, accounting and carrier connectors will plug in here."
              />
            ),
          },
          {
            path: "team",
            element: (
              <ComingSoonPage
                icon={<UserPlusIcon className="size-6" />}
                title="Team Members"
                description="Invite and manage the buyers and approvers who run this procurement workspace."
              />
            ),
          },
          {
            path: "settings",
            element: (
              <ComingSoonPage
                icon={<SettingsIcon className="size-6" />}
                title="Settings"
                description="Approval matrices, currencies and workspace preferences will live here."
              />
            ),
          },
          {
            path: "help",
            element: (
              <ComingSoonPage
                icon={<LifeBuoyIcon className="size-6" />}
                title="Help & Getting Started"
                description="Guides and support for setting up your procurement workspace are on the way."
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

const rootEl = document.getElementById("root");
if (!rootEl) throw new Error("Root element #root not found");

createRoot(rootEl).render(
  <StrictMode>
    <RouterProvider router={router} />
  </StrictMode>,
);
