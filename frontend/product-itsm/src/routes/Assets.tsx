import { useCallback, useMemo, useState } from "react";
import type { ComponentType, SVGProps } from "react";
import { useNavigate } from "react-router-dom";

import { listAssets } from "@/api/client";
import type { Asset, AssetStatus, AssetType } from "@/api/types";
import { assetsEntity } from "@/data/importExport/assets";
import type { AssetFilters } from "@/data/importExport/assets";
import { assetTypeLabel, daysUntil, formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useImportExport } from "@opsgrids/client";
import {
  AssetStatusPill,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import {
  BoxIcon,
  Button,
  DownloadIcon,
  ImportExportButton,
  KeyIcon,
  LaptopIcon,
  MonitorIcon,
  NetworkIcon,
  PhoneIcon,
  PlusIcon,
  ServerIcon,
  useToast,
} from "@opsgrids/ui";
import type { TableSelection } from "@opsgrids/ui";
import { AssetFormDialog } from "@/forms/AssetFormDialog";
import { downloadCsv, toCsv } from "@/lib/exportCsv";

const CAN_WRITE = "itsm.cmdb.asset.write";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

const TYPE_ICON: Record<AssetType, IconType> = {
  laptop: LaptopIcon,
  desktop: MonitorIcon,
  server: ServerIcon,
  monitor: MonitorIcon,
  phone: PhoneIcon,
  network: NetworkIcon,
  license: KeyIcon,
};

type StatusFilter = AssetStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "in_use", label: "In use" },
  { value: "in_stock", label: "In stock" },
  { value: "in_repair", label: "In repair" },
  { value: "retired", label: "Retired" },
];

const TYPE_OPTIONS: { value: AssetType | "all"; label: string }[] = [
  { value: "all", label: "All types" },
  { value: "laptop", label: "Laptops" },
  { value: "desktop", label: "Desktops" },
  { value: "server", label: "Servers" },
  { value: "monitor", label: "Monitors" },
  { value: "phone", label: "Phones" },
  { value: "network", label: "Network" },
  { value: "license", label: "Licenses" },
];

const NO_ASSETS: Asset[] = [];
const WARRANTY_WINDOW_DAYS = 60;

/** A small flag on the warranty cell: expired, or expiring within 60 days — against the real
 *  clock, never a frozen one. */
function warrantyFlag(iso: string | null): { label: string; tone: string } | null {
  if (iso === null) return null;
  const remaining = daysUntil(iso);
  if (remaining < 0) return { label: "Expired", tone: "bg-danger-soft text-danger" };
  if (remaining <= WARRANTY_WINDOW_DAYS) return { label: "Soon", tone: "bg-late-soft text-late" };
  return null;
}

