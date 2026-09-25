import { useCallback, useEffect, useState } from "react";

import { Can, useAsync, useCan } from "@opsgrids/client";
import { ShieldIcon } from "@opsgrids/ui";

import { listSuppressions, removeSuppression } from "@/api/client";
import type { SuppressionOut, SuppressionReason } from "@/api/types";
import { downloadCsv } from "@/lib/csv";
import { failureMessage } from "@/lib/errors";
import { formatInstant } from "@/lib/format";
import {
  Announcer,
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  LoadFailure,
  PageHeader,
  Pagination,
  RowActions,
  Select,
  Skeleton,
  SuppressionReasonTag,
  useAnnounce,
} from "@/ui";
import type { Column, SelectOption, TableSelection } from "@/ui";
import { SuppressionFormDialog } from "./SuppressionFormDialog";

/**
 * Addresses Engage may not mail, and why. Three of the four reasons are facts reported by a
 * recipient or their mailbox provider — `unsubscribed`, `bounced`, `complained` — and only
 * `manual` was written by a person through this screen. **Remove is offered only for `manual`,
 * and the button is presentation only**: `services.remove_suppression` refuses to lift any other
 * reason regardless of what a direct request asks for, so hiding it here saves a click that the
 * server would refuse anyway rather than protecting anything.
 */

const REASON_OPTIONS: SelectOption[] = [
  { value: "", label: "All reasons" },
  { value: "manual", label: "Added by hand" },
  { value: "unsubscribed", label: "Unsubscribed" },
  { value: "bounced", label: "Bounced" },
  { value: "complained", label: "Complained" },
];

const REASON_LABEL: Record<SuppressionReason, string> = {
  manual: "Added by hand",
  unsubscribed: "Unsubscribed",
  bounced: "Bounced",
  complained: "Complained",
};

const NO_SUPPRESSIONS: SuppressionOut[] = [];

