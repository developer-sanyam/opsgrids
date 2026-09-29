import { useState } from "react";

import {
  insightBacklog,
  insightBottlenecks,
  insightDiscrepancyTrend,
  insightWorkload,
  listAlerts,
  resolveAlert,
} from "@/api/client";
import type { AlertKind, OperationalAlertOut } from "@/api/types";
import { formatDate } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  Skeleton,
} from "@/ui";
import type { Column } from "@/ui";
import { AlertTriangleIcon, useToast } from "@opsgrids/ui";

const KIND_FILTERS: { value: AlertKind | "all"; label: string }[] = [
  { value: "all", label: "All" },
  { value: "low_stock", label: "Low stock" },
  { value: "task_delayed", label: "Delayed tasks" },
  { value: "shipment_delayed", label: "Delayed shipments" },
];

const KIND_LABEL: Record<AlertKind, string> = {
  low_stock: "Low stock",
  task_delayed: "Delayed task",
  shipment_delayed: "Delayed shipment",
};

function summarize(alert: OperationalAlertOut): string {
  const d = alert.detail;
  if (alert.kind === "low_stock") {
    return `${String(d.name ?? d.sku ?? "Item")} — ${String(d.on_hand ?? "?")} on hand, reorder at ${String(d.reorder_point ?? "?")}`;
  }
  if (alert.kind === "task_delayed") {
    return `Task ${String(d.task_id ?? "")} — ${String(d.status ?? "")}, ${Math.round(Number(d.age_minutes ?? 0))}m open (SLA ${String(d.sla_minutes ?? "?")}m)`;
  }
  return `Shipment ${String(d.shipment_id ?? "")} — ${String(d.status ?? "")}, ${Math.round(Number(d.age_minutes ?? 0))}m open (SLA ${String(d.sla_minutes ?? "?")}m)`;
}

/**
 * Operational alerts (low stock, delayed pick tasks, delayed shipments — fulfilment plan §4;
 * F12) and the insight dashboards computed from the same real tables. Every automation these
 * alerts can drive (wait, condition, email/WhatsApp) is authored in Engage's Automations screen
 * against these same events, once `operations.*` triggers are visible there — this screen is
 * where an operations manager sees what fired and why, not a second graph builder.
 */
