import { useCallback, useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { deleteRequest, decideRequest, fulfilRequest, getRequest, updateRequest } from "@/api/client";
import type { ServiceRequest } from "@/api/types";
import { catalogCategoryLabel, formatDateTime } from "@/lib/format";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { Card, ErrorState, RequestStatusPill, SectionHeader, Skeleton } from "@/ui";
import {
  ArrowLeftIcon,
  Button,
  ConfirmDialog,
  InlineAlert,
  TextField,
  useToast,
} from "@opsgrids/ui";
import { RejectRequestDialog } from "@/forms/RejectRequestDialog";

const CAN_WRITE = "itsm.service.request.write";

function PropertyRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col items-start gap-1 py-2.5 sm:flex-row sm:items-center sm:justify-between sm:gap-3">
      <span className="text-sm text-muted">{label}</span>
      <span className="w-full text-sm font-semibold text-ink sm:w-auto sm:text-right">{children}</span>
    </div>
  );
}

function AssigneeControl({
  request,
  canWriteOrg,
  onChanged,
}: {
  request: ServiceRequest;
  canWriteOrg: boolean;
  onChanged: (request: ServiceRequest) => void;
}) {
  const toast = useToast();
  const [value, setValue] = useState(request.assignee ?? "");
  const change = useMutation(
    (assignee: string) => updateRequest(request.id, { assignee: assignee === "" ? null : assignee }),
    {
      onSuccess: (updated) => {
        onChanged(updated);
        toast.success(updated.assignee ? `Assigned to ${updated.assignee}.` : "Unassigned.");
      },
    },
  );

  if (!canWriteOrg) {
    return <span className="text-sm font-medium text-ink">{request.assignee ?? "Unassigned"}</span>;
  }

  return (
    <form
      className="flex w-full flex-col gap-1.5 sm:w-44 sm:items-end"
      onSubmit={(e) => {
        e.preventDefault();
        if (value.trim() !== (request.assignee ?? "")) void change.mutate(value.trim());
      }}
    >
      <TextField
        label="Assignee"
        hideLabel
        containerClassName="w-full"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onBlur={() => {
          if (value.trim() !== (request.assignee ?? "")) void change.mutate(value.trim());
        }}
        disabled={change.pending}
        maxLength={120}
        placeholder="Unassigned"
      />
      {change.error ? (
        <InlineAlert tone="danger" title={problemMessage(change.error)} className="w-full" />
      ) : null}
    </form>
  );
}

