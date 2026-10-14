import { useCan, useProductShell } from "@opsgrids/client";
import { CreditCardIcon, PlusIcon, ProductShell, ReceiptIcon } from "@opsgrids/ui";
import type { ProductQuickAction } from "@opsgrids/ui";
import { useNavGroups } from "./ui";
import { LogoMark } from "./ui/LogoMark";

const BANNER_COPY = {
  title: "Permissions couldn't be checked — some actions may be hidden",
  body:
    "Create, edit and issue controls are hidden until this succeeds. Reloading the page also retries it.",
};

/**
 * The app shell — the sidebar, the rail, the mobile header/bottom nav and the routed main column —
 * is `@opsgrids/ui`'s `ProductShell`, fed by `useProductShell` from `@opsgrids/client`. This file
 * supplies only what is this product's: its mark, its own nav, its search and its words.
 */
export function AppShell() {
  const shell = useProductShell();
  const { groups, pinned } = useNavGroups();
  const canIssue = useCan("finance.billing.invoice.issue", "ORG");
  const canRecord = useCan("finance.billing.payment.record", "ORG");
  // "Record payment" and "Issue credit note" act on an invoice: the first opens the Payments screen
  // where recording starts, the second the invoice list to pick the invoice to credit.
  const quickActions: ProductQuickAction[] = [
    ...(canIssue ? [{ key: "new-invoice", label: "New invoice", icon: PlusIcon, to: "/invoices/new" }] : []),
    ...(canRecord ? [{ key: "record-payment", label: "Record payment", icon: CreditCardIcon, to: "/payments" }] : []),
    ...(canIssue ? [{ key: "issue-credit-note", label: "Issue credit note", icon: ReceiptIcon, to: "/invoices" }] : []),
  ];

  return (
    <ProductShell
      {...shell}
      product={{ name: "OpsGrids Finance", mark: <LogoMark className="size-7" /> }}
      nav={groups}
      pinned={pinned}
      searchPlaceholder="Search invoices"
      searchFallback={{ label: "Search invoices for", to: "/invoices" }}
      bannerCopy={BANNER_COPY}
      bottomNavExtra={["/invoices", "/payments"]}
      quickActions={quickActions}
    />
  );
}
