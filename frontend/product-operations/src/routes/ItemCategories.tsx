import { useMemo, useState } from "react";

import { deleteItemCategory, listItemCategories } from "@/api/client";
import type { ItemCategoryOut } from "@/api/types";
import { Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
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
import { FolderIcon, PlusIcon, useToast } from "@opsgrids/ui";
import { ItemCategoryFormDialog } from "@/forms/ItemCategoryFormDialog";

/** Depth of each category in the tree, and a display-ordered flat list (parents before their
 *  children) — client-side, since the directory is always small enough to hold in one page. */
function orderedWithDepth(categories: ItemCategoryOut[]): { category: ItemCategoryOut; depth: number }[] {
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
  const out: { category: ItemCategoryOut; depth: number }[] = [];
  const visit = (parentId: string | null, depth: number, seen: Set<string>) => {
    for (const category of byParent.get(parentId) ?? []) {
      if (seen.has(category.id)) continue; // defensive: never loop on a data anomaly
      out.push({ category, depth });
      visit(category.id, depth + 1, new Set(seen).add(category.id));
    }
  };
  visit(null, 0, new Set());
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

  const columns: Column<{ category: ItemCategoryOut; depth: number }>[] = [
    {
      key: "name",
      header: "Name",
      render: ({ category, depth }) => (
        <div style={{ paddingLeft: depth * 20 }} className="flex items-center gap-2">
          {depth > 0 ? <span aria-hidden className="text-muted">└</span> : null}
          <span className="font-semibold text-ink">{category.name}</span>
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
          <Can permission="operations.inventory.item.write" scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              New category
            </Button>
          </Can>
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
