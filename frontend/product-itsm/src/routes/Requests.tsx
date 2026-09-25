import { useCallback, useMemo, useState } from "react";
import type { ComponentType, SVGProps } from "react";
import { useNavigate } from "react-router-dom";

import { deleteCatalogItem, listCatalog, listRequests } from "@/api/client";
import type { CatalogCategory, CatalogItem, RequestStatus, ServiceRequest } from "@/api/types";
import { catalogCategoryLabel, relativeTime } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Avatar,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Pill,
  RequestStatusPill,
  SearchInput,
  SectionHeader,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import {
  Button,
  ClockIcon,
  ConfirmDialog,
  DownloadIcon,
  GridIcon,
  KeyIcon,
  LaptopIcon,
  LogOutIcon,
  PlusIcon,
  RowActions,
  TicketIcon,
  UserPlusIcon,
  useToast,
} from "@opsgrids/ui";
import type { TableSelection } from "@opsgrids/ui";
import { CatalogItemFormDialog } from "@/forms/CatalogItemFormDialog";
import { RaiseRequestDialog } from "@/forms/RaiseRequestDialog";
import { downloadCsv, toCsv } from "@/lib/exportCsv";

const CAN_RAISE = "itsm.service.request.write";
/** Administering the catalog is its own ORG-only permission, not the one a requester holds. */
const CAN_ADMIN_CATALOG = "itsm.service.catalog.write";

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
  { value: "rejected", label: "Rejected" },
];

const NO_REQUESTS: ServiceRequest[] = [];
const NO_CATALOG: CatalogItem[] = [];

function CatalogCard({
  item,
  canRaise,
  canAdmin,
  onRaise,
  onEdit,
  onDelete,
}: {
  item: CatalogItem;
  canRaise: boolean;
  canAdmin: boolean;
  onRaise: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
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
        {canAdmin ? (
          <RowActions
            label={item.name}
            actions={[
              { label: "Edit", onSelect: onEdit },
              { label: "Retire", tone: "danger", onSelect: onDelete },
            ]}
          />
        ) : null}
      </div>
      <p className="text-sm text-muted leading-relaxed">{item.description}</p>
      <div className="mt-auto flex items-center gap-2 border-t border-line pt-3 text-xs">
        <span className="inline-flex items-center gap-1.5 font-semibold text-ink-soft">
          <ClockIcon className="size-4 text-faint" />
          SLA {item.sla_days}d
        </span>
        {canRaise ? (
          <Button size="sm" variant="outline" className="ml-auto" onClick={onRaise}>
            Raise request
          </Button>
        ) : null}
      </div>
    </Card>
  );
}

