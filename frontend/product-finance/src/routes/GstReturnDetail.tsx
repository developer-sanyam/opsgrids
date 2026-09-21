import { useCallback, useState } from "react";
import { Link, useParams } from "react-router-dom";

import { fileReturn, generateReturn, getReturn, getReturnDiff } from "@/api/client";
import type { GstReturn } from "@/api/types";
import { formatDateTime } from "@/lib/format";
import { formatMoney } from "@/lib/money";
import { Can, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  ErrorState,
  InlineAlert,
  SectionHeader,
  Skeleton,
  Tag,
  TextArea,
  TextField,
  Timeline,
} from "@/ui";
import type { TimelineEntry } from "@/ui";
import { ArrowLeftIcon, useToast } from "@opsgrids/ui";

const STATUS_TONE: Record<string, string> = {
  draft: "bg-ground text-muted",
  generated: "bg-info-soft text-info",
  filed: "bg-present-soft text-present",
};

function TotalsGrid({ ret }: { ret: GstReturn }) {
  return (
    <dl className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs font-semibold uppercase tracking-wide text-faint">Taxable</dt>
        <dd className="money text-sm font-semibold text-ink">{formatMoney(ret.total_taxable_minor)}</dd>
      </div>
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs font-semibold uppercase tracking-wide text-faint">CGST</dt>
        <dd className="money text-sm font-semibold text-ink">{formatMoney(ret.total_cgst_minor)}</dd>
      </div>
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs font-semibold uppercase tracking-wide text-faint">SGST</dt>
        <dd className="money text-sm font-semibold text-ink">{formatMoney(ret.total_sgst_minor)}</dd>
      </div>
      <div className="flex flex-col gap-0.5">
        <dt className="text-xs font-semibold uppercase tracking-wide text-faint">IGST</dt>
        <dd className="money text-sm font-semibold text-ink">{formatMoney(ret.total_igst_minor)}</dd>
      </div>
    </dl>
  );
}

function FileReturnDialog({
  ret,
  onClose,
  onFiled,
}: {
  ret: GstReturn;
  onClose: () => void;
  onFiled: (ret: GstReturn) => void;
}) {
  const [arn, setArn] = useState("");
  const [note, setNote] = useState("");
  const [arnError, setArnError] = useState<string | undefined>(undefined);

  const file = useMutation((input: { arn: string; note?: string | null }) => fileReturn(ret.id, input), {
    onSuccess: (updated) => onFiled(updated),
  });

  function confirm() {
    if (arn.trim() === "") {
      setArnError("Enter the portal's Acknowledgement Reference Number.");
      return;
    }
    void file.mutate({ arn: arn.trim(), note: note.trim() === "" ? null : note.trim() });
  }

  return (
    <ConfirmDialog
      open
      onClose={onClose}
      onConfirm={confirm}
      title={`File ${ret.period} ${ret.return_type.toUpperCase()}?`}
      body={`This is terminal: once filed, ${ret.period} can never be regenerated or un-filed. The remedy for a later correction is an amendment in a future period, prepared from the diff.`}
      confirmLabel="Mark as filed"
      pendingLabel="Filing…"
      tone="danger"
      pending={file.pending}
      {...(file.error ? { error: problemMessage(file.error) } : {})}
    >
      <div className="flex flex-col gap-4 pt-2">
        <TextField
          label="ARN"
          value={arn}
          onChange={(e) => setArn(e.target.value)}
          helper="The portal's Acknowledgement Reference Number."
          error={arnError}
        />
        <TextArea label="Note" value={note} onChange={(e) => setNote(e.target.value)} optional rows={2} />
      </div>
    </ConfirmDialog>
  );
}

