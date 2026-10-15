import { useSearchParams } from "react-router-dom";

import { getPlatformHost, PLATFORM_PRODUCTS } from "@opsgrids/client";
import { EmptyState, SearchInput, StatusBadge } from "@opsgrids/ui";
import type { StatusTone } from "@opsgrids/ui";

import { groupConnectors } from "@/lib/integrations";
import type { Connector, ConnectorStatus } from "@/lib/integrations";
import { Button, Card, PageHeader } from "@/ui";

const BADGE: Record<ConnectorStatus, { tone: StatusTone; label: string }> = {
  available: { tone: "success", label: "Available" },
  coming_soon: { tone: "neutral", label: "Coming soon" },
  needs_partner: { tone: "warning", label: "Needs partner" },
};

function ConnectorCard({ connector }: { connector: Connector }) {
  const product = PLATFORM_PRODUCTS.find((p) => p.key === connector.product);
  // Only an available connector can be connected; the others show their status and no dead link.
  const href =
    connector.status === "available" && connector.product !== null && connector.path !== null
      ? `${getPlatformHost(connector.product)}${connector.path}`
      : null;
  return (
    <li className="flex">
      <Card className="flex w-full flex-col gap-3 p-4">
        <div className="flex items-start justify-between gap-2">
          <h3 className="text-card-title">{connector.name}</h3>
          <StatusBadge {...BADGE[connector.status]} />
        </div>
        {href !== null ? (
          <a
            href={href}
            className="mt-auto inline-flex min-h-11 items-center text-sm font-semibold text-accent-ink underline-offset-2 hover:underline"
          >
            Connect in {product?.displayName ?? "its product"}
            <span className="sr-only"> — {connector.name}</span>
          </a>
        ) : null}
      </Card>
    </li>
  );
}

/**
 * The connector catalogue. Connecting stays on the owning product's own page (it holds the
 * credentials screen and the permission); this page only lists what exists and where to go.
 * Reachable by any signed-in member of the organisation.
 */
export function IntegrationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const groups = groupConnectors(query);
  const count = groups.reduce((n, g) => n + g.connectors.length, 0);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Integrations"
        title="Connect your own accounts"
        description="Sales channels, couriers, payment providers and more. You connect your own partner accounts, so money and shipments stay with you."
      />
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <SearchInput
          label="Search integrations"
          placeholder="Search by name or category"
          value={query}
          onChange={(e) => {
            const next = e.target.value;
            setSearchParams(next ? { q: next } : {}, { replace: true });
          }}
          containerClassName="w-full sm:max-w-sm"
        />
        <p aria-live="polite" className="tnum text-sm text-muted">
          {count} {count === 1 ? "integration" : "integrations"}
        </p>
      </div>
      {groups.length === 0 ? (
        <EmptyState
          title="No integrations match that search"
          description="Try a shorter search term."
          action={
            <Button variant="outline" onClick={() => setSearchParams({}, { replace: true })}>
              Clear search
            </Button>
          }
        />
      ) : (
        groups.map(({ category, connectors }) => (
          <section key={category.key} aria-labelledby={`cat-${category.key}`} className="flex flex-col gap-3">
            <h2 id={`cat-${category.key}`} className="text-section">
              {category.name}
            </h2>
            <ul className="grid grid-cols-1 list-none gap-3 p-0 sm:grid-cols-2 xl:grid-cols-3">
              {connectors.map((c) => (
                <ConnectorCard key={c.key} connector={c} />
              ))}
            </ul>
          </section>
        ))
      )}
    </div>
  );
}
