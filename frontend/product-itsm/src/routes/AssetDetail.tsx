import { useCallback } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getAsset, getIncident } from "@/api/client";
import type { Asset, Incident } from "@/api/types";
import { assetStatusLabel, assetTypeLabel, formatDate, formatMoney } from "@/lib/format";
import { useAsync } from "@atmora/client";
import {
  AssetStatusPill,
  Card,
  ErrorState,
  IncidentStatusPill,
  PriorityPill,
  SectionHeader,
  Skeleton,
} from "@/ui";
import { ArrowLeftIcon, BoxIcon, CheckCircleIcon, ChevronRight } from "@atmora/ui";

interface AssetDetail {
  asset: Asset;
  incidents: Incident[];
}

const NOW_MS = Date.parse("2026-08-24T18:00:00Z");

function PropertyRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-3 py-2.5">
      <span className="text-sm text-muted">{label}</span>
      <span className="text-sm font-semibold text-ink text-right">{children}</span>
    </div>
  );
}

export function AssetDetailPage() {
  const { tag = "" } = useParams();
  const navigate = useNavigate();

  const loader = useCallback(async (): Promise<AssetDetail> => {
    const asset = await getAsset(tag);
    const incidents = await Promise.all(asset.open_incident_ids.map((id) => getIncident(id)));
    return { asset, incidents };
  }, [tag]);
  const state = useAsync(loader, [tag]);

  return (
    <div className="rise flex flex-col gap-5">
      <button
        type="button"
        onClick={() => navigate("/assets")}
        className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to assets
      </button>

      {state.status === "loading" ? (
        <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
          <Card className="p-6 xl:col-span-2">
            <Skeleton className="h-8 w-2/3" />
            <Skeleton className="mt-4 h-40 w-full rounded-tile" />
          </Card>
          <Card className="p-6">
            <Skeleton className="h-40 w-full rounded-tile" />
          </Card>
        </div>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={state.error.message} onRetry={() => navigate("/assets")} />
        </Card>
      ) : null}

      {state.status === "success"
        ? (() => {
            const { asset, incidents } = state.data;
            const warrantyExpired =
              asset.warranty_expires_at !== null &&
              Date.parse(`${asset.warranty_expires_at}T00:00:00Z`) < NOW_MS;
            return (
              <>
                <header className="flex flex-col gap-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="tnum text-sm font-semibold text-muted">{asset.tag}</span>
                    <AssetStatusPill status={asset.status} />
                    <span className="rounded-full bg-ground px-2.5 py-1 text-xs font-medium text-muted">
                      {assetTypeLabel(asset.type)}
                    </span>
                  </div>
                  <h1 className="display text-2xl sm:text-3xl font-extrabold leading-tight">
                    {asset.name}
                  </h1>
                </header>

                <div className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
                  {/* Left — properties */}
                  <Card className="flex flex-col p-6 xl:col-span-2">
                    <SectionHeader title="Configuration item" subtitle="CMDB record" />
                    <dl className="mt-2 grid grid-cols-1 gap-x-8 sm:grid-cols-2">
                      <div className="divide-y divide-line">
                        <PropertyRow label="Type">{assetTypeLabel(asset.type)}</PropertyRow>
                        <PropertyRow label="Status">{assetStatusLabel(asset.status)}</PropertyRow>
                        <PropertyRow label="Owner">{asset.owner ?? "Unassigned"}</PropertyRow>
                        <PropertyRow label="Location">{asset.location}</PropertyRow>
                      </div>
                      <div className="divide-y divide-line">
                        <PropertyRow label="Serial">
                          <span className="tnum">{asset.serial}</span>
                        </PropertyRow>
                        <PropertyRow label="Purchased">
                          <span className="tnum font-medium text-muted">
                            {formatDate(asset.purchased_at)}
                          </span>
                        </PropertyRow>
                        <PropertyRow label="Warranty">
                          <span className="inline-flex items-center gap-2">
                            {warrantyExpired ? (
                              <span className="rounded-full bg-danger-soft px-2 py-0.5 text-[0.7rem] font-bold text-danger">
                                Expired
                              </span>
                            ) : null}
                            <span className="tnum">{formatDate(asset.warranty_expires_at)}</span>
                          </span>
                        </PropertyRow>
                        <PropertyRow label="Book value">
                          <span className="tnum">
                            {formatMoney(asset.cost_minor, asset.cost_currency)}
                          </span>
                        </PropertyRow>
                      </div>
                    </dl>
                  </Card>

                  {/* Right — linked incidents */}
                  <Card className="flex flex-col gap-4 p-6">
                    <SectionHeader
                      title="Open incidents"
                      subtitle={`${incidents.length} linked to this CI`}
                    />
                    {incidents.length === 0 ? (
                      <div className="flex flex-col items-center gap-2 py-6 text-center">
                        <span className="grid size-11 place-items-center rounded-full bg-present-soft text-present">
                          <CheckCircleIcon className="size-6" />
                        </span>
                        <p className="text-sm font-semibold text-ink">No open incidents</p>
                        <p className="text-sm text-muted">This asset is healthy right now.</p>
                      </div>
                    ) : (
                      <ul className="flex flex-col gap-2">
                        {incidents.map((incident) => (
                          <li key={incident.id}>
                            <Link
                              to={`/incidents/${incident.id}`}
                              className="group flex items-center gap-3 rounded-tile px-3 py-2.5 transition-colors hover:bg-ground focus-visible:bg-ground"
                            >
                              <div className="flex min-w-0 flex-1 flex-col gap-1">
                                <div className="flex items-center gap-2">
                                  <PriorityPill priority={incident.priority} />
                                  <IncidentStatusPill status={incident.status} />
                                </div>
                                <p className="truncate text-sm font-semibold text-ink">
                                  {incident.summary}
                                </p>
                                <span className="tnum text-xs text-muted">{incident.id}</span>
                              </div>
                              <ChevronRight className="size-4 shrink-0 text-faint transition-colors group-hover:text-ink" />
                            </Link>
                          </li>
                        ))}
                      </ul>
                    )}
                  </Card>
                </div>

                <p className="flex items-center gap-2 text-xs text-faint">
                  <BoxIcon className="size-4" />
                  Configuration items live in the CMDB with no cross-service foreign keys — incident
                  links are resolved by tag.
                </p>
              </>
            );
          })()
        : null}
    </div>
  );
}
