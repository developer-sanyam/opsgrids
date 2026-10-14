import { useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { getReceivingContext, postGoodsReceipt } from "@/api/client";
import type { GoodsReceiptIn, ReceivingContextOut } from "@/api/types";
import { useReceivingLocations } from "@/data/useReceivingLocations";
import { ReceiveLineEditor } from "@/forms/ReceiveLineEditor";
import { RequirePermission, problemMessage, useAsync, useMutation } from "@opsgrids/client";
import { attemptKey, newAttemptKeyring } from "@/lib/attemptKey";
import { today } from "@/lib/now";
import { buildReceipt, emptyLineDraft } from "@/lib/receiptForm";
import { serverLineProblems } from "@/lib/receiptForm";
import type { ReceiptLineDraft } from "@/lib/receiptForm";
import {
  Button,
  Card,
  ErrorState,
  FormSection,
  InlineAlert,
  LoadError,
  PageHeader,
  Skeleton,
  TextArea,
  TextField,
  UnsavedChangesGuard,
} from "@/ui";
import { ArrowRightIcon, useToast } from "@opsgrids/ui";

function BackLink({ id }: { id: string }) {
  return (
    <Link
      to={`/purchase-orders/${id}`}
      className="relative hit-44 inline-flex w-fit items-center gap-2 text-sm font-semibold text-muted hover:text-ink transition-colors"
    >
      <ArrowRightIcon className="size-4 rotate-180" />
      Back to purchase order
    </Link>
  );
}

function Denied({ id }: { id: string }) {
  return (
    <div className="rise flex flex-col gap-6">
      <BackLink id={id} />
      <Card className="p-6">
        <ErrorState
          tone="neutral"
          title="You can't receive goods"
          message="Posting a goods receipt needs both the receipt and the stock-receive permissions. Ask an administrator if you should have them."
        />
      </Card>
    </div>
  );
}

/**
 * Receive goods against a purchase order: `/purchase-orders/:id/receive`. Gated on both permissions
 * the server asks for — a UI gate only; the endpoint refuses on its own.
 */
export function ReceiveGoodsPage() {
  const { id = "" } = useParams();
  return (
    <RequirePermission perm="operations.procurement.receipt.write" scope="ORG" fallback={<Denied id={id} />}>
      <RequirePermission perm="operations.inventory.stock.receive" scope="ORG" fallback={<Denied id={id} />}>
        <ReceiveGoodsLoader id={id} />
      </RequirePermission>
    </RequirePermission>
  );
}

function ReceiveGoodsLoader({ id }: { id: string }) {
  const state = useAsync(() => getReceivingContext(id), [id]);

  if (state.status === "loading") {
    return (
      <div className="rise flex flex-col gap-6" role="status" aria-label="Loading what can be received">
        <Skeleton className="h-8 w-40 rounded-tile" />
        <Card className="p-6 flex flex-col gap-4">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-56 w-full rounded-tile" />
        </Card>
      </div>
    );
  }
  if (state.status === "error") {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink id={id} />
        <Card className="p-6">
          <LoadError error={state.error} noun="what can be received" onRetry={state.reload} />
        </Card>
      </div>
    );
  }
  const context = state.data;
  if (!context.can_receive) {
    return (
      <div className="rise flex flex-col gap-6">
        <BackLink id={id} />
        <Card className="p-6">
          <ErrorState
            tone="neutral"
            title="This purchase order can't be received"
            message={`${context.po_number} is ${context.po_status.replace(/_/g, " ")}. Only an approved or partially received order takes a delivery.`}
          />
        </Card>
      </div>
    );
  }
  return <ReceiveForm context={context} />;
}

