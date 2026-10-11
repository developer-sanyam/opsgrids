import { useState } from "react";

import { deleteQcTemplate, listQcTemplates } from "@/api/client";
import type { QcKind, QcTemplateOut } from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import { QcTemplateFormDialog } from "@/forms/QcTemplateFormDialog";
import { formatBounds } from "@/lib/quality";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  InlineAlert,
  PageHeader,
  Pagination,
  Pill,
  RowActions,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { ClipboardListIcon } from "@opsgrids/ui";

type KindFilter = QcKind | "all";

const KIND_FILTERS: { value: KindFilter; label: string }[] = [
  { value: "all", label: "All" },
  { value: "incoming", label: "Incoming" },
  { value: "in-process", label: "In-process" },
  { value: "finished", label: "Finished" },
];

const KIND_LABEL: Record<QcKind, string> = {
  incoming: "Incoming",
  "in-process": "In-process",
  finished: "Finished",
};

/**
 * QC templates — the checklists inspections are judged against. Anyone with
 * `quality.inspection.read` sees them; defining or changing one is `approve`, because what counts
 * as acceptable is an approver's decision, not the inspector's being measured against it.
 */
export function QcTemplatesPage() {
  const canManage = useCan("operations.quality.inspection.approve", "ORG");
  const [kind, setKind] = useState<KindFilter>("all");
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [editing, setEditing] = useState<QcTemplateOut | "new" | null>(null);
  const [deleting, setDeleting] = useState<QcTemplateOut | null>(null);

  const state = useAsync(() => listQcTemplates({ kind, page, size }), [kind, page, size]);

  const remove = useMutation((t: QcTemplateOut) => deleteQcTemplate(t.id), {
    onSuccess: () => {
      setDeleting(null);
      state.reload();
    },
  });

  const columns: Column<QcTemplateOut>[] = [
    {
      key: "template",
      header: "Template",
      render: (t) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{t.name}</span>
          <span className="text-xs text-muted">{t.code}</span>
        </div>
      ),
    },
    { key: "kind", header: "Kind", render: (t) => <span className="text-muted">{KIND_LABEL[t.kind]}</span> },
    {
      key: "params",
      header: "Parameters",
      cellClassName: "hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (t) =>
        t.params.length === 0 ? (
          <span className="text-muted">None</span>
        ) : (
          <ul className="flex flex-col gap-1 text-xs text-muted">
            {t.params.slice(0, 4).map((p) => (
              <li key={p.id}>
                <span className="font-medium text-ink">{p.name}</span> · {formatBounds(p)}
                {p.unit ? ` ${p.unit}` : ""}
                {p.is_critical ? " · critical" : ""}
              </li>
            ))}
            {t.params.length > 4 ? <li>+{t.params.length - 4} more</li> : null}
          </ul>
        ),
    },
    {
      key: "active",
      header: "Status",
      render: (t) => <Tag>{t.is_active ? "Active" : "Inactive"}</Tag>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: (t) => (
        <RowActions
          label={`Template ${t.code}`}
          actions={[
            { label: "Edit", onSelect: () => setEditing(t), hidden: !canManage },
            { label: "Delete", tone: "danger", onSelect: () => setDeleting(t), hidden: !canManage },
          ]}
        />
      ),
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Quality"
        title="QC templates"
        description="Define what an inspection measures and what counts as acceptable."
        actions={canManage ? <Button onClick={() => setEditing("new")}>New template</Button> : undefined}
      />

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
        </div>
      </Card>

      {remove.error ? <InlineAlert tone="danger" title={problemMessage(remove.error)} /> : null}

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState title="Couldn't load QC templates" message={problemMessage(state.error)} onRetry={state.reload} />
        ) : null}
        {state.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={state.data.items}
              rowKey={(t) => t.id}
              caption="QC templates"
              emptyState={
                <EmptyState
                  icon={<ClipboardListIcon className="size-6" />}
                  title={kind !== "all" ? "No templates of this kind" : "No QC templates yet"}
                  description={
                    kind !== "all"
                      ? "Try another kind, or show all."
                      : canManage
                        ? "Create one to give inspections something to measure against."
                        : "A quality manager can create one."
                  }
                />
              }
            />
            <Pagination
              page={state.data.page}
              pages={state.data.pages}
              total={state.data.total}
              size={state.data.size}
              noun="templates"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {editing !== null ? (
        <Can permission="operations.quality.inspection.approve" scope="ORG">
          <QcTemplateFormDialog
            {...(editing === "new" ? {} : { template: editing })}
            onClose={() => setEditing(null)}
            onSaved={() => {
              setEditing(null);
              state.reload();
            }}
          />
        </Can>
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && remove.mutate(deleting)}
        title="Delete this template?"
        body="New inspections can no longer use it. Inspections already judged against it keep their results."
        confirmLabel="Delete template"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
