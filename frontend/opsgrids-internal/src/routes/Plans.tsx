import { Link } from "react-router-dom";

import { Card, EnvBadge, PageHeader } from "@/ui";
import { LayersIcon } from "@/ui/icons";

/**
 * There is no catalog-browsing read on the live API. `catalog-service`'s `/admin/catalog` prefix
 * only carries write routes — create a plan, patch it, retire it, create or publish a version —
 * never a `GET` that lists products, plans or versions (`services/catalog/app/api/admin.py` has no
 * such route; `libs/contracts/openapi/catalog.json` confirms it). A screen that called one anyway
 * would 404 on every load, so this page says what is actually true instead of always erroring: the
 * console cannot browse the catalog yet, and bespoke pricing — the one thing this page used to also
 * offer — already has a real, working home on each account's own page.
 */
export function PlansPage() {
  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Catalog"
        title="Plans & Pricing"
        description="Browsing the global catalog from here isn't available yet."
        actions={<EnvBadge />}
      />

      <Card className="flex flex-col items-center gap-4 p-10 text-center">
        <span className="grid size-14 place-items-center rounded-full bg-ground text-ink-soft">
          <LayersIcon className="size-6" />
        </span>
        <div className="flex max-w-md flex-col gap-2">
          <p className="text-base font-bold">Catalog browsing isn't available from the console yet</p>
          <p className="text-sm text-muted">
            The catalog service's admin surface only exposes writes — creating and publishing plans
            and versions — not a read that lists them. There is nothing this screen can honestly show
            until that endpoint exists.
          </p>
        </div>
        <p className="text-sm text-muted">
          To set what one customer pays for a product, open their account and use{" "}
          <span className="font-semibold text-ink">Client pricing</span> — that flow is wired to the
          real pricing API.
        </p>
        <Link
          to="/accounts"
          className="inline-flex h-10 items-center rounded-full bg-accent px-4 text-sm font-semibold text-white hover:bg-accent-strong transition-colors"
        >
          Go to accounts
        </Link>
      </Card>
    </div>
  );
}
