import { useState } from "react";

import { listWarehouses, listWorkers, updateWorker } from "@/api/client";
import type { WorkerOut } from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { formatDate } from "@/lib/format";
import { WorkerFormDialog } from "@/forms/WorkerFormDialog";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Pill,
  Skeleton,
  Switch,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { PlusIcon, UsersIcon } from "@opsgrids/ui";

type ActiveFilter = "all" | "active" | "inactive";

/**
 * The worker roster (plan F3): who can be offered pick/pack work, at which warehouse, and
 * whether they are online right now. Presence here is read-only raw status — the plan explicitly
 * keeps live position/map display out of F3.
 */
export function FulfilmentWorkersPage() {
  const canWrite = useCan("operations.fulfilment.worker.write", "ORG");
  const [warehouseId, setWarehouseId] = useState<string>("all");
  const [activeFilter, setActiveFilter] = useState<ActiveFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<WorkerOut | null>(null);

  const warehousesState = useAsync(() => listWarehouses({ size: 200 }), []);
  const warehouseName = new Map(
    warehousesState.status === "success" ? warehousesState.data.items.map((w) => [w.id, w.name]) : [],
  );

  const loader = () =>
    listWorkers({
      ...(warehouseId !== "all" ? { warehouse_id: warehouseId } : {}),
      ...(activeFilter !== "all" ? { is_active: activeFilter === "active" } : {}),
      page,
      size,
    });
  const state = useAsync(loader, [warehouseId, activeFilter, page, size]);

  const setActive = useMutation(
    (input: { worker: WorkerOut; isActive: boolean }) =>
      updateWorker(input.worker.id, { is_active: input.isActive }),
    { onSuccess: () => state.reload() },
  );

  const columns: Column<WorkerOut>[] = [
    {
      key: "name",
      header: "Worker",
      mobile: "title",
      render: (w) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{w.display_name || "Unnamed worker"}</span>
          <span className="text-xs text-faint tnum">{w.employee_id}</span>
        </div>
      ),
    },
    {
      key: "warehouse",
      header: "Warehouse",
      render: (w) => <span className="text-ink-soft">{warehouseName.get(w.warehouse_id) ?? "—"}</span>,
    },
    {
      key: "skills",
      header: "Skills",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (w) =>
        w.skills.length > 0 ? (
          <div className="flex flex-wrap gap-1">
            {w.skills.map((skill) => (
              <Tag key={skill}>{skill}</Tag>
            ))}
          </div>
        ) : (
          <span className="text-faint">—</span>
        ),
    },
    {
      key: "presence",
      header: "Presence",
      mobile: "status",
      render: (w) => (
        <div className="flex flex-col leading-tight">
          <span
            className={
              w.presence_status === "online" ? "font-semibold text-present" : "font-semibold text-faint"
            }
          >
            {w.presence_status === "online" ? "Online" : "Offline"}
          </span>
          {w.presence_last_seen_at ? (
            <span className="text-xs text-faint tnum">Last seen {formatDate(w.presence_last_seen_at.slice(0, 10))}</span>
          ) : null}
        </div>
      ),
    },
    {
      key: "active",
      header: "Active",
      align: "right",
      render: (w) => (
        <Switch
          checked={w.is_active}
          onChange={(checked) => void setActive.mutate({ worker: w, isActive: checked })}
          label={`${w.display_name || "Worker"} active`}
          className="justify-end"
          disabled={!canWrite || setActive.pending}
        />
      ),
    },
    {
      key: "edit",
      header: "",
      mobile: "actions",
      align: "right",
      render: (w) =>
        canWrite ? (
          <Button size="sm" variant="outline" onClick={() => setEditing(w)}>
            Edit
          </Button>
        ) : null,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Fulfilment"
        title="Workers"
        description="Who can be offered pick and pack work, at which warehouse, and whether they're online now."
        actions={
          <Can permission="operations.fulfilment.worker.write" scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New worker
            </Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
          <Pill active={warehouseId === "all"} onClick={() => setWarehouseId("all")}>
            Every warehouse
          </Pill>
          {warehousesState.status === "success"
            ? warehousesState.data.items.map((w) => (
                <Pill key={w.id} active={warehouseId === w.id} onClick={() => setWarehouseId(w.id)}>
                  {w.name}
                </Pill>
              ))
            : null}
        </div>
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
          {(["all", "active", "inactive"] as const).map((f) => (
            <Pill
              key={f}
              active={activeFilter === f}
              onClick={() => {
                setActiveFilter(f);
                setPage(1);
              }}
            >
              {f === "all" ? "All" : f === "active" ? "Active" : "Inactive"}
            </Pill>
          ))}
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load workers" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(w) => w.id}
              caption="Workers"
              emptyState={
                <EmptyState
                  icon={<UsersIcon className="size-6" />}
                  title="No workers yet"
                  description="Add the first picker or packer to start offering tasks."
                  action={
                    <Can permission="operations.fulfilment.worker.write" scope="ORG">
                      <Button size="sm" onClick={() => setCreating(true)}>
                        New worker
                      </Button>
                    </Can>
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="workers"
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
        <WorkerFormDialog
          worker={null}
          onClose={() => setCreating(false)}
          onSaved={() => {
            setCreating(false);
            state.reload();
          }}
        />
      ) : null}

      {editing ? (
        <WorkerFormDialog
          worker={editing}
          onClose={() => setEditing(null)}
          onSaved={() => {
            setEditing(null);
            state.reload();
          }}
        />
      ) : null}
    </div>
  );
}
