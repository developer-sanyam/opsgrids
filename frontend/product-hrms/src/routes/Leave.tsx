import { useCallback, useEffect, useState } from "react";

import { Can, useCan } from "@opsgrids/client";
import {
  Announcer,
  Button,
  Card,
  ClipboardListIcon,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  RowActions,
  SectionHeader,
  Skeleton,
  Tag,
  useAnnounce,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { listLeaveBalances, listLeaveRequests } from "@/api/leave";
import type { LeaveBalanceOut, LeaveRequestOut } from "@/api/types";
import { formatHalfDays, formatLeaveRange, leaveStatusCopy } from "@/lib/leaveCopy";
import { getMyEmployeeId } from "@/lib/subject";
import { ApplyLeaveDialog } from "@/ui/ApplyLeaveDialog";
import { CancelLeaveDialog } from "@/ui/CancelLeaveDialog";

type LoadState =
  | { status: "loading" }
  | { status: "error"; error: Error }
  | { status: "success" };

function BalanceCards({ balances }: { balances: LeaveBalanceOut[] }) {
  if (balances.length === 0) {
    return (
      <Card className="p-6">
        <EmptyState
          title="No leave types defined yet"
          description="Ask an HR administrator to set up leave types before applying."
        />
      </Card>
    );
  }
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
      {balances.map((b) => (
        <Card key={b.leave_type_id} className="p-5 flex flex-col gap-1">
          <span className="text-xs font-semibold uppercase tracking-wide text-faint">
            {b.leave_type_code}
          </span>
          <span className="text-2xl font-semibold text-ink tnum">
            {(b.balance_half_days / 2).toLocaleString("en-US", { maximumFractionDigits: 1 })}{" "}
            <span className="text-sm font-normal text-muted">days</span>
          </span>
          <span className="text-xs text-muted tnum">{b.balance_half_days} half-days</span>
        </Card>
      ))}
    </div>
  );
}

function MyLeaveScreen() {
  const announce = useAnnounce();
  const myEmployeeId = getMyEmployeeId();
  const canWriteOwn = useCan("hrms.time.leave_request.write", "OWN");

  const [balances, setBalances] = useState<LeaveBalanceOut[]>([]);
  const [requests, setRequests] = useState<LeaveRequestOut[]>([]);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [applying, setApplying] = useState(false);
  const [cancelling, setCancelling] = useState<LeaveRequestOut | null>(null);

  const load = useCallback(async () => {
    if (!myEmployeeId) {
      setState({ status: "success" });
      return;
    }
    setState({ status: "loading" });
    try {
      const [balancePage, requestPage] = await Promise.all([
        listLeaveBalances(myEmployeeId),
        listLeaveRequests({ employeeId: myEmployeeId, size: 50 }),
      ]);
      setBalances(balancePage);
      setRequests(requestPage.items);
      setState({ status: "success" });
    } catch (err) {
      setState({ status: "error", error: err instanceof Error ? err : new Error(String(err)) });
    }
  }, [myEmployeeId]);

  useEffect(() => {
    void load();
  }, [load]);

  const columns: Column<LeaveRequestOut>[] = [
    {
      key: "dates",
      header: "Dates",
      mobile: "title",
      render: (r) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink tnum">
            {formatLeaveRange(r.start_date, r.end_date)}
          </span>
          <span className="text-xs text-muted">{formatHalfDays(r.total_half_days)}</span>
        </div>
      ),
    },
    {
      key: "reason",
      header: "Reason",
      cellClassName: "hidden md:table-cell max-w-64",
      headerClassName: "hidden md:table-cell",
      render: (r) => <span className="truncate text-sm text-ink-soft">{r.reason}</span>,
    },
    {
      key: "status",
      header: "Status",
      mobile: "status",
      render: (r) => {
        const copy = leaveStatusCopy(r.status);
        return (
          <span title={r.failure_reason ?? undefined}>
            <Tag tone={copy.tone}>{copy.label}</Tag>
          </span>
        );
      },
    },
    {
      key: "actions",
      header: "",
      mobile: "actions",
      render: (r) => (
        <RowActions
          label={`leave request for ${formatLeaveRange(r.start_date, r.end_date)}`}
          actions={[
            {
              label: "Cancel",
              tone: "danger",
              onSelect: () => setCancelling(r),
              hidden: !canWriteOwn || (r.status !== "pending" && r.status !== "approved"),
            },
          ]}
        />
      ),
    },
  ];

  if (!myEmployeeId) {
    return (
      <div className="rise flex flex-col gap-6">
        <PageHeader eyebrow="Leave" title="Your leave" />
        <Card className="p-6">
          <EmptyState
            title="No employee record linked to your account"
            description="Ask an administrator to link your membership to an employee record before applying for leave."
          />
        </Card>
      </div>
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Leave"
        title="Your leave"
        actions={
          <Can permission="hrms.time.leave_request.write" scope="OWN">
            <Button variant="dark" onClick={() => setApplying(true)}>
              Apply for leave
            </Button>
          </Can>
        }
      />

      {state.status === "loading" ? (
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <Skeleton key={i} className="h-24 w-full rounded-tile" />
          ))}
        </div>
      ) : null}

      {state.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={state.error.message} onRetry={() => void load()} />
        </Card>
      ) : null}

      {state.status === "success" ? (
        <>
          <BalanceCards balances={balances} />

          <Card className="p-2 sm:p-4 flex flex-col gap-4">
            <div className="px-2 sm:px-0">
              <SectionHeader title="Your requests" />
            </div>
            <DataTable
              caption="Leave requests you raised"
              columns={columns}
              rows={requests}
              rowKey={(r) => r.id}
              mobileCard="auto"
              emptyState={
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title="You haven't applied for leave yet"
                  description="Apply for leave to see it listed here, along with its approval status."
                  action={
                    <Can permission="hrms.time.leave_request.write" scope="OWN">
                      <Button variant="dark" onClick={() => setApplying(true)}>
                        Apply for leave
                      </Button>
                    </Can>
                  }
                />
              }
            />
          </Card>
        </>
      ) : null}

      {applying ? (
        <ApplyLeaveDialog
          onClose={() => setApplying(false)}
          onSaved={(saved) => {
            setRequests((prev) => [saved, ...prev]);
            announce(`Applied for leave from ${formatLeaveRange(saved.start_date, saved.end_date)}.`);
          }}
        />
      ) : null}

      {cancelling ? (
        <CancelLeaveDialog
          open
          onClose={() => setCancelling(null)}
          request={cancelling}
          onCancelled={(updated) => {
            setRequests((prev) => prev.map((r) => (r.id === updated.id ? updated : r)));
            setCancelling(null);
            void load();
            announce("Leave request cancelled.");
          }}
        />
      ) : null}
    </div>
  );
}

export function LeavePage() {
  return (
    <Announcer>
      <MyLeaveScreen />
    </Announcer>
  );
}
