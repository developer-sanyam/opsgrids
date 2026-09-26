import { useMemo, useState } from "react";

import { deleteItemCategory, listItemCategories } from "@/api/client";
import type { ItemCategoryOut } from "@/api/types";
import { itemCategoryImport } from "@/data/importExport/itemCategories";
import {
  Can,
  problemMessage,
  useAsync,
  useCan,
  useImportExport,
  useMutation,
} from "@opsgrids/client";
import {
  Button,
  Card,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  RowActions,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { FolderIcon, ImportExportButton, PlusIcon, useToast } from "@opsgrids/ui";
import { ItemCategoryFormDialog } from "@/forms/ItemCategoryFormDialog";

interface Row {
  category: ItemCategoryOut;
  depth: number;
  /** True for a category the top-down walk from the roots never reached — the server refuses
   *  the write that would create this (H2's advisory lock), so it should never happen from here
   *  on, but a row restored from a backup taken before that fix existed could still carry one.
   *  Shown, flagged, and fixable — never silently dropped from the list. */
  needsRepair: boolean;
}

/** Depth of each category in the tree, and a display-ordered flat list (parents before their
 *  children) — client-side, since the directory is always small enough to hold in one page.
 *  Anything the walk from the roots cannot reach (a cycle, or a `parent_id` naming a category
 *  that no longer exists) is appended at the end rather than dropped. */
function orderedWithDepth(categories: ItemCategoryOut[]): Row[] {
  const byParent = new Map<string | null, ItemCategoryOut[]>();
  for (const c of categories) {
    const key = c.parent_id;
    const siblings = byParent.get(key) ?? [];
    siblings.push(c);
    byParent.set(key, siblings);
  }
  for (const siblings of byParent.values()) {
    siblings.sort((a, b) => a.name.localeCompare(b.name));
  }
  const out: Row[] = [];
  const reached = new Set<string>();
  const visit = (parentId: string | null, depth: number, ancestors: Set<string>) => {
    for (const category of byParent.get(parentId) ?? []) {
      if (ancestors.has(category.id)) continue; // a cycle: stop, do not loop forever
      reached.add(category.id);
      out.push({ category, depth, needsRepair: false });
      visit(category.id, depth + 1, new Set(ancestors).add(category.id));
    }
  };
  visit(null, 0, new Set());

  const unreached = categories
    .filter((c) => !reached.has(c.id))
    .sort((a, b) => a.name.localeCompare(b.name));
  for (const category of unreached) {
    out.push({ category, depth: 0, needsRepair: true });
  }
  return out;
}

/**
 * The item master's own category tree (ADR-0014 Phase 1) — additive to the fixed procurement
 * category every item already carries. Admin-only writes, like warehouses.
 */
export function ItemCategoriesPage() {
  const toast = useToast();
  const canWrite = useCan("operations.inventory.item.write", "ORG");
  const [creating, setCreating] = useState(false);
  const [editing, setEditing] = useState<ItemCategoryOut | null>(null);
  const [deleting, setDeleting] = useState<ItemCategoryOut | null>(null);

  const loader = () => listItemCategories({ size: 200 });
  const state = useAsync(loader, []);
  // `state.reload` keeps one identity for the life of the screen. The dialog stays open on its
  // result, so the rows it just created appear behind it as soon as the run ends.
  const dx = useImportExport(itemCategoryImport, { onImported: state.reload });
  const rows = useMemo(
    () => (state.status === "success" ? orderedWithDepth(state.data.items) : []),
    [state],
  );

  const remove = useMutation((category: ItemCategoryOut) => deleteItemCategory(category.id), {
    onSuccess: (_result, category) => {
      setDeleting(null);
      toast.success(`${category.name} deleted.`);
      state.reload();
    },
  });

  const columns: Column<Row>[] = [
    {
      key: "name",
      header: "Name",
      render: ({ category, depth, needsRepair }) => (
        <div style={{ paddingLeft: depth * 20 }} className="flex items-center gap-2">
          {depth > 0 ? <span aria-hidden className="text-muted">└</span> : null}
          <span className="font-semibold text-ink">{category.name}</span>
          {needsRepair ? (
            <span
              role="status"
              className="rounded-full bg-danger-soft px-2 py-0.5 text-xs font-semibold text-danger"
            >
              Broken parent chain — edit to fix
            </span>
          ) : null}
        </div>
      ),
    },
    {
      key: "code",
      header: "Code",
      render: ({ category }) => <Tag>{category.code}</Tag>,
    },
    {
      key: "actions",
      header: "",
      align: "right",
      render: ({ category }) => (
        <RowActions
          label={category.name}
          actions={[
            { label: "Edit", onSelect: () => setEditing(category), hidden: !canWrite },
            {
              label: "Delete",
              tone: "danger",
              onSelect: () => setDeleting(category),
              hidden: !canWrite,
            },
          ]}
        />
      ),
    },
  ];

  const allCategories = state.status === "success" ? state.data.items : [];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Item categories"
        description="The item master's own catalogue tree — separate from the procurement category every item already carries."
        actions={
          <div className="flex items-center gap-2">
            <ImportExportButton
              meta={dx.meta}
              state={dx.state}
              actions={dx.actions}
              canImport={canWrite}
              canExport
            />
            <Can permission="operations.inventory.item.write" scope="ORG">
              <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
                New category
              </Button>
            </Can>
          </div>
        }
      />

      <Card className="p-2 sm:p-4">
        {state.status === "loading" ? (
          <div className="flex flex-col gap-3 p-3">
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {state.status === "error" ? (
          <ErrorState
            title="Couldn't load categories"
            message={problemMessage(state.error)}
            onRetry={state.reload}
          />
        ) : null}
        {state.status === "success" ? (
          <DataTable
            columns={columns}
            rows={rows}
            rowKey={(r) => r.category.id}
            caption="Item categories"
            emptyState={
              <EmptyState
                icon={<FolderIcon className="size-6" />}
                title="No categories yet"
                description="Add the first one to start building the item master's catalogue tree."
                action={
                  <Can permission="operations.inventory.item.write" scope="ORG">
                    <Button size="sm" onClick={() => setCreating(true)}>
                      New category
                    </Button>
                  </Can>
                }
              />
            }
          />
        ) : null}
      </Card>

      {creating ? (
        <ItemCategoryFormDialog
          category={null}
          candidates={allCategories}
          onClose={() => setCreating(false)}
          onSaved={(category) => {
            setCreating(false);
            toast.success(`${category.name} created.`);
            state.reload();
          }}
        />
      ) : null}

      {editing ? (
        <ItemCategoryFormDialog
          category={editing}
          candidates={allCategories}
          onClose={() => setEditing(null)}
          onSaved={(category) => {
            setEditing(null);
            toast.success(`${category.name} saved.`);
            state.reload();
          }}
        />
      ) : null}

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => {
          setDeleting(null);
          remove.reset();
        }}
        onConfirm={() => deleting && void remove.mutate(deleting)}
        title="Delete this category?"
        body={`${deleting?.name} is removed from the catalogue tree. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error ? { error: problemMessage(remove.error) } : {})}
      />
    </div>
  );
}
