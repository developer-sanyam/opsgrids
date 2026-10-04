import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getBom } from "@/api/client";
import type { BomVersionOut } from "@/api/types";
import { BomVersionDialog } from "@/forms/BomVersionDialog";
import { BackLink } from "@/routes/manufacturing/BackLink";
import { formatDateRange, formatDateTime } from "@/lib/format";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import { BomVersionStatusPill, Button, Card, DataTable, EmptyState, ErrorState, PageHeader, SectionHeader, Skeleton } from "@/ui";
import type { Column } from "@/ui";
import { NetworkIcon, PlusIcon, StatusBadge } from "@opsgrids/ui";

/**
 * One BOM and its versions (`GET .../boms/{id}`). A BOM's recipe lives in its versions: a version
 * is a draft until it is published, and from then on it never changes — a change is a new version.
 * "New version" is `operations.manufacturing.bom.write`; the server's 409 when a draft already
 * exists is shown in the dialog.
 */
export function BomDetailPage() {
  const { bomId = "" } = useParams();
  const navigate = useNavigate();
  const [creating, setCreating] = useState(false);
  const state = useAsync(() => getBom(bomId), [bomId]);

  const back = <BackLink to="/manufacturing/boms">Back to bills of materials</BackLink>;

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6" aria-busy="true" aria-label="Loading BOM">
        {back}
        <Skeleton className="h-24 w-full rounded-tile" />
        <Skeleton className="h-64 w-full rounded-tile" />
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        {back}
        <ErrorState title="Couldn't load this BOM" message={problemMessage(state.error)} onRetry={state.reload} />
      </div>
    );
  }

  const bom = state.data;

  const columns: Column<BomVersionOut>[] = [
    {
      key: "version",
      header: "Version",
      render: (v) => (
        <Link
          to={`/manufacturing/boms/${bom.id}/versions/${v.id}`}
          className="font-semibold text-ink tnum hover:underline"
        >
          Version {v.version_no}
          {bom.current_version_id === v.id ? <span className="sr-only"> (current)</span> : null}
        </Link>
      ),
    },
    {
      key: "status",
      header: "Status",
      render: (v) => (
        <div className="flex flex-wrap items-center gap-2">
          <BomVersionStatusPill status={v.status} />
          {bom.current_version_id === v.id ? <StatusBadge tone="info" label="In force" /> : null}
        </div>
      ),
    },
    {
      key: "effective",
      header: "Effective",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (v) => <span className="text-muted">{formatDateRange(v.effective_from, v.effective_to)}</span>,
    },
    {
      key: "components",
      header: "Components",
      align: "right",
      render: (v) => <span className="tnum">{v.component_count}</span>,
    },
    {
      key: "published",
      header: "Published",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (v) => <span className="text-muted">{formatDateTime(v.published_at)}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      {back}
      <PageHeader
        eyebrow="Bill of materials"
        title={`${bom.code} · ${bom.name}`}
        description={`Produces ${bom.sku} — ${bom.item_name}, in ${bom.uom}.${bom.description ? ` ${bom.description}` : ""}`}
        actions={
          <div className="flex items-center gap-2">
            {bom.is_active ? <StatusBadge status="active" /> : <StatusBadge status="inactive" />}
            <Can permission="operations.manufacturing.bom.write" scope="ORG">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New version
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="flex flex-col gap-4 p-2 sm:p-4">
        <div className="px-2 pt-2 sm:px-0 sm:pt-0">
          <SectionHeader
            title="Versions"
            subtitle={
              bom.current_version_no === null
                ? "Nothing is published yet, so no production order can be planned from this BOM."
                : `Version ${bom.current_version_no} is the one in force today.`
            }
          />
        </div>
        <DataTable
          columns={columns}
          rows={bom.versions}
          rowKey={(v) => v.id}
          caption="Versions of this BOM"
          emptyState={
            <EmptyState
              icon={<NetworkIcon className="size-6" />}
              title="No versions yet"
              description="Start a new version to define the components."
            />
          }
        />
      </Card>

      {creating ? (
        <Can permission="operations.manufacturing.bom.write" scope="ORG">
          <BomVersionDialog
            bom={bom}
            onClose={() => setCreating(false)}
            onCreated={(version) => {
              setCreating(false);
              navigate(`/manufacturing/boms/${bom.id}/versions/${version.id}`);
            }}
          />
        </Can>
      ) : null}
    </div>
  );
}
