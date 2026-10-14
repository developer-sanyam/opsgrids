import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";

import { deleteDeal, listDeals } from "@/api/client";
import type { Company, Deal, DealStage } from "@/api/types";
import { dealsByStage } from "@/data/derive";
import { useDirectory } from "@/data/directoryContext";
import { dealImport } from "@/data/importExport/deals";
import type { DealFilters } from "@/data/importExport/deals";
import { companyName } from "@/data/lookups";
import { STAGES } from "@/data/stages";
import { formatMoney, cn, HandshakeIcon, ImportExportButton, KanbanIcon, LayoutIcon, PlusIcon, useDebouncedValue, useToast } from "@opsgrids/ui";
import { formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useImportExport, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  KanbanBoard,
  OwnerBadge,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  SearchInput,
  Select,
  Skeleton,
  StagePill,
} from "@/ui";
import type { Column } from "@/ui";
import { DealFormDialog } from "@/forms/DealFormDialog";

type StageFilter = DealStage | "all";
type View = "board" | "list";

const STAGE_FILTERS: { value: StageFilter; label: string }[] = [
  { value: "all", label: "All stages" },
  ...STAGES.map((s) => ({ value: s.stage as StageFilter, label: s.label })),
];

const NO_DEALS: Deal[] = [];
const BOARD_PAGE_SIZE = 200;
const LIST_PAGE_SIZE = 25;

