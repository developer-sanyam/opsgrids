import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";

import { listBoms } from "@/api/client";
import type { BomOut } from "@/api/types";
import { BomFormDialog } from "@/forms/BomFormDialog";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  SearchInput,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { NetworkIcon, PlusIcon, StatusBadge } from "@opsgrids/ui";

/**
 * Bills of materials (inventory Phase 5): what goes into each produced item. Reading is
 * `operations.manufacturing.bom.read`; "New BOM" is `...bom.write`. A BOM is created with its first
 * draft version and the screen goes straight to it, because an empty draft is the next thing to fill.
 */
export function BomsPage() {
  const navigate = useNavigate();
  const [search, setSearch] = useState("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);

  const state = useAsync(() => listBoms({ q: search, page, size }), [search, page, size]);

  const columns: Column<BomOut>[] = [
    {
      key: "code",
      header: "Code",
      render: (b) => (
        <Link to={`/manufacturing/boms/${b.id}`} className="font-semibold text-ink hover:underline">
          {b.code}
        </Link>
      ),
    },
    { key: "name", header: "Name", render: (b) => b.name },
    {
      key: "item",
      header: "Produces",
      render: (b) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{b.sku}</span>
          <span className="text-xs text-muted">{b.item_name}</span>
        </div>
      ),
    },
    {
      key: "current",
      header: "Current version",
      render: (b) =>
        b.current_version_no === null ? (
          <span className="text-muted">Nothing published</span>
        ) : (
          <span className="tnum">Version {b.current_version_no}</span>
        ),
    },
    {
      key: "versions",
      header: "Versions",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (b) => <span className="tnum text-muted">{b.version_count}</span>,
    },
    {
      key: "active",
      header: "Status",
      render: (b) => (b.is_active ? <StatusBadge status="active" /> : <StatusBadge status="inactive" />),
    },
  ];

  const searching = search !== "";

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Manufacturing"
        title="Bills of materials"
        description="The recipe for each produced item, versioned so an order always knows which one it was planned from."
        actions={
          <Can permission="operations.manufacturing.bom.write" scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New BOM
            </Button>
          </Can>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-6">
        <SearchInput
          label="Search BOMs"
          placeholder="Search by code or name…"
          value={search}
          onDebouncedChange={(q) => {
            setSearch(q);
            setPage(1);
          }}
          containerClassName="lg:max-w-sm"
        />
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3" aria-busy="true" aria-label="Loading BOMs">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load BOMs" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(b) => b.id}
              caption="Bills of materials"
              emptyState={
                <EmptyState
                  icon={<NetworkIcon className="size-6" />}
                  title={searching ? "No BOMs match this search" : "No BOMs yet"}
                  description={
                    searching
                      ? "Try a different code or name."
                      : "Create a BOM to define what goes into a produced item."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="BOMs"
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
        <Can permission="operations.manufacturing.bom.write" scope="ORG">
          <BomFormDialog
            onClose={() => setCreating(false)}
            onCreated={(bom) => {
              setCreating(false);
              navigate(`/manufacturing/boms/${bom.id}`);
            }}
          />
        </Can>
      ) : null}
    </div>
  );
}