function Suppressions() {
  const announce = useAnnounce();
  const canWrite = useCan("engage.audience.suppression.write", "ORG");
  const [reason, setReason] = useState<SuppressionReason | "">("");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [adding, setAdding] = useState(false);
  const [removing, setRemoving] = useState<SuppressionOut | null>(null);
  const [removeFailure, setRemoveFailure] = useState<string | null>(null);
  const [removePending, setRemovePending] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const loader = useCallback(
    () => listSuppressions({ ...(reason === "" ? {} : { reason }), page, size }),
    [reason, page, size],
  );
  const state = useAsync(loader, [reason, page, size]);
  const suppressions = state.status === "success" ? state.data.items : NO_SUPPRESSIONS;

  // Removing the last row on a page beyond the first empties that page without emptying the
  // list — stepping back is what a person would do by hand, and it's what keeps this from
  // looking like "no suppressions yet" when there are, in fact, suppressions on the page before.
  useEffect(() => {
    if (state.status === "success" && suppressions.length === 0 && page > 1) {
      setPage(page - 1);
    }
  }, [state.status, suppressions.length, page]);

  function reload(message: string) {
    announce(message);
    setAdding(false);
    state.reload();
  }

  async function confirmRemove() {
    if (removing === null || removePending) return;
    setRemovePending(true);
    setRemoveFailure(null);
    try {
      await removeSuppression(removing.id);
      const email = removing.email;
      setRemoving(null);
      reload(`${email} can be mailed again.`);
    } catch (caught) {
      setRemoveFailure(failureMessage(caught));
    } finally {
      setRemovePending(false);
    }
  }

  const columns: Column<SuppressionOut>[] = [
    {
      key: "address",
      header: "Address",
      mobile: "title",
      render: (s) => <span className="font-mono text-sm text-ink">{s.email}</span>,
    },
    {
      key: "reason",
      header: "Reason",
      mobile: "status",
      render: (s) => <SuppressionReasonTag reason={s.reason} />,
    },
    { key: "added", header: "Added", render: (s) => <span className="text-muted">{formatInstant(s.created_at)}</span> },
    {
      key: "actions",
      header: "",
      align: "right",
      mobile: "actions",
      render: (s) =>
        s.reason === "manual" ? (
          <RowActions
            label={`suppression on ${s.email}`}
            actions={[{ label: "Remove", tone: "danger", onSelect: () => setRemoving(s), hidden: !canWrite }]}
          />
        ) : (
          <span className="text-xs text-faint">Reported automatically</span>
        ),
    },
  ];

  const suppressionSelection: TableSelection<SuppressionOut> | undefined =
    state.status === "success"
      ? {
          selected,
          onToggle: (key, next) =>
            setSelected((prev) => {
              const nextSet = new Set(prev);
              if (next) nextSet.add(key);
              else nextSet.delete(key);
              return nextSet;
            }),
          onToggleAll: (next) => setSelected(next ? new Set(suppressions.map((s) => s.id)) : new Set()),
          label: (s) => s.email,
        }
      : undefined;

  function exportSelectedSuppressions() {
    const chosen = suppressions.filter((s) => selected.has(s.id));
    downloadCsv(
      "suppressions.csv",
      ["Address", "Reason", "Added"],
      chosen.map((s) => [s.email, REASON_LABEL[s.reason], formatInstant(s.created_at)]),
    );
  }

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Audience"
        title="Suppressions"
        description="Addresses Engage will never mail, whether they unsubscribed, bounced, complained, or were added by hand."
        actions={
          <Can permission="engage.audience.suppression.write" scope="ORG">
            <Button onClick={() => setAdding(true)}>Suppress an address</Button>
          </Can>
        }
      />

      <Card className="flex flex-col gap-4 p-4 sm:p-5">
        <Select
          label="Filter by reason"
          containerClassName="sm:max-w-xs"
          value={reason}
          onChange={(event) => {
            setReason(event.target.value as SuppressionReason | "");
            setPage(1);
          }}
          options={REASON_OPTIONS}
        />
      </Card>

      {state.status === "loading" ? (
        <Card className="flex flex-col gap-4 p-6">
          <Skeleton className="h-6 w-48" />
          <Skeleton className="h-32 w-full rounded-tile" />
        </Card>
      ) : null}

      {state.status === "error" ? (
        <LoadFailure error={state.error} title="Couldn't load your suppressions" />
      ) : null}

      {state.status === "success" && suppressions.length === 0 ? (
        <Card className="p-6">
          <EmptyState
            icon={<ShieldIcon className="size-6" />}
            title={reason === "" ? "No suppressions yet" : "No suppressions with that reason"}
            description={
              reason === ""
                ? "Every address Engage may not mail will show up here — reported automatically, or added by hand."
                : "Clear the filter to see suppressions for every reason."
            }
          />
        </Card>
      ) : null}

      {state.status === "success" && suppressions.length > 0 ? (
        <Card className="p-6">
          <DataTable
            caption="Suppressed addresses, the reason for each and when it was added"
            rows={suppressions}
            rowKey={(s) => s.id}
            columns={columns}
            mobileCard="auto"
            selection={suppressionSelection}
            bulkActions={
              <Button variant="outline" size="sm" onClick={exportSelectedSuppressions}>
                Export selected
              </Button>
            }
          />
          <Pagination
            page={state.data.page}
            pages={state.data.pages}
            total={state.data.total}
            size={state.data.size}
            noun="suppressions"
            onPageChange={setPage}
            onSizeChange={(n) => {
              setSize(n);
              setPage(1);
            }}
            className="mt-4"
          />
        </Card>
      ) : null}

      <SuppressionFormDialog
        open={adding}
        onClose={() => setAdding(false)}
        onSaved={(_suppression, message) => reload(message)}
      />

      <ConfirmDialog
        open={removing !== null}
        onClose={() => {
          setRemoving(null);
          setRemoveFailure(null);
        }}
        onConfirm={() => void confirmRemove()}
        title="Lift this suppression?"
        body={`${removing?.email} becomes mailable again as soon as this is lifted.`}
        confirmLabel="Remove"
        pendingLabel="Removing…"
        pending={removePending}
        {...(removeFailure !== null ? { error: removeFailure } : {})}
      />
    </div>
  );
}

/** The screen's single polite live region. */
export function SuppressionsPage() {
  return (
    <Announcer>
      <Suppressions />
    </Announcer>
  );
}