export function RequestDetailPage() {
  const { id = "" } = useParams();
  const navigate = useNavigate();
  const toast = useToast();
  const canWriteOrg = useCan(CAN_WRITE, "ORG");

  const loader = useCallback(() => getRequest(id), [id]);
  const state = useAsync(loader, [id]);
  const [override, setOverride] = useState<ServiceRequest | null>(null);
  useEffect(() => setOverride(null), [id]);
  const request = override ?? (state.status === "success" ? state.data : null);

  const [rejecting, setRejecting] = useState(false);
  const [cancelling, setCancelling] = useState(false);

  const approve = useMutation<void, ServiceRequest>(
    () => decideRequest(request?.id ?? "", { decision: "approve" }),
    {
      onSuccess: (updated) => {
        setOverride(updated);
        toast.success(`${updated.id} approved.`);
      },
    },
  );
  const fulfil = useMutation<void, ServiceRequest>(() => fulfilRequest(request?.id ?? ""), {
    onSuccess: (updated) => {
      setOverride(updated);
      toast.success(`${updated.id} marked fulfilled.`);
    },
  });
  const cancel = useMutation<void, void>(() => deleteRequest(request?.id ?? ""), {
    onSuccess: () => {
      toast.success(`${request?.id} cancelled.`);
      navigate("/requests");
    },
  });

  return (
    <div className="rise flex flex-col gap-5">
      <button
        type="button"
        onClick={() => navigate("/requests")}
        className="inline-flex w-fit items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors"
      >
        <ArrowLeftIcon className="size-4" />
        Back to requests
      </button>

      {state.status === "loading" && !request ? (
        <Card className="p-6">
          <Skeleton label="Loading request" className="h-8 w-2/3" />
          <Skeleton className="mt-4 h-40 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" && !request ? (
        <Card className="p-6">
          <ErrorState
            title="Couldn't load that request"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        </Card>
      ) : null}

      {request ? (
        <>
          <header className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex flex-col gap-3">
              <div className="flex flex-wrap items-center gap-2">
                <span className="tnum text-sm font-semibold text-muted">{request.id}</span>
                <RequestStatusPill status={request.status} />
              </div>
              <h1 className="display text-2xl sm:text-3xl font-extrabold leading-tight">
                {request.catalog_item_name}
              </h1>
              <p className="text-sm text-muted">{request.subject}</p>
            </div>
            <Can permission={CAN_WRITE} scope="ORG">
              <div className="flex flex-wrap items-center gap-2">
                {request.status === "approval" ? (
                  <>
                    <Button variant="outline" onClick={() => setRejecting(true)}>
                      Reject…
                    </Button>
                    <Button loading={approve.pending} onClick={() => void approve.mutate()}>
                      Approve
                    </Button>
                  </>
                ) : null}
                {request.status === "in_progress" ? (
                  <Button loading={fulfil.pending} onClick={() => void fulfil.mutate()}>
                    Mark fulfilled
                  </Button>
                ) : null}
                {request.status === "approval" || request.status === "in_progress" ? (
                  <Button variant="danger" onClick={() => setCancelling(true)}>
                    Cancel
                  </Button>
                ) : null}
              </div>
            </Can>
          </header>

          {approve.error ? <InlineAlert tone="danger" title={problemMessage(approve.error)} /> : null}
          {fulfil.error ? <InlineAlert tone="danger" title={problemMessage(fulfil.error)} /> : null}

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-3 items-start">
            <Card className="flex flex-col gap-3 p-6 xl:col-span-2">
              <SectionHeader title="Details" />
              <dl className="mt-2 divide-y divide-line">
                <PropertyRow label="Category">{catalogCategoryLabel(request.category)}</PropertyRow>
                <PropertyRow label="Requester">{request.requester}</PropertyRow>
                <PropertyRow label="Assignee">
                  <AssigneeControl request={request} canWriteOrg={canWriteOrg} onChanged={setOverride} />
                </PropertyRow>
                <PropertyRow label="Raised">
                  <span className="tnum font-medium text-muted">
                    {formatDateTime(request.created_at)}
                  </span>
                </PropertyRow>
                <PropertyRow label="Updated">
                  <span className="tnum font-medium text-muted">
                    {formatDateTime(request.updated_at)}
                  </span>
                </PropertyRow>
              </dl>
              {request.decision_note ? (
                <div className="mt-2 border-t border-line pt-4">
                  <span className="text-xs font-semibold uppercase tracking-wide text-faint">
                    Decision note
                  </span>
                  <p className="mt-1 text-sm text-ink-soft leading-relaxed whitespace-pre-line">
                    {request.decision_note}
                  </p>
                </div>
              ) : null}
            </Card>
          </div>
        </>
      ) : null}

      {rejecting && request ? (
        <RejectRequestDialog
          request={request}
          onClose={() => setRejecting(false)}
          onDecided={(updated) => {
            setRejecting(false);
            setOverride(updated);
            toast.success(`${updated.id} rejected.`);
          }}
        />
      ) : null}

      <ConfirmDialog
        open={cancelling}
        onClose={() => {
          setCancelling(false);
          cancel.reset();
        }}
        onConfirm={() => void cancel.mutate()}
        title="Cancel this request?"
        body={`${request?.id} — ${request?.subject} is withdrawn. This can't be undone.`}
        confirmLabel="Cancel request"
        pendingLabel="Cancelling…"
        pending={cancel.pending}
        error={cancel.error ? problemMessage(cancel.error) : undefined}
      />
    </div>
  );
}