function buildListColumns(
  companies: Company[],
  canWrite: boolean,
  onEdit: (d: Deal) => void,
  onDelete: (d: Deal) => void,
): Column<Deal>[] {
  return [
    {
      key: "name",
      header: "Deal",
      render: (d) => (
        <div className="flex flex-col leading-tight">
          <Link
            to={`/deals/${d.id}`}
            className="relative hit-44 font-semibold text-ink hover:text-accent transition-colors"
          >
            {d.name}
          </Link>
          <span className="text-xs text-muted">{companyName(companies, d.company_id)}</span>
        </div>
      ),
    },
    { key: "stage", header: "Stage", render: (d) => <StagePill stage={d.stage} /> },
    {
      key: "value",
      header: "Value",
      align: "right",
      render: (d) => <span className="tnum font-semibold text-ink">{formatMoney(d.value_minor, d.currency)}</span>,
    },
    {
      key: "owner",
      header: "Owner",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (d) => <OwnerBadge ownerId={d.owner_id} />,
    },
    {
      key: "close",
      header: "Close date",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (d) => <span className="tnum text-ink-soft">{formatDate(d.close_date)}</span>,
    },
    {
      key: "prob",
      header: "Prob.",
      align: "right",
      cellClassName: "hidden lg:table-cell",
      headerClassName: "hidden lg:table-cell",
      render: (d) => <span className="tnum text-muted">{d.probability}%</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (d) => (
        <RowActions
          label={d.name}
          actions={[
            { label: "Edit", onSelect: () => onEdit(d), hidden: !canWrite },
            { label: "Delete", tone: "danger", onSelect: () => onDelete(d), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];
}

export function DealsPage() {
  const toast = useToast();
  const [searchParams] = useSearchParams();
  const [search, setSearch] = useState(() => searchParams.get("q") ?? "");
  const debouncedSearch = useDebouncedValue(search);
  const [stage, setStage] = useState<StageFilter>("all");
  const [ownerId, setOwnerId] = useState<string | "all">("all");
  const [view, setView] = useState<View>("board");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(LIST_PAGE_SIZE);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<Deal | null>(null);
  const [deleting, setDeleting] = useState<Deal | null>(null);
  const directory = useDirectory();
  const { owners: ownerOptions, companies, contacts } = directory;
  const canWrite = useCan("crm.pipeline.deal.write", "OWN");

  useEffect(() => {
    const q = searchParams.get("q");
    if (q !== null) setSearch(q);
  }, [searchParams]);

  const effectiveSize = view === "board" ? BOARD_PAGE_SIZE : size;
  const effectivePage = view === "board" ? 1 : page;
  const loader = useCallback(
    () =>
      listDeals({
        search: debouncedSearch,
        stage,
        ownerId,
        page: effectivePage,
        size: effectiveSize,
      }),
    [debouncedSearch, stage, ownerId, effectivePage, effectiveSize],
  );
  const deals = useAsync(loader, [debouncedSearch, stage, ownerId, effectivePage, effectiveSize, view]);

  // The export writes the whole filtered pipeline, not the page or the board on screen, so it
  // needs the filters the list is loaded with (and neither the view nor the page).
  const importFilters = useMemo<DealFilters>(
    () => ({ search: debouncedSearch, stage, ownerId }),
    [debouncedSearch, stage, ownerId],
  );
  // New rows show without a refresh: the board or the list reloads.
  const { reload: reloadDeals } = deals;
  const dx = useImportExport(dealImport, { filters: importFilters, onImported: reloadDeals });

  const remove = useMutation((deal: Deal) => deleteDeal(deal.id), {
    onSuccess: (_result, deal) => {
      setDeleting(null);
      toast.success(`${deal.name} deleted.`);
      deals.reload();
    },
  });

  const items = useMemo(
    () => (deals.status === "success" ? deals.data.items : NO_DEALS),
    [deals],
  );
  const groups = useMemo(() => dealsByStage(items), [items]);
  const listColumns = useMemo(
    () => buildListColumns(companies, canWrite, setEditing, setDeleting),
    [companies, canWrite],
  );

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Pipeline"
        title="Deals"
        actions={
          <>
            <div
              role="tablist"
              aria-label="View"
              className="flex items-center rounded-full bg-surface p-0.5 shadow-card"
            >
              <button
                type="button"
                role="tab"
                aria-selected={view === "board"}
                onClick={() => setView("board")}
                className={cn(
                  "inline-flex h-11 items-center gap-1.5 rounded-full px-3.5 sm:h-9 text-sm font-semibold transition-colors",
                  view === "board" ? "bg-accent text-white" : "text-muted hover:text-ink",
                )}
              >
                <KanbanIcon className="size-4" />
                <span className="sr-only sm:not-sr-only">Board</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={view === "list"}
                onClick={() => setView("list")}
                className={cn(
                  "inline-flex h-11 items-center gap-1.5 rounded-full px-3.5 sm:h-9 text-sm font-semibold transition-colors",
                  view === "list" ? "bg-accent text-white" : "text-muted hover:text-ink",
                )}
              >
                <LayoutIcon className="size-4" />
                <span className="sr-only sm:not-sr-only">List</span>
              </button>
            </div>
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canWrite}
              canExport
            />
            <Can permission="crm.pipeline.deal.write" scope="OWN">
              <Button size="md" leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New deal
              </Button>
            </Can>
          </>
        }
      />

      {/* Controls */}
      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <div className="flex flex-col gap-3 lg:flex-row lg:items-center">
          <SearchInput
            label="Search deals"
            placeholder="Search by deal name…"
            value={search}
            onChange={(e) => {
              setSearch(e.target.value);
              setPage(1);
            }}
            containerClassName="lg:max-w-xs"
          />
          <div className="flex items-center gap-1.5 overflow-x-auto -mx-1 px-1">
            {STAGE_FILTERS.map((f) => (
              <Pill
                key={f.value}
                active={stage === f.value}
                onClick={() => {
                  setStage(f.value);
                  setPage(1);
                }}
              >
                {f.label}
              </Pill>
            ))}
          </div>
          <Select
            label="Filter by owner"
            containerClassName="lg:ml-auto lg:w-56"
            value={ownerId}
            onChange={(e) => {
              setOwnerId(e.target.value);
              setPage(1);
            }}
            options={[
              { value: "all", label: "All owners" },
              ...ownerOptions.map((o) => ({ value: o.id, label: o.full_name })),
            ]}
          />
        </div>
      </Card>

      {deals.status === "loading" ? (
        <div className="flex gap-4 overflow-hidden">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="flex w-[264px] shrink-0 flex-col gap-2.5 rounded-card bg-ground/60 p-2.5">
              <Skeleton className="h-6 w-32" />
              <Skeleton className="h-24 w-full rounded-tile" />
              <Skeleton className="h-24 w-full rounded-tile" />
            </div>
          ))}
        </div>
      ) : null}

      {deals.status === "error" ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load deals"
            message={problemMessage(deals.error)}
            onRetry={deals.reload}
          />
        </Card>
      ) : null}

      {deals.status === "success" ? (
        items.length === 0 ? (
          <Card className="p-6">
            <EmptyState
              icon={<HandshakeIcon className="size-6" />}
              title="No matching deals"
              description="No deals match these filters. Try another stage, owner, or clear the search."
              action={
                <Can permission="crm.pipeline.deal.write" scope="OWN">
                  <Button size="sm" onClick={() => setCreating(true)}>
                    New deal
                  </Button>
                </Can>
              }
            />
          </Card>
        ) : view === "board" ? (
          <KanbanBoard
            groups={groups}
            canMove={canWrite}
            onMoved={(message) => toast.success(message)}
            onMoveFailed={(message) => toast.error(message)}
            reload={deals.reload}
          />
        ) : (
          <>
            <Card className="p-2 sm:p-4">
              <DataTable
                caption="All deals in the pipeline"
                columns={listColumns}
                rows={items}
                rowKey={(d) => d.id}
              />
            </Card>
            <Pagination
              page={deals.data.page}
              pages={deals.data.pages}
              total={deals.data.total}
              size={deals.data.size}
              noun="deals"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        )
      ) : null}

      {creating ? (
        <DealFormDialog
          deal={null}
          companies={companies}
          contacts={contacts}
          owners={ownerOptions}
          onOwnerCreated={() => directory.reload()}
          onClose={() => setCreating(false)}
          onSaved={(deal) => {
            setCreating(false);
            toast.success(`${deal.name} created.`);
            deals.reload();
          }}
        />
      ) : null}

      {editing ? (
        <DealFormDialog
          deal={editing}
          companies={companies}
          contacts={contacts}
          owners={ownerOptions}
          onOwnerCreated={() => directory.reload()}
          onClose={() => setEditing(null)}
          onSaved={(deal) => {
            setEditing(null);
            toast.success(`${deal.name} saved.`);
            deals.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Delete this deal?"
        body={`${deleting?.name} is removed from the pipeline. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