export function RequestsPage() {
  const navigate = useNavigate();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [raising, setRaising] = useState<CatalogItem | null>(null);
  const [catalogCreating, setCatalogCreating] = useState(false);
  const [catalogEditing, setCatalogEditing] = useState<CatalogItem | null>(null);
  const [catalogDeleting, setCatalogDeleting] = useState<CatalogItem | null>(null);
  const [selectedIds, setSelectedIds] = useState<ReadonlySet<string>>(new Set());

  const canRaise = useCan(CAN_RAISE, "OWN");
  const canAdminCatalog = useCan(CAN_ADMIN_CATALOG, "ORG");

  const catalogState = useAsync(useCallback(() => listCatalog(), []), []);
  const requestsLoader = useCallback(
    () => listRequests({ search, status, page, size }),
    [search, status, page, size],
  );
  const requestsState = useAsync(requestsLoader, [search, status, page, size]);

  const retireCatalogItem = useMutation((item: CatalogItem) => deleteCatalogItem(item.id), {
    onSuccess: (_result, item) => {
      setCatalogDeleting(null);
      toast.success(`${item.name} retired from the catalog.`);
      catalogState.reload();
    },
  });

  const catalog = catalogState.status === "success" ? catalogState.data.items : NO_CATALOG;
  const requests = useMemo(
    () => (requestsState.status === "success" ? requestsState.data.items : NO_REQUESTS),
    [requestsState],
  );

  const columns: Column<ServiceRequest>[] = useMemo(
    () => [
      {
        key: "id",
        header: "ID",
        mobile: "field",
        render: (r) => <span className="tnum font-semibold text-ink-soft">{r.id}</span>,
      },
      {
        key: "type",
        header: "Request type",
        mobile: "title",
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
        mobile: "status",
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

  const hasFilters = search.trim() !== "" || status !== "all";

  const selection: TableSelection<ServiceRequest> = {
    selected: selectedIds,
    onToggle: (key, next) =>
      setSelectedIds((prev) => {
        const nextSet = new Set(prev);
        if (next) nextSet.add(key);
        else nextSet.delete(key);
        return nextSet;
      }),
    onToggleAll: (next) => setSelectedIds(next ? new Set(requests.map((r) => r.id)) : new Set()),
    label: (r) => r.id,
  };

  function exportSelected() {
    const rows = requests.filter((r) => selectedIds.has(r.id));
    const csv = toCsv(
      ["ID", "Request type", "Subject", "Requester", "Status", "With", "Updated"],
      rows.map((r) => [
        r.id,
        r.catalog_item_name,
        r.subject,
        r.requester,
        r.status,
        r.assignee ?? "",
        r.updated_at,
      ]),
    );
    downloadCsv(`requests-selected-${rows.length}.csv`, csv);
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Service desk"
        title="Service requests"
        actions={
          <span className="rounded-full bg-surface shadow-card px-4 h-11 inline-flex items-center gap-2 text-sm font-semibold">
            <TicketIcon className="size-4 text-muted" />
            <span className="tnum">
              {requestsState.status === "success" ? requestsState.data.total : "—"}
            </span>
            <span className="text-muted font-medium">requests</span>
          </span>
        }
      />

      {/* Catalog */}
      <section aria-label="Service catalog" className="flex flex-col gap-4">
        <SectionHeader
          title="Service catalog"
          subtitle="Request types staff can raise — including the joiners, movers and leavers flows"
          action={
            <Can permission={CAN_ADMIN_CATALOG} scope="ORG">
              <Button
                size="sm"
                variant="outline"
                leadingIcon={<PlusIcon className="size-4" />}
                onClick={() => setCatalogCreating(true)}
              >
                Add item
              </Button>
            </Can>
          }
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
            <ErrorState message={problemMessage(catalogState.error)} onRetry={catalogState.reload} />
          </Card>
        ) : null}
        {catalogState.status === "success" && catalog.length === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={<GridIcon className="size-6" />}
              title="Nothing is available to request yet"
              description={
                canAdminCatalog
                  ? "Add the first catalog item so people have something to raise a request against."
                  : "Ask an administrator to add items to the service catalog."
              }
              action={
                canAdminCatalog ? (
                  <Button size="sm" onClick={() => setCatalogCreating(true)}>
                    Add the first item
                  </Button>
                ) : null
              }
            />
          </Card>
        ) : null}
        {catalogState.status === "success" && catalog.length > 0 ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-3">
            {catalog.map((item) => (
              <CatalogCard
                key={item.id}
                item={item}
                canRaise={canRaise}
                canAdmin={canAdminCatalog}
                onRaise={() => setRaising(item)}
                onEdit={() => setCatalogEditing(item)}
                onDelete={() => setCatalogDeleting(item)}
              />
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
              placeholder="Search by ID, requester or subject…"
              value={search}
              onDebouncedChange={(next) => {
                setSearch(next);
                setPage(1);
              }}
              containerClassName="lg:max-w-sm"
            />
            <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
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
          {requestsState.status === "loading" ? (
            <div className="flex flex-col gap-3 p-3">
              {Array.from({ length: 6 }).map((_, i) => (
                <Skeleton key={i} className="h-12 w-full rounded-tile" />
              ))}
            </div>
          ) : null}
          {requestsState.status === "error" ? (
            <ErrorState
              title="Couldn't load requests"
              message={problemMessage(requestsState.error)}
              onRetry={requestsState.reload}
            />
          ) : null}
          {requestsState.status === "success" ? (
            <>
              <DataTable
                columns={columns}
                rows={requests}
                rowKey={(r) => r.id}
                caption="Service requests"
                onRowClick={(r) => navigate(`/requests/${r.id}`)}
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
                    icon={<TicketIcon className="size-6" />}
                    title={hasFilters ? "No matching requests" : "No requests yet"}
                    description={
                      hasFilters
                        ? "Nothing matches these filters. Try a different status or clear the search."
                        : "Raise the first request from the catalog above."
                    }
                  />
                }
              />
              <Pagination
                page={requestsState.data.page}
                pages={requestsState.data.pages}
                total={requestsState.data.total}
                size={requestsState.data.size}
                noun="requests"
                onPageChange={setPage}
                onSizeChange={(n) => {
                  setSize(n);
                  setPage(1);
                }}
              />
            </>
          ) : null}
        </Card>
      </section>

      {raising ? (
        <RaiseRequestDialog
          catalog={catalog}
          preselected={raising}
          onClose={() => setRaising(null)}
          onCreated={(request) => {
            setRaising(null);
            toast.success(`${request.id} raised — awaiting approval.`);
            requestsState.reload();
          }}
        />
      ) : null}

      {catalogCreating ? (
        <CatalogItemFormDialog
          item={null}
          onClose={() => setCatalogCreating(false)}
          onSaved={(item) => {
            setCatalogCreating(false);
            toast.success(`${item.name} added to the catalog.`);
            catalogState.reload();
          }}
        />
      ) : null}

      {catalogEditing ? (
        <CatalogItemFormDialog
          item={catalogEditing}
          onClose={() => setCatalogEditing(null)}
          onSaved={(item) => {
            setCatalogEditing(null);
            toast.success(`${item.name} saved.`);
            catalogState.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={catalogDeleting !== null}
        onClose={() => {
          setCatalogDeleting(null);
          retireCatalogItem.reset();
        }}
        onConfirm={() => catalogDeleting && void retireCatalogItem.mutate(catalogDeleting)}
        title="Retire this catalog item?"
        body={`${catalogDeleting?.name} is removed from the catalog. Existing requests against it keep rendering.`}
        confirmLabel="Retire"
        pendingLabel="Retiring…"
        pending={retireCatalogItem.pending}
        error={retireCatalogItem.error ? problemMessage(retireCatalogItem.error) : undefined}
      />
    </div>
  );
}