export function GstReturnDetailPage() {
  const { id = "" } = useParams();
  const toast = useToast();
  const loader = useCallback(() => getReturn(id), [id]);
  const detail = useAsync(loader, [id]);
  const [filing, setFiling] = useState(false);
  const [showDiff, setShowDiff] = useState(false);

  const diff = useAsync(
    () => (showDiff ? getReturnDiff(id) : Promise.resolve(null)),
    [id, showDiff],
  );

  const regenerate = useMutation(
    (input: { period: string; return_type: "gstr1" | "gstr7"; gst_registration_id: string }) =>
      generateReturn(input),
    { onSuccess: () => detail.reload() },
  );

  return (
    <div className="rise flex flex-col gap-6">
      <Link
        to="/compliance/returns"
        className="inline-flex items-center gap-1.5 text-sm font-semibold text-muted hover:text-ink transition-colors self-start"
      >
        <ArrowLeftIcon className="size-4" />
        Back to GST returns
      </Link>

      {detail.status === "loading" ? <Skeleton className="h-64 w-full rounded-tile" /> : null}
      {detail.status === "error" ? (
        <Card className="p-6">
          <ErrorState message={problemMessage(detail.error)} onRetry={detail.reload} />
        </Card>
      ) : null}

      {detail.status === "success" ? (
        <>
          <Card className="flex flex-col gap-5 p-6 lg:p-8">
            <div className="flex flex-wrap items-start justify-between gap-4">
              <div className="flex flex-col gap-2">
                <div className="flex items-center gap-2.5">
                  <h1 className="display text-2xl font-extrabold">{detail.data.ret.period}</h1>
                  <span className={`inline-flex items-center rounded-full px-2.5 py-1 text-xs font-semibold capitalize ${STATUS_TONE[detail.data.ret.status] ?? "bg-ground text-muted"}`}>
                    {detail.data.ret.status}
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-2">
                  <Tag>{detail.data.ret.return_type.toUpperCase()}</Tag>
                  <Tag className="money">{detail.data.ret.gstin}</Tag>
                  {detail.data.ret.arn ? <Tag className="money">ARN {detail.data.ret.arn}</Tag> : null}
                </div>
              </div>
              <div className="flex flex-wrap items-center gap-2">
                {detail.data.ret.status !== "filed" ? (
                  <Can permission="finance.compliance.returns.generate" scope="ORG">
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={regenerate.pending}
                      onClick={() =>
                        void regenerate.mutate({
                          period: detail.data.ret.period,
                          return_type: detail.data.ret.return_type as "gstr1" | "gstr7",
                          gst_registration_id: detail.data.ret.gst_registration_id,
                        })
                      }
                    >
                      {regenerate.pending ? "Regenerating…" : detail.data.ret.status === "draft" ? "Generate" : "Regenerate"}
                    </Button>
                  </Can>
                ) : null}
                {detail.data.ret.status === "generated" ? (
                  <Can permission="finance.compliance.returns.file" scope="ORG">
                    <Button size="sm" onClick={() => setFiling(true)}>
                      Mark as filed
                    </Button>
                  </Can>
                ) : null}
              </div>
            </div>
            {regenerate.error ? (
              <InlineAlert tone="danger" title={problemMessage(regenerate.error)} />
            ) : null}
            <TotalsGrid ret={detail.data.ret} />
          </Card>

          <Card className="flex flex-col gap-4 p-6 lg:p-8">
            <SectionHeader
              title="What has changed since generation"
              subtitle="A credit note raised later doesn't rewrite this period — it makes a difference, for a later amendment"
              action={
                <Button variant="outline" size="sm" onClick={() => setShowDiff((v) => !v)}>
                  {showDiff ? "Hide diff" : "Show diff"}
                </Button>
              }
            />
            {showDiff && diff.status === "loading" ? <Skeleton className="h-24 w-full rounded-tile" /> : null}
            {showDiff && diff.status === "error" ? (
              <ErrorState message={problemMessage(diff.error)} onRetry={diff.reload} />
            ) : null}
            {showDiff && diff.status === "success" && diff.data ? (
              diff.data.changed ? (
                <InlineAlert tone="warning" title="The book has moved since this return was generated">
                  Current taxable value is {formatMoney(diff.data.current.taxable_minor)} against a
                  generated {formatMoney(diff.data.snapshot.taxable_minor)} — a difference of{" "}
                  {formatMoney(diff.data.delta.taxable_minor)}.
                </InlineAlert>
              ) : (
                <InlineAlert tone="success" title="Nothing has changed since this return was generated" />
              )
            ) : null}
          </Card>

          <Card className="flex flex-col gap-4 p-6 lg:p-8">
            <SectionHeader title="Status trail" subtitle="Append-only — every transition, oldest first" />
            <Timeline
              entries={detail.data.transitions.map(
                (t): TimelineEntry => ({
                  id: t.id,
                  content: (
                    <span>
                      Moved to <span className="font-semibold capitalize">{t.to_status}</span> by{" "}
                      {t.transitioned_by}
                      {t.arn ? <span className="money"> · ARN {t.arn}</span> : null}
                    </span>
                  ),
                  timestamp: formatDateTime(t.transitioned_at),
                  dateTime: t.transitioned_at,
                  ...(t.note ? { quote: t.note } : {}),
                  tone: t.to_status === "filed" ? "positive" : "info",
                }),
              )}
              emptyMessage="No transitions yet."
            />
          </Card>
        </>
      ) : null}

      {filing && detail.status === "success" ? (
        <FileReturnDialog
          ret={detail.data.ret}
          onClose={() => setFiling(false)}
          onFiled={(ret) => {
            setFiling(false);
            toast.success(`${ret.period} ${ret.return_type.toUpperCase()} filed.`);
            detail.reload();
          }}
        />
      ) : null}
    </div>
  );
}
