import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { Button, Card, InlineAlert, PageHeader } from "@opsgrids/ui";

import { useAuth } from "@/auth/context";
import { EnvBadge } from "@/ui";
import { CreatePlanDialog } from "./plans/CreatePlanDialog";
import { PlanDetail } from "./plans/PlanDetail";
import { PlanList } from "./plans/PlanList";
import type { PlanFilters } from "./plans/PlanList";

/**
 * Plans & prices — the catalogue every customer is priced from (catalog's `/admin/catalog`).
 *
 * `owner` and `billing_ops` may read it; only `owner` may change it, and every change carries a
 * reason and an `Idempotency-Key`. `support` is not shown it at all: the catalogue includes every
 * bespoke version, which is every customer's negotiated price. These role checks only decide what
 * is drawn — catalog refuses the same calls server-side whatever this screen shows.
 *
 * The open plan lives in the URL (`?plan=<id>`) so a plan can be linked to and Back works.
 */
export function PlansPage() {
  const { hasRole } = useAuth();
  const canRead = hasRole("owner") || hasRole("billing_ops");
  const canWrite = hasRole("owner");
  const [params, setParams] = useSearchParams();
  const planId = params.get("plan");
  const [filters, setFilters] = useState<PlanFilters>({ productKey: "", audience: "", state: "" });
  const [creating, setCreating] = useState(false);
  const [result, setResult] = useState("");
  const [reloadToken, setReloadToken] = useState(0);

  if (!canRead) {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Catalog" title="Plans & prices" actions={<EnvBadge />} />
        <Card className="p-6">
          <InlineAlert
            tone="info"
            title="Plans & prices need the owner or billing role"
            live="off"
          >
            The catalogue includes every customer&apos;s negotiated price, so support staff do not see it.
          </InlineAlert>
        </Card>
      </div>
    );
  }

  function open(id: string | null) {
    setResult("");
    setParams(id === null ? {} : { plan: id });
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Catalog"
        title="Plans & prices"
        description={
          canWrite
            ? "What every customer can buy. Draft a version to change a price; publishing freezes it for good."
            : "What every customer can buy. Read-only — changing the catalogue needs the owner role."
        }
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <EnvBadge />
            {canWrite && planId === null ? (
              <Button variant="primary" onClick={() => setCreating(true)}>
                New plan
              </Button>
            ) : null}
          </div>
        }
      />

      <div role="status" aria-live="polite">
        {result ? <InlineAlert tone="success" title={result} live="off" /> : null}
      </div>

      {planId === null ? (
        <PlanList
          filters={filters}
          onFiltersChange={setFilters}
          onOpen={(plan) => open(plan.id)}
          reloadToken={reloadToken}
        />
      ) : (
        <PlanDetail
          key={planId}
          planId={planId}
          canWrite={canWrite}
          onBack={() => open(null)}
          onResult={setResult}
        />
      )}

      {creating ? (
        <CreatePlanDialog
          onClose={() => setCreating(false)}
          onDone={(created) => {
            setCreating(false);
            setResult(`${created.name} created. Draft a version to give it a price.`);
            setReloadToken((token) => token + 1);
          }}
        />
      ) : null}
    </div>
  );
}