export function AutomationPage() {
  const toast = useToast();
  const [kind, setKind] = useState<AlertKind | "all">("all");
  const [includeResolved, setIncludeResolved] = useState(false);
  const [page, setPage] = useState(1);
  const canManage = useCan("operations.automation.alert.manage", "ORG");

  const alertsState = useAsync(
    () =>
      listAlerts({
        kind: kind === "all" ? undefined : kind,
        include_resolved: includeResolved,
        page,
        size: 20,
      }),
    [kind, includeResolved, page],
  );
  const backlogState = useAsync(() => insightBacklog(), []);
  const bottlenecksState = useAsync(() => insightBottlenecks(7), []);
  const workloadState = useAsync(() => insightWorkload(), []);
  const discrepancyState = useAsync(() => insightDiscrepancyTrend(30), []);

  const resolve = useMutation((alert: OperationalAlertOut) => resolveAlert(alert.id), {
    onSuccess: (_result, alert) => {
      toast.success(`${KIND_LABEL[alert.kind]} alert dismissed.`);
      alertsState.reload();
    },
  });

  const columns: Column<OperationalAlertOut>[] = [
    {
      key: "kind",
      header: "Kind",
      render: (a) => <span className="font-semibold text-ink">{KIND_LABEL[a.kind]}</span>,
    },
    {
      key: "detail",
      header: "What's happening",
      render: (a) => <span className="text-ink-soft">{summarize(a)}</span>,
    },
    {
      key: "detected_at",
      header: "First seen",
      align: "right",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (a) => <span className="tnum text-muted whitespace-nowrap">{formatDate(a.detected_at)}</span>,
    },
    {
      key: "status",
      header: "Status",
      align: "right",
      render: (a) =>
        a.resolved_at ? (
          <span className="text-faint text-xs">Resolved {formatDate(a.resolved_at)}</span>
        ) : (
          <span className="text-late text-xs font-semibold uppercase tracking-wide">Open</span>
        ),
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (a) =>
        a.resolved_at ? null : (
          <RowActions
            label={KIND_LABEL[a.kind]}
            actions={[
              {
                label: "Dismiss",
                onSelect: () => void resolve.mutate(a),
                hidden: !canManage,
              },
            ]}
          />
        ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader eyebrow="Automation" title="Operational alerts" />

      <Card className="p-4 sm:p-6 flex flex-col gap-4">
        <div className="flex items-center gap-2 overflow-x-auto -mx-1 px-1">
          {KIND_FILTERS.map((f) => (
            <Pill
              key={f.value}
              active={kind === f.value}
              onClick={() => {
                setKind(f.value);
                setPage(1);
              }}
            >
              {f.label}
            </Pill>
          ))}
          <Pill
            active={includeResolved}
            onClick={() => {
              setIncludeResolved(!includeResolved);
              setPage(1);
            }}
          >
            {includeResolved ? "Hide resolved" : "Show resolved"}
          </Pill>
        </div>
      </Card>

      <Card className="p-2 sm:p-4">
        {alertsState.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {alertsState.status === "error" ? (
          <ErrorState
            title="Couldn't load alerts"
            message={problemMessage(alertsState.error)}
            onRetry={alertsState.reload}
          />
        ) : null}
        {alertsState.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={alertsState.data.items}
              rowKey={(a) => a.id}
              caption="Operational alerts"
              mobileCard="auto"
              emptyState={
                <EmptyState
                  icon={<AlertTriangleIcon className="size-6" />}
                  title="Nothing needs attention"
                  description="Low stock, delayed pick tasks and delayed shipments will show up here as the sweep finds them."
                />
              }
            />
            <Pagination
              page={alertsState.data.page}
              pages={alertsState.data.pages}
              total={alertsState.data.total}
              size={alertsState.data.size}
              noun="alerts"
              onPageChange={setPage}
              onSizeChange={() => {}}
            />
          </>
        ) : null}
      </Card>

      <Can permission="operations.automation.insight.read" scope="ORG">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          <Card className="p-4 sm:p-6 flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-ink">Task backlog, by warehouse</h2>
            {backlogState.status === "success" ? (
              backlogState.data.length === 0 ? (
                <p className="text-sm text-faint">No open tasks.</p>
              ) : (
                <table className="w-full text-sm">
                  <tbody>
                    {backlogState.data.map((r) => (
                      <tr key={r.warehouse_id} className="border-b border-line last:border-0">
                        <td className="py-2 text-ink-soft">{r.warehouse_id}</td>
                        <td className="py-2 text-right tnum">{r.open_tasks} open</td>
                        <td className="py-2 text-right tnum text-muted">
                          avg {r.avg_age_minutes.toFixed(0)}m, max {r.max_age_minutes.toFixed(0)}m
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            ) : (
              <Skeleton className="h-24 w-full rounded-tile" />
            )}
          </Card>

          <Card className="p-4 sm:p-6 flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-ink">Slowest zones (last 7 days)</h2>
            {bottlenecksState.status === "success" ? (
              bottlenecksState.data.length === 0 ? (
                <p className="text-sm text-faint">Nothing closed in this window yet.</p>
              ) : (
                <table className="w-full text-sm">
                  <tbody>
                    {bottlenecksState.data.map((r) => (
                      <tr key={r.zone_location_id} className="border-b border-line last:border-0">
                        <td className="py-2 text-ink-soft">{r.zone_location_id}</td>
                        <td className="py-2 text-right tnum">{r.closed_tasks} closed</td>
                        <td className="py-2 text-right tnum text-muted">
                          avg {r.avg_cycle_minutes.toFixed(0)}m
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            ) : (
              <Skeleton className="h-24 w-full rounded-tile" />
            )}
          </Card>

          <Card className="p-4 sm:p-6 flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-ink">Worker workload</h2>
            {workloadState.status === "success" ? (
              workloadState.data.length === 0 ? (
                <p className="text-sm text-faint">Nobody is holding an open task.</p>
              ) : (
                <table className="w-full text-sm">
                  <tbody>
                    {workloadState.data.map((r) => (
                      <tr key={r.worker_id} className="border-b border-line last:border-0">
                        <td className="py-2 text-ink-soft">{r.worker_name}</td>
                        <td className="py-2 text-right tnum">{r.open_tasks} open</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            ) : (
              <Skeleton className="h-24 w-full rounded-tile" />
            )}
          </Card>

          <Card className="p-4 sm:p-6 flex flex-col gap-3">
            <h2 className="text-sm font-semibold text-ink">Inventory discrepancies (last 30 days)</h2>
            {discrepancyState.status === "success" ? (
              discrepancyState.data.length === 0 ? (
                <p className="text-sm text-faint">No discrepancies found by the reconciler.</p>
              ) : (
                <table className="w-full text-sm">
                  <tbody>
                    {discrepancyState.data.map((r) => (
                      <tr key={`${r.date}-${r.kind}`} className="border-b border-line last:border-0">
                        <td className="py-2 text-ink-soft">{r.date}</td>
                        <td className="py-2 text-ink-soft">{r.kind}</td>
                        <td className="py-2 text-right tnum">{r.count}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )
            ) : (
              <Skeleton className="h-24 w-full rounded-tile" />
            )}
          </Card>
        </div>
      </Can>
    </div>
  );
}
