import { useCallback, useMemo, useState } from "react";
import type { ComponentType, SVGProps } from "react";
import { useNavigate } from "react-router-dom";

import { listAssets } from "@/api/client";
import type { Asset, AssetStatus, AssetType } from "@/api/types";
import { assetTypeLabel, formatDate } from "@/lib/format";
import { useAsync } from "@opsgrids/client";
import {
  AssetStatusPill,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import {
  BoxIcon,
  KeyIcon,
  LaptopIcon,
  MonitorIcon,
  NetworkIcon,
  PhoneIcon,
  ServerIcon,
} from "@opsgrids/ui";

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
const NOW_MS = Date.parse("2026-08-24T18:00:00Z");
const WARRANTY_WINDOW_DAYS = 60;

/** A small flag on the warranty cell: expired, or expiring within 60 days. */
function warrantyFlag(iso: string | null): { label: string; tone: string } | null {
  if (iso === null) return null;
  const exp = Date.parse(`${iso}T00:00:00Z`);
  if (exp < NOW_MS) return { label: "Expired", tone: "bg-danger-soft text-danger" };
  if (exp <= NOW_MS + WARRANTY_WINDOW_DAYS * 86_400_000) {
    return { label: "Soon", tone: "bg-late-soft text-late" };
  }
  return null;
}

export function AssetsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [type, setType] = useState<AssetType | "all">("all");
  const [status, setStatus] = useState<StatusFilter>("all");

  const loader = useCallback(() => listAssets({ search, type, status }), [search, type, status]);
  const state = useAsync(loader, [search, type, status]);

  const items = useMemo(
    () => (state.status === "success" ? state.data.items : NO_ASSETS),
    [state],
  );

  const columns: Column<Asset>[] = useMemo(
    () => [
      {
        key: "tag",
        header: "Tag",
        render: (a) => <span className="tnum font-semibold text-ink-soft">{a.tag}</span>,
      },
      {
        key: "name",
        header: "Asset",
        render: (a) => {
          const Icon = TYPE_ICON[a.type];
          return (
            <span className="inline-flex min-w-0 max-w-[20rem] items-center gap-2.5">
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
                <span
                  className={`rounded-full px-2 py-0.5 text-[0.7rem] font-bold ${flag.tone}`}
                >
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

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Assets & config"
        title="Assets (CMDB)"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <BoxIcon className="size-4 text-muted" />
            <span className="tnum">{items.length}</span>
            <span className="text-muted font-medium">assets</span>
          </span>
        }
      />

      <Card className="p-4 sm:p-5 flex flex-col gap-4">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search assets"
            placeholder="Search by tag, name, owner or serial…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            containerClassName="lg:max-w-sm"
          />
          <label className="inline-flex h-11 shrink-0 items-center gap-2 rounded-full bg-ground px-3 text-sm font-semibold text-ink-soft">
            <span className="sr-only">Filter by type</span>
            <select
              value={type}
              onChange={(e) => setType(e.target.value as AssetType | "all")}
              className="h-full cursor-pointer bg-transparent pr-1 text-sm font-semibold text-ink focus:outline-none"
            >
              {TYPE_OPTIONS.map((t) => (
                <option key={t.value} value={t.value}>
                  {t.label}
                </option>
              ))}
            </select>
          </label>
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1 lg:ml-auto">
            {STATUS_FILTERS.map((f) => (
              <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                {f.label}
              </Pill>
            ))}
          </div>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 8 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? <ErrorState message={state.error.message} /> : null}
        {state.status === "success" ? (
          <DataTable
            columns={columns}
            rows={items}
            rowKey={(a) => a.tag}
            caption="Assets"
            onRowClick={(a) => navigate(`/assets/${a.tag}`)}
            emptyState={
              <EmptyState
                icon={<BoxIcon className="size-6" />}
                title="No matching assets"
                description="Nothing matches these filters. Try a different type, status, or clear the search."
              />
            }
          />
        ) : null}
      </Card>
    </div>
  );
}
