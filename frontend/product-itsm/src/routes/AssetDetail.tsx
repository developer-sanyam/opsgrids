import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { deleteAsset, getAsset } from "@/api/client";
import type { Asset } from "@/api/types";
import { assetStatusLabel, assetTypeLabel, daysUntil, formatDate, formatMoney } from "@/lib/format";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { AssetStatusPill, Card, ErrorState, SectionHeader, Skeleton } from "@/ui";
import {
  ArrowLeftIcon,
  BoxIcon,
  Button,
  CheckCircleIcon,
  ConfirmDialog,
  useToast,
} from "@opsgrids/ui";
import { AssetFormDialog } from "@/forms/AssetFormDialog";

const CAN_WRITE = "itsm.cmdb.asset.write";

function PropertyRow({ label, children }: { label: string; children: ReactNode }) {
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
  const toast = useToast();

  const loader = useCallback(() => getAsset(tag), [tag]);
  const state = useAsync(loader, [tag]);
  const [override, setOverride] = useState<Asset | null>(null);
  useEffect(() => setOverride(null), [tag]);
  const asset = override ?? (state.status === "success" ? state.data : null);

  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const remove = useMutation<void, void>(() => deleteAsset(asset?.tag ?? ""), {
    onSuccess: () => {
      toast.success(`${asset?.tag} deleted.`);
      navigate("/assets");
    },
  });

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

      {state.status === "loading" && !asset ? (
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

      {state.status === "error" && !asset ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load that asset"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        </Card>
      ) : null}

      {asset
        ? (() => {
            const warrantyExpired =
              asset.warranty_expires_at !== null && daysUntil(asset.warranty_expires_at) < 0;
            const hasOpenIncidents = asset.open_incident_ids.length > 0;
            return (
              <>
                <header className="flex flex-wrap items-start justify-between gap-4">
                  <div className="flex flex-col gap-3">
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
                  </div>
                  <div className="flex items-center gap-2">
                    <Can permission={CAN_WRITE} scope="OWN">
                      <Button variant="outline" onClick={() => setEditing(true)}>
                        Edit
                      </Button>
                    </Can>
                    <Can permission={CAN_WRITE} scope="ORG">
                      <Button
                        variant="danger"
                        disabled={hasOpenIncidents}
                        title={
                          hasOpenIncidents
                            ? "Open incidents still name this asset — resolve or reassign them first."
                            : undefined
                        }
                        onClick={() => setDeleting(true)}
                      >
                        Delete
                      </Button>
                    </Can>
                  </div>
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
                        <PropertyRow label="Location">{asset.location || "—"}</PropertyRow>
                      </div>
                      <div className="divide-y divide-line">
                        <PropertyRow label="Serial">
                          <span className="tnum">{asset.serial || "—"}</span>
                        </PropertyRow>
                        <PropertyRow label="Purchased">
                          <span className="tnum font-medium text-muted">
                            {formatDate(asset.purchased_at)}
                          </span>
                        </PropertyRow>
                        <PropertyRow label="Warranty">
                          <span className="inline-flex items-center gap-2">
                            {warrantyExpired ? (
                              <span className="rounded-full bg-danger-soft px-2 py-0.5 text-caption font-bold text-danger">
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
                      subtitle={`${asset.open_incident_ids.length} linked to this CI`}
                    />
                    {asset.open_incident_ids.length === 0 ? (
                      <div className="flex flex-col items-center gap-2 py-6 text-center">
                        <span className="grid size-11 place-items-center rounded-full bg-present-soft text-present">
                          <CheckCircleIcon className="size-6" />
                        </span>
                        <p className="text-sm font-semibold text-ink">No open incidents</p>
                        <p className="text-sm text-muted">This asset is healthy right now.</p>
                      </div>
                    ) : (
                      <ul className="flex flex-col gap-2">
                        {asset.open_incident_ids.map((number) => (
                          <li key={number}>
                            <button
                              type="button"
                              onClick={() => navigate(`/incidents/${number}`)}
                              className="group flex w-full items-center gap-3 rounded-tile px-3 py-2.5 text-left transition-colors hover:bg-ground focus-visible:bg-ground"
                            >
                              <span className="tnum text-sm font-semibold text-ink">{number}</span>
                              <span className="ml-auto text-xs text-muted">View incident →</span>
                            </button>
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

      {editing && asset ? (
        <AssetFormDialog
          asset={asset}
          onClose={() => setEditing(false)}
          onSaved={(updated) => {
            setEditing(false);
            setOverride(updated);
            toast.success("Asset saved.");
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting}
        onClose={() => {
          setDeleting(false);
          remove.reset();
        }}
        onConfirm={() => void remove.mutate()}
        title="Delete this asset?"
        body={`${asset?.tag} — ${asset?.name} is removed from the CMDB. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        error={remove.error ? problemMessage(remove.error) : undefined}
      />
    </div>
  );
}
