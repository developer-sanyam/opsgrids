import { useState } from "react";
import { useSearchParams } from "react-router-dom";

import { useAsync } from "@opsgrids/client";
import { Button, Card, EmptyState, PageHeader, SearchInput, StatusBadge } from "@opsgrids/ui";

import { getMerchantAccount, listChannels, listWarehouses } from "@/api/integrations";
import type { ChannelOut, MerchantAccountOut } from "@/api/integrations";
import { CATEGORIES, groupConnectors } from "@/lib/integrations";
import type { Connector } from "@/lib/integrations";

import { ConnectChannelDialog } from "./integrations/ConnectChannelDialog";
import { ConnectPaymentDialog } from "./integrations/ConnectPaymentDialog";
import { ManageChannelDialog } from "./integrations/ManageChannelDialog";
import { ManagePaymentDialog } from "./integrations/ManagePaymentDialog";

export function IntegrationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const query = searchParams.get("q") ?? "";
  const filterCat = searchParams.get("category") ?? "all";
  const filterState = searchParams.get("state") ?? "all";

  // Data fetching
  const channelsState = useAsync(() => listChannels(), []);
  const channels = channelsState.status === "success" ? channelsState.data : [];

  const merchantState = useAsync(() => getMerchantAccount(), []);
  const merchantAccount = merchantState.status === "success" ? merchantState.data : null;

  const warehousesState = useAsync(() => listWarehouses(), []);
  const warehouses = warehousesState.status === "success" ? warehousesState.data : [];

  // Dialog state
  const [connectConnector, setConnectConnector] = useState<Connector | null>(null);
  const [manageChannel, setManageChannel] = useState<ChannelOut | null>(null);
  const [managePayment, setManagePayment] = useState<MerchantAccountOut | null>(null);

  // Helper to determine live connection status
  function getConnectedChannel(key: string): ChannelOut | undefined {
    return channels.find((c) => c.provider === key && c.status === "connected");
  }

  function isConnected(connector: Connector): boolean {
    if (connector.category === "payments") {
      return merchantAccount !== null && merchantAccount.provider === connector.key;
    }
    return getConnectedChannel(connector.key) !== undefined;
  }

  // Filter connectors
  const allGroups = groupConnectors(query);
  const filteredGroups = allGroups
    .map((g) => {
      if (filterCat !== "all" && g.category.key !== filterCat) {
        return null;
      }
      const filteredConnectors = g.connectors.filter((c) => {
        if (filterState === "connected") return isConnected(c);
        if (filterState === "available") return !isConnected(c);
        return true;
      });
      if (filteredConnectors.length === 0) return null;
      return { ...g, connectors: filteredConnectors };
    })
    .filter((g): g is NonNullable<typeof g> => g !== null);

  const totalConnected =
    channels.filter((c) => c.status === "connected").length + (merchantAccount ? 1 : 0);
  const totalCount = filteredGroups.reduce((acc, g) => acc + g.connectors.length, 0);

  function handleConnectClick(connector: Connector) {
    setConnectConnector(connector);
  }

  function handleManageClick(connector: Connector) {
    if (connector.category === "payments") {
      if (merchantAccount) setManagePayment(merchantAccount);
      return;
    }
    const channel = getConnectedChannel(connector.key);
    if (channel) {
      setManageChannel(channel);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Integrations & Channels"
        title="Connect & Automate Across Your Platform"
        description="Configure your sales marketplaces, couriers, payment providers, and communication channels. Integrations configured here on app.opsgrids are unified across your organization and shared automatically with Operations, Storefront, Finance, CRM, and Engage."
      />

      {/* Overview Stat Strip */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <Card className="flex flex-col gap-1 p-4">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">Active Integrations</span>
          <span className="text-2xl font-bold text-ink">{totalConnected} Connected</span>
          <span className="text-xs text-muted">Live across all products</span>
        </Card>
        <Card className="flex flex-col gap-1 p-4">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">Available Connectors</span>
          <span className="text-2xl font-bold text-ink">{allGroups.reduce((acc, g) => acc + g.connectors.length, 0)} Supported</span>
          <span className="text-xs text-muted">Sales, couriers, payments & ERP</span>
        </Card>
        <Card className="flex flex-col gap-1 p-4">
          <span className="text-xs font-semibold uppercase tracking-wider text-muted">Organization Scope</span>
          <span className="text-2xl font-bold text-ink">Centralized</span>
          <span className="text-xs text-muted">Configured once, active everywhere</span>
        </Card>
      </div>

      {/* Search & Filter Controls */}
      <div className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
          <SearchInput
            label="Search integrations"
            placeholder="Search by name, provider or category…"
            value={query}
            onChange={(e) => {
              const next = e.target.value;
              const p = new URLSearchParams(searchParams);
              if (next) p.set("q", next);
              else p.delete("q");
              setSearchParams(p, { replace: true });
            }}
            containerClassName="w-full sm:max-w-md"
          />
          <div className="flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              variant={filterState === "all" ? "primary" : "outline"}
              onClick={() => {
                const p = new URLSearchParams(searchParams);
                p.set("state", "all");
                setSearchParams(p, { replace: true });
              }}
            >
              All
            </Button>
            <Button
              size="sm"
              variant={filterState === "connected" ? "primary" : "outline"}
              onClick={() => {
                const p = new URLSearchParams(searchParams);
                p.set("state", "connected");
                setSearchParams(p, { replace: true });
              }}
            >
              Connected ({totalConnected})
            </Button>
            <Button
              size="sm"
              variant={filterState === "available" ? "primary" : "outline"}
              onClick={() => {
                const p = new URLSearchParams(searchParams);
                p.set("state", "available");
                setSearchParams(p, { replace: true });
              }}
            >
              Available
            </Button>
          </div>
          <span className="tnum ml-auto text-sm text-muted">
            {totalCount} {totalCount === 1 ? "connector" : "connectors"}
          </span>
        </div>

        {/* Category Filter Pills */}
        <div className="flex flex-wrap gap-1.5 overflow-x-auto pb-1">
          <button
            type="button"
            className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
              filterCat === "all"
                ? "bg-accent-surface text-accent-ink"
                : "bg-surface-muted text-muted hover:text-ink"
            }`}
            onClick={() => {
              const p = new URLSearchParams(searchParams);
              p.set("category", "all");
              setSearchParams(p, { replace: true });
            }}
          >
            All Categories
          </button>
          {CATEGORIES.map((cat) => (
            <button
              key={cat.key}
              type="button"
              className={`rounded-full px-3 py-1 text-xs font-semibold transition-colors ${
                filterCat === cat.key
                  ? "bg-accent-surface text-accent-ink"
                  : "bg-surface-muted text-muted hover:text-ink"
              }`}
              onClick={() => {
                const p = new URLSearchParams(searchParams);
                p.set("category", cat.key);
                setSearchParams(p, { replace: true });
              }}
            >
              {cat.name}
            </button>
          ))}
        </div>
      </div>

      {/* Connectors Grid by Category */}
      {filteredGroups.length === 0 ? (
        <EmptyState
          title="No integrations match that filter"
          description="Try clearing your search term or selecting another category."
          action={
            <Button
              variant="outline"
              onClick={() => {
                setSearchParams({}, { replace: true });
              }}
            >
              Reset Filters
            </Button>
          }
        />
      ) : (
        filteredGroups.map(({ category, connectors }) => (
          <section key={category.key} aria-labelledby={`cat-${category.key}`} className="flex flex-col gap-3">
            <div className="flex items-center justify-between border-b pb-2">
              <h2 id={`cat-${category.key}`} className="text-section font-bold">
                {category.name}
              </h2>
              <span className="tnum text-xs font-medium text-muted">
                {connectors.length} {connectors.length === 1 ? "provider" : "providers"}
              </span>
            </div>

            <ul className="grid grid-cols-1 list-none gap-4 p-0 sm:grid-cols-2 xl:grid-cols-3">
              {connectors.map((c) => {
                const connected = isConnected(c);
                const activeChannel = getConnectedChannel(c.key);

                return (
                  <li key={c.key} className="flex">
                    <Card className="flex w-full flex-col justify-between gap-4 p-5 transition-shadow hover:shadow-card">
                      <div className="flex flex-col gap-2.5">
                        <div className="flex items-start justify-between gap-2">
                          <h3 className="text-card-title text-base font-bold">{c.name}</h3>
                          <StatusBadge
                            tone={connected ? "success" : c.status === "available" ? "neutral" : "warning"}
                            label={connected ? "Connected" : c.status === "available" ? "Available" : "Partner"}
                          />
                        </div>

                        <p className="text-xs leading-relaxed text-muted">
                          {c.description ?? `Connect ${c.name} for automated operations and business workflow.`}
                        </p>

                        {/* Connected Metadata Pill */}
                        {connected ? (
                          <div className="rounded-card bg-surface-muted px-3 py-2 text-xs">
                            {c.category === "payments" && merchantAccount ? (
                              <p className="font-mono text-xs text-ink">
                                Mode: <span className="font-bold uppercase">{merchantAccount.mode}</span> • ID:{" "}
                                {merchantAccount.key_id}
                              </p>
                            ) : activeChannel ? (
                              <p className="truncate font-mono text-xs text-ink">
                                {activeChannel.external_shop_id ? `${activeChannel.external_shop_id} • ` : ""}
                                {activeChannel.auto_accept_orders ? "Auto-accept ON" : "Manual review"}
                              </p>
                            ) : (
                              <p className="text-xs text-ink">Active in organization</p>
                            )}
                          </div>
                        ) : null}
                      </div>

                      {/* Card Action Buttons */}
                      <div className="flex items-center justify-between border-t pt-3">
                        {connected ? (
                          <>
                            <span className="flex items-center gap-1.5 text-xs font-medium text-success">
                              <span className="inline-block h-2 w-2 rounded-full bg-success" />
                              Active & Synced
                            </span>
                            <Button
                              size="sm"
                              variant="outline"
                              onClick={() => handleManageClick(c)}
                            >
                              Manage
                            </Button>
                          </>
                        ) : (
                          <>
                            <span className="text-xs text-muted">Ready to configure</span>
                            <Button
                              size="sm"
                              variant="primary"
                              onClick={() => handleConnectClick(c)}
                            >
                              Connect
                            </Button>
                          </>
                        )}
                      </div>
                    </Card>
                  </li>
                );
              })}
            </ul>
          </section>
        ))
      )}

      {/* Connect Channel Modal */}
      {connectConnector && connectConnector.category !== "payments" ? (
        <ConnectChannelDialog
          connector={connectConnector}
          warehouses={warehouses}
          onClose={() => setConnectConnector(null)}
          onConnected={() => {
            setConnectConnector(null);
            void channelsState.refresh();
          }}
        />
      ) : null}

      {/* Connect Payment Modal */}
      {connectConnector && connectConnector.category === "payments" ? (
        <ConnectPaymentDialog
          connector={connectConnector}
          onClose={() => setConnectConnector(null)}
          onConnected={() => {
            setConnectConnector(null);
            void merchantState.refresh();
          }}
        />
      ) : null}

      {/* Manage Channel Modal */}
      {manageChannel ? (
        <ManageChannelDialog
          channel={manageChannel}
          warehouses={warehouses}
          onClose={() => setManageChannel(null)}
          onUpdated={() => {
            void channelsState.refresh();
          }}
          onDisconnected={() => {
            setManageChannel(null);
            void channelsState.refresh();
          }}
        />
      ) : null}

      {/* Manage Payment Modal */}
      {managePayment ? (
        <ManagePaymentDialog
          account={managePayment}
          onClose={() => setManagePayment(null)}
          onUpdated={() => {
            void merchantState.refresh();
          }}
          onDisconnected={() => {
            setManagePayment(null);
            void merchantState.refresh();
          }}
        />
      ) : null}
    </div>
  );
}
