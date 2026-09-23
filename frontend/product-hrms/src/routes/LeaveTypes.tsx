import { useState } from "react";

import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ClipboardListIcon,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  RowActions,
  Skeleton,
  Tag,
  useToast,
} from "@opsgrids/ui";
import type { Column } from "@opsgrids/ui";

import { deleteLeaveType, listLeaveTypes } from "@/api/leave";
import type { LeaveLedgerEntryOut, LeaveTypeOut } from "@/api/types";
import { GrantLeaveBalanceDialog } from "@/ui/GrantLeaveBalanceDialog";
import { LeaveTypeForm } from "@/ui/LeaveTypeForm";

function policySummary(t: LeaveTypeOut): string {
  const parts: string[] = [];
  if (t.annual_quota_half_days != null) parts.push(`${t.annual_quota_half_days / 2} days/year`);
  if (t.monthly_accrual_half_days != null) {
    parts.push(`accrues ${t.monthly_accrual_half_days / 2} days/month`);
  }
  if (t.carry_forward_cap_half_days != null) {
    parts.push(`carries forward up to ${t.carry_forward_cap_half_days / 2} days`);
  }
  return parts.length > 0 ? parts.join(" · ") : "No fixed quota";
}

function LeaveTypesScreen() {
  const toast = useToast();
  const canWrite = useCan("hrms.time.leave_type.write", "ORG");
  const canGrant = useCan("hrms.time.leave_balance.write", "ORG");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<LeaveTypeOut | null>(null);
  const [retiring, setRetiring] = useState<LeaveTypeOut | null>(null);
  const [granting, setGranting] = useState(false);

  const leaveTypes = useAsync(() => listLeaveTypes({ size: 100 }), []);

  const retire = useMutation((leaveType: LeaveTypeOut) => deleteLeaveType(leaveType.id), {
    onSuccess: (_result, leaveType) => {
      setRetiring(null);
      toast.success(`${leaveType.name} retired.`);
      leaveTypes.reload();
    },
  });

  const columns: Column<LeaveTypeOut>[] = [
    {
      key: "code",
      header: "Code",
      render: (t) => <span className="tnum font-semibold">{t.code}</span>,
    },
    { key: "name", header: "Name", render: (t) => t.name },
    {
      key: "paid",
      header: "Paid",
      render: (t) => <Tag tone={t.is_paid ? "positive" : "neutral"}>{t.is_paid ? "Paid" : "Unpaid"}</Tag>,
    },
    {
      key: "policy",
      header: "Policy",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (t) => <span className="text-sm text-muted">{policySummary(t)}</span>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (t) => (
        <RowActions
          label={t.name}
          actions={[
            { label: "Edit", onSelect: () => setEditing(t), hidden: !canWrite },
            { label: "Retire", tone: "danger", onSelect: () => setRetiring(t), hidden: !canWrite },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Leave"
        title="Leave types"
        actions={
          <div className="flex items-center gap-2">
            {canGrant ? (
              <Button variant="outline" onClick={() => setGranting(true)}>
                Grant balance
              </Button>
            ) : null}
            <Can permission="hrms.time.leave_type.write" scope="ORG">
              <Button variant="dark" onClick={() => setCreating(true)}>
                New leave type
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="p-2 sm:p-4 flex flex-col gap-4">
        {leaveTypes.status === "loading" ? (
          <div className="flex flex-col gap-3 p-2">
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {leaveTypes.status === "error" ? (
          <ErrorState
            title="Couldn't load leave types"
            message={problemMessage(leaveTypes.error)}
            onRetry={leaveTypes.reload}
          />
        ) : null}
        {leaveTypes.status === "success" ? (
          <DataTable
            caption="Leave types"
            columns={columns}
            rows={leaveTypes.data.items}
            rowKey={(t) => t.id}
            emptyState={
              <EmptyState
                icon={<ClipboardListIcon className="size-6" />}
                title="No leave types yet"
                description="Define at least one before employees can apply for leave."
                action={
                  <Can permission="hrms.time.leave_type.write" scope="ORG">
                    <Button onClick={() => setCreating(true)}>New leave type</Button>
                  </Can>
                }
              />
            }
          />
        ) : null}
      </Card>

      {creating ? (
        <LeaveTypeForm
          leaveType={null}
          onClose={() => setCreating(false)}
          onSaved={(t) => { toast.success(`${t.name} created.`); leaveTypes.reload(); }}
        />
      ) : null}
      {editing ? (
        <LeaveTypeForm
          leaveType={editing}
          onClose={() => setEditing(null)}
          onSaved={(t) => { toast.success(`${t.name} saved.`); leaveTypes.reload(); }}
        />
      ) : null}
      {granting && leaveTypes.status === "success" ? (
        <GrantLeaveBalanceDialog
          leaveTypes={leaveTypes.data.items}
          onClose={() => setGranting(false)}
          onSaved={(entry: LeaveLedgerEntryOut) => {
            toast.success(`Granted ${entry.delta_half_days} half-day(s).`);
          }}
        />
      ) : null}
      <ConfirmDialog
        open={retiring !== null}
        onClose={() => { setRetiring(null); retire.reset(); }}
        onConfirm={() => retiring && void retire.mutate(retiring)}
        title="Retire this leave type?"
        body={`${retiring?.name} (${retiring?.code}) can no longer be applied for. Existing requests and balances are unaffected.`}
        confirmLabel="Retire leave type"
        pendingLabel="Retiring…"
        pending={retire.pending}
        {...(retire.error ? { error: problemMessage(retire.error) } : {})}
      />
    </div>
  );
}

export function LeaveTypesPage() {
  return <LeaveTypesScreen />;
}