function ReceiveForm({ context }: { context: ReceivingContextOut }) {
  const navigate = useNavigate();
  const toast = useToast();
  const locations = useReceivingLocations(context.warehouse);

  const initial = useState(() => ({
    header: { receivedOn: today(), deliveryNote: "", note: "" },
    lines: context.lines.map((l) => emptyLineDraft(l.purchase_order_line_id)),
  }))[0];
  const [header, setHeader] = useState(initial.header);
  const [lines, setLines] = useState<ReceiptLineDraft[]>(initial.lines);
  const [attempted, setAttempted] = useState(false);
  const [includedLineIds, setIncludedLineIds] = useState<string[]>([]);
  // Where to go once posted. Navigating from an effect, after `savedTo` has switched the
  // unsaved-changes guard off, keeps the guard from asking about a receipt that was just posted.
  const [savedTo, setSavedTo] = useState<string | null>(null);
  useEffect(() => {
    if (savedTo !== null) navigate(savedTo);
  }, [savedTo, navigate]);
  const keyring = useRef(newAttemptKeyring());
  const summaryRef = useRef<HTMLDivElement>(null);

  // Recomputed on every change, so the short / over hints and the roll totals are live; the
  // blocking summary below waits for the first attempt to post.
  const built = buildReceipt(context.purchase_order_id, header, lines, context.lines);
  const dirty = JSON.stringify({ header, lines }) !== JSON.stringify(initial);

  const post = useMutation(
    (payload: GoodsReceiptIn) => postGoodsReceipt(payload, attemptKey(keyring.current, payload)),
    {
      onSuccess: (receipt) => {
        toast.success(`${receipt.number} posted against ${receipt.po_number}.`);
        setSavedTo(`/goods-receipts/${receipt.id}`);
      },
    },
  );

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setAttempted(true);
    post.reset();
    setIncludedLineIds(built.includedLineIds);
    if (built.payload === null) {
      summaryRef.current?.focus();
      return;
    }
    void post.mutate(built.payload);
  }

  const server = serverLineProblems(post.fieldErrors, includedLineIds);
  const hasFieldErrors = Object.keys(post.fieldErrors).length > 0;
  const invalid = attempted && built.payload === null;
  const headerErrorFor = (field: string) => server.header[field];

  return (
    <div className="rise flex flex-col gap-6">
      <BackLink id={context.purchase_order_id} />
      <PageHeader
        eyebrow={`${context.po_number} · ${context.supplier_name}`}
        title="Receive goods"
        description={`Delivery into ${context.warehouse}. Enter what arrived on each line; leave a line blank to skip it.`}
        actions={
          <Button type="button" variant="ghost" disabled={post.pending} onClick={() => navigate(`/purchase-orders/${context.purchase_order_id}`)}>
            Cancel
          </Button>
        }
      />
      <UnsavedChangesGuard when={dirty && savedTo === null} subject="this receipt" />

      <form noValidate onSubmit={submit} className="flex flex-col gap-6">
        <Card className="p-4 sm:p-6">
          <FormSection title="Delivery" description="When it arrived, and the paperwork that came with it." headingLevel={2}>
            <TextField
              label="Received on"
              type="date"
              value={header.receivedOn}
              max={today()}
              onChange={(e) => setHeader((h) => ({ ...h, receivedOn: e.target.value }))}
              {...(headerErrorFor("received_on") !== undefined ? { error: headerErrorFor("received_on") } : {})}
            />
            <TextField
              label="Delivery note"
              value={header.deliveryNote}
              onChange={(e) => setHeader((h) => ({ ...h, deliveryNote: e.target.value }))}
              optional
              maxLength={80}
              helper="The supplier's challan or delivery note number."
              {...(headerErrorFor("delivery_note") !== undefined ? { error: headerErrorFor("delivery_note") } : {})}
            />
            <TextArea
              label="Note"
              value={header.note}
              onChange={(e) => setHeader((h) => ({ ...h, note: e.target.value }))}
              optional
              maxLength={2000}
              counterFrom={1800}
              containerClassName="col-span-full"
              {...(headerErrorFor("note") !== undefined ? { error: headerErrorFor("note") } : {})}
            />
          </FormSection>
        </Card>

        <Card className="p-4 sm:p-6 flex flex-col gap-4">
          <FormSection title="Lines" columns={1} headingLevel={2}>
            {locations.status === "error" ? (
              <InlineAlert
                tone="warning"
                title="Locations couldn't be loaded. Stock goes to each item's default location."
              />
            ) : null}
            {context.lines.map((ctx, index) => {
              const draft = lines[index];
              if (!draft) return null;
              return (
                <ReceiveLineEditor
                  key={ctx.purchase_order_line_id}
                  ctx={ctx}
                  draft={draft}
                  analysis={built.analyses[ctx.purchase_order_line_id]}
                  onChange={(next) => setLines((all) => all.map((l, i) => (i === index ? next : l)))}
                  locationOptions={locations.options}
                  attempted={attempted}
                  server={server.byLine[ctx.purchase_order_line_id]}
                />
              );
            })}
          </FormSection>

          <div ref={summaryRef} tabIndex={-1} aria-live="polite" className="empty:hidden focus:outline-none">
            {invalid ? (
              <InlineAlert
                tone="danger"
                title={built.formError ?? "Some lines need attention. Fix the messages below the fields, then post again."}
              />
            ) : null}
            {post.error && !hasFieldErrors ? <InlineAlert tone="danger" title={problemMessage(post.error)} /> : null}
            {post.error && hasFieldErrors ? (
              <InlineAlert tone="danger" title="The server refused this receipt. Nothing was posted. See the messages on the lines." />
            ) : null}
          </div>

          {/* Below md the bar stays above the bottom navigation, so "Post receipt" is in reach however
              many lines the delivery has. */}
          <div
            className="sticky z-10 -mx-4 flex items-center justify-end gap-3 border-t border-line bg-surface px-4 py-3 md:static md:mx-0 md:bg-transparent md:px-0 md:pb-0 md:pt-4"
            style={{ bottom: "var(--shell-bottom-inset)" }}
            data-action-bar=""
          >
            <Button type="submit" loading={post.pending} className="w-full md:w-auto">
              Post receipt
            </Button>
          </div>
        </Card>
      </form>
    </div>
  );
}
