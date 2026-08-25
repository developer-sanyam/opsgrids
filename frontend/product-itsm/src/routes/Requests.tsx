import { useCallback, useMemo, useState } from "react";
import type { ComponentType, SVGProps } from "react";

import { listCatalog, listRequests } from "@/api/client";
import type { CatalogCategory, CatalogItem, RequestStatus, ServiceRequest } from "@/api/types";
import { catalogCategoryLabel, relativeTime } from "@/lib/format";
import { useAsync } from "@atmora/client";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pill,
  RequestStatusPill,
  SearchInput,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import {
  ClockIcon,
  GridIcon,
  KeyIcon,
  LaptopIcon,
  LogOutIcon,
  TicketIcon,
  UserPlusIcon,
} from "@atmora/ui";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

const CATEGORY_ICON: Record<CatalogCategory, IconType> = {
  onboarding: UserPlusIcon,
  offboarding: LogOutIcon,
  access: KeyIcon,
  hardware: LaptopIcon,
  software: GridIcon,
};

const CATEGORY_TONE: Record<CatalogCategory, string> = {
  onboarding: "bg-accent-soft text-accent-ink",
  offboarding: "bg-late-soft text-late",
  access: "bg-info-soft text-info",
  hardware: "bg-cyan-soft text-cyan",
  software: "bg-present-soft text-present",
};

type StatusFilter = RequestStatus | "all";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "approval", label: "Awaiting approval" },
  { value: "in_progress", label: "In progress" },
  { value: "fulfilled", label: "Fulfilled" },
];

const NO_REQUESTS: ServiceRequest[] = [];
const NO_CATALOG: CatalogItem[] = [];

function CatalogCard({ item }: { item: CatalogItem }) {
  const Icon = CATEGORY_ICON[item.category];
  return (
    <Card className="flex flex-col gap-3 p-5">
      <div className="flex items-start gap-3">
        <span
          className={`grid size-10 shrink-0 place-items-center rounded-tile ${CATEGORY_TONE[item.category]}`}
        >
          <Icon className="size-5" />
        </span>
        <div className="flex min-w-0 flex-1 flex-col leading-tight">
          <span className="font-bold text-ink text-balance">{item.name}</span>
          <span className="text-xs text-muted">{catalogCategoryLabel(item.category)}</span>
        </div>
      </div>
      <p className="text-sm text-muted leading-relaxed">{item.description}</p>
      <div className="mt-auto flex items-center gap-2 border-t border-line pt-3 text-xs">
        <span className="inline-flex items-center gap-1.5 font-semibold text-ink-soft">
          <ClockIcon className="size-4 text-faint" />
          SLA {item.sla_days}d
        </span>
        <span className="ml-auto tnum text-muted">{item.requests_30d} in last 30d</span>
      </div>
    </Card>
  );
}

export function RequestsPage() {
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");

  const catalogState = useAsync(useCallback(() => listCatalog(), []), []);
  const requestsLoader = useCallback(() => listRequests({ search, status }), [search, status]);
  const requestsState = useAsync(requestsLoader, [search, status]);

  const catalog = catalogState.status === "success" ? catalogState.data : NO_CATALOG;
  const requests = useMemo(
    () => (requestsState.status === "success" ? requestsState.data.items : NO_REQUESTS),
    [requestsState],
  );

  const columns: Column<ServiceRequest>[] = useMemo(
    () => [
      {
        key: "id",
        header: "ID",
        render: (r) => <span className="tnum font-semibold text-ink-soft">{r.id}</span>,
      },
      {
        key: "type",
        header: "Request type",
        render: (r) => (
          <div className="flex min-w-0 max-w-[18rem] flex-col">
            <span className="truncate font-semibold text-ink">{r.catalog_item_name}</span>
            <span className="truncate text-xs text-muted">{r.subject}</span>
          </div>
        ),
      },
      {
        key: "requester",
        header: "Requester",
        cellClassName: "hidden md:table-cell",
        headerClassName: "hidden md:table-cell",
        render: (r) => (
          <span className="inline-flex items-center gap-2">
            <Avatar name={r.requester} size="sm" />
            <span className="text-sm text-ink-soft whitespace-nowrap">{r.requester}</span>
          </span>
        ),
      },
      {
        key: "status",
        header: "Status",
        render: (r) => <RequestStatusPill status={r.status} />,
      },
      {
        key: "assignee",
        header: "With",
        cellClassName: "hidden lg:table-cell",
        headerClassName: "hidden lg:table-cell",
        render: (r) => (
          <span className="text-sm text-muted whitespace-nowrap">{r.assignee ?? "—"}</span>
        ),
      },
      {
        key: "updated",
        header: "Updated",
        align: "right",
        cellClassName: "hidden sm:table-cell",
        headerClassName: "hidden sm:table-cell",
        render: (r) => (
          <span className="tnum text-sm text-muted whitespace-nowrap">
            {relativeTime(r.updated_at)}
          </span>
        ),
      },
    ],
    [],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Service desk"
        title="Service requests"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <TicketIcon className="size-4 text-muted" />
            <span className="tnum">{requests.length}</span>
            <span className="text-muted font-medium">requests</span>
          </span>
        }
      />

      {/* Catalog */}
      <section aria-label="Service catalog" className="flex flex-col gap-4">
        <SectionHeader
          title="Service catalog"
          subtitle="Request types staff can raise — including the joiners, movers and leavers flows"
        />
        {catalogState.status === "loading" ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Card key={i} className="p-5 flex flex-col gap-3">
                <div className="flex gap-3">
                  <Skeleton className="size-10 rounded-tile" />
                  <Skeleton className="h-5 w-40" />
                </div>
                <Skeleton className="h-12 w-full rounded-tile" />
              </Card>
            ))}
          </div>
        ) : null}
        {catalogState.status === "error" ? (
          <Card className="p-6">
            <ErrorState message={catalogState.error.message} />
          </Card>
        ) : null}
        {catalogState.status === "success" ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {catalog.map((item) => (
              <CatalogCard key={item.id} item={item} />
            ))}
          </div>
        ) : null}
      </section>

      {/* Requests */}
      <section aria-label="Requests" className="flex flex-col gap-4">
        <SectionHeader title="Requests" subtitle="Everything raised, and where it is in fulfilment" />

        <Card className="p-4 sm:p-5 flex flex-col gap-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
            <SearchInput
              label="Search requests"
              placeholder="Search by ID, requester, subject or type…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              containerClassName="lg:max-w-sm"
            />
            <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
              {STATUS_FILTERS.map((f) => (
                <Pill key={f.value} active={status === f.value} onClick={() => setStatus(f.value)}>
                  {f.label}
                </Pill>
              ))}
            </div>
          </div>
        </Card>

        <Card className="p-2 sm:p-4">
          {requestsState.status === "loading" ? (
            <div className="flex flex-col gap-3 p-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {requestsState.status === "error" ? (
            <ErrorState message={requestsState.error.message} />
          ) : null}
          {requestsState.status === "success" ? (
            <DataTable
              columns={columns}
              rows={requests}
              rowKey={(r) => r.id}
              caption="Service requests"
              emptyState={
                <EmptyState
                  icon={<TicketIcon className="size-6" />}
                  title="No matching requests"
                  description="Nothing matches these filters. Try a different status or clear the search."
                />
              }
            />
          ) : null}
        </Card>
      </section>
    </div>
  );
}
