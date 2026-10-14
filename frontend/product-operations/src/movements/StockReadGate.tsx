import type { ReactNode } from "react";

import { RequirePermission } from "@opsgrids/client";
import { Card, EmptyState, GoBackButton } from "@/ui";
import { ShieldIcon } from "@opsgrids/ui";

/**
 * Gates a movements screen on `operations.inventory.stock.read` — the permission every read behind
 * it asks for. The screen is hidden, not protected: a direct request is refused by the server
 * regardless. A person who lacks it is told so and who to ask, rather than shown an empty page.
 */
export function StockReadGate({ children }: { children: ReactNode }) {
  return (
    <RequirePermission
      perm="operations.inventory.stock.read"
      scope="ORG"
      fallback={
        <Card className="p-6">
          <EmptyState
            icon={<ShieldIcon className="size-6" />}
            title="You don't have access to stock movements"
            description="Ask an administrator for permission to read inventory stock, or check that you are signed in to the right organization."
            action={<GoBackButton />}
          />
        </Card>
      }
    >
      {children}
    </RequirePermission>
  );
}