export function AssetsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const canWrite = useCan(CAN_WRITE, "OWN");
  const [search, setSearch] = useState("");
  const [type, setType] = useState<AssetType | "all">("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [selectedTags, setSelectedTags] = useState<ReadonlySet<string>>(new Set());

  const loader = useCallback(
    () => listAssets({ search, type, status, page, size }),
    [search, type, status, page, size],
  );
  const state = useAsync(loader, [search, type, status, page, size]);

  // What an export of this list must respect: the filters on screen, not the page window.
  const exportFilters = useMemo<AssetFilters>(() => ({ search, type, status }), [search, type, status]);
  // The list's own reload (one identity for the life of the screen), so assets an import created
  // show up without a refresh.
  const dx = useImportExport(assetsEntity, { filters: exportFilters, onImported: state.reload });

  const items = useMemo(
    () => (state.status === "success" ? state.data.items : NO_ASSETS),
    [state],
  );
  const hasFilters = search.trim() !== "" || type !== "all" || status !== "all";

  const columns: Column<Asset>[] = useMemo(
    () => [
      {
        key: "tag",
        header: "Tag",
        mobile: "field",
        render: (a) => <span className="tnum font-semibold text-ink-soft">{a.tag}</span>,
      },
      {
        key: "name",
        header: "Asset",
        mobile: "title",
        render: (a) => {
          const Icon = TYPE_ICON[a.type];
          return (
            <span className="inline-flex min-w-0 max-w-full items-center gap-2.5 md:max-w-xs">
              <span className="grid size-8 shrink-0 place-items-center rounded-tile bg-ground text-ink-soft">
                <Icon className="size-[18px]" />
              </span>
              <span className="flex min-w-0 flex-col leading-tight">
                <span className="truncate font-semibold text-ink">{a.name}</span>
                <span className="truncate text-xs text-muted">{a.location}</span>
              </span>
            </span>
          );
        },
      },
      {
        key: "type",
        header: "Type",
        cellClassName: "hidden lg:table-cell",
        headerClassName: "hidden lg:table-cell",
        render: (a) => <span className="text-sm text-ink-soft">{assetTypeLabel(a.type)}</span>,
      },
      {
        key: "owner",
        header: "Owner",
        cellClassName: "hidden md:table-cell",
        headerClassName: "hidden md:table-cell",
        render: (a) => (
          <span className="text-sm text-ink-soft whitespace-nowrap">{a.owner ?? "Unassigned"}</span>
        ),
      },
      {
        key: "status",
        header: "Status",
        mobile: "status",
        render: (a) => <AssetStatusPill status={a.status} />,
      },
      {
        key: "warranty",
        header: "Warranty",
        align: "right",
        render: (a) => {
          const flag = warrantyFlag(a.warranty_expires_at);
          return (
            <span className="inline-flex items-center justify-end gap-2 whitespace-nowrap">
              {flag ? (
                <span className={`rounded-full px-2 py-0.5 text-caption font-bold ${flag.tone}`}>
                  {flag.label}
                </span>
              ) : null}
              <span className="tnum text-sm text-muted">{formatDate(a.warranty_expires_at)}</span>
            </span>
          );
        },
      },
    ],
    [],
  );

  const selection: TableSelection<Asset> = {
    selected: selectedTags,
    onToggle: (key, next) =>
      setSelectedTags((prev) => {
        const nextSet = new Set(prev);
        if (next) nextSet.add(key);
        else nextSet.delete(key);
        return nextSet;
      }),
    onToggleAll: (next) => setSelectedTags(next ? new Set(items.map((a) => a.tag)) : new Set()),
    label: (a) => a.name,
  };

  function exportSelected() {
    const rows = items.filter((a) => selectedTags.has(a.tag));
    const csv = toCsv(
      ["Tag", "Name", "Type", "Owner", "Status", "Warranty expires"],
      rows.map((a) => [
        a.tag,
        a.name,
        assetTypeLabel(a.type),
        a.owner ?? "Unassigned",
        a.status,
        a.warranty_expires_at ?? "",
      ]),
    );
    downloadCsv(`assets-selected-${rows.length}.csv`, csv);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Assets & config"
        title="Assets (CMDB)"
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
              <BoxIcon className="size-4 text-muted" />
              <span className="tnum">{state.status === "success" ? state.data.total : "—"}</span>
              <span className="text-muted font-medium">total</span>
            </span>
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canWrite}
              canExport
            />
            <Can permission={CAN_WRITE} scope="OWN">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                Add asset
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search assets"
            placeholder="Search by tag, name, owner or serial…"
            value={search}
            onDebouncedChange={(next) => {
              setSearch(next);
              setPage(1);
            }}
            containerClassName="lg:max-w-sm"
          />
          <label className="inline-flex h-11 shrink-0 items-center gap-2 rounded-full bg-ground px-3 text-sm font-semibold text-ink-soft focus-within:outline focus-within:outline-2 focus-within:outline-accent focus-within:outline-offset-2">
            <span className="sr-only">Filter by type</span>
            <select
              value={type}
              onChange={(e) => {
                setType(e.target.value as AssetType | "all");
                setPage(1);
              }}
              className="h-full cursor-pointer bg-transparent pr-1 text-sm font-semibold text-ink focus:outline-none"
            >
              {TYPE_OPTIONS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <div role="group" aria-label="Filter by status" className="flex items-center gap-1.5 overflow-x-auto -mx-1 -my-1 px-1 py-1 lg:ml-auto">
            {STATUS_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={status === f.value}
                onClick={() => {
                  setStatus(f.value);
                  setPage(1);
                }}
              >
                {f.label}
              </Pill>
            ))}
          </div>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <Skeleton label="Loading assets" className="m-3 h-96 rounded-tile" />
        ) : null}
        {state.status === "error" ? (
          <ErrorState
            title="Couldn't load assets"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={items}
              rowKey={(a) => a.tag}
              caption="Assets"
              onRowClick={(a) => navigate(`/assets/${a.tag}`)}
              mobileCard="auto"
              selection={selection}
              bulkActions={
                <Button
                  variant="secondary"
                  size="sm"
                  leadingIcon={<DownloadIcon className="size-4" />}
                  onClick={exportSelected}
                >
                  Export selected
                </Button>
              }
              emptyState={
                <EmptyState
                  icon={<BoxIcon className="size-6" />}
                  title={hasFilters ? "No matching assets" : "No assets yet"}
                  description={
                    hasFilters
                      ? "Nothing matches these filters. Try a different type, status, or clear the search."
                      : "Add the first asset to start tracking it."
                  }
                  action={
                    canWrite && !hasFilters ? (
                      <Button size="sm" onClick={() => setCreating(true)}>
                        Add asset
                      </Button>
                    ) : null
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="assets"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {creating ? (
        <AssetFormDialog
          asset={null}
          onClose={() => setCreating(false)}
          onSaved={(asset) => {
            setCreating(false);
            toast.success(`${asset.tag} added.`);
            state.reload();
          }}
        />
      ) : null}
    </div>
  );
}
