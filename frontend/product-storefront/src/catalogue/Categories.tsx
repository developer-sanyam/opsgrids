import { useState } from "react";

import { ApiError, Can, problemMessage, useAsync, useCan, useMutation } from "@opsgrids/client";
import {
  Button,
  ConfirmDialog,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  RowActions,
  SearchInput,
  Skeleton,
  StatusBadge,
  useDebouncedValue,
  useToast,
} from "@opsgrids/ui";
import type { Column, TableSelection } from "@opsgrids/ui";

import { downloadCsv } from "@/lib/csv";
import { CategoryForm } from "./CategoryForm";
import { deleteCategory, listCategories } from "./api";
import type { CategoryOut } from "./types";

const WRITE = "storefront.catalogue.product.write";
const READ = "storefront.catalogue.product.read";

export function CategoriesPage() {
  const toast = useToast();
  const canWrite = useCan(WRITE, "ORG");
  const canRead = useCan(READ, "ORG");
  const [rawQuery, setRawQuery] = useState("");
  const query = useDebouncedValue(rawQuery);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [editing, setEditing] = useState<CategoryOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<CategoryOut | null>(null);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const categories = useAsync(
    () => listCategories({ q: query, page, size }),
    [query, page, size],
  );

  const categorySelection: TableSelection<CategoryOut> | undefined =
    categories.status === "success"
      ? {
          selected,
          onToggle: (key, next) =>
            setSelected((prev) => {
              const nextSet = new Set(prev);
              if (next) nextSet.add(key);
              else nextSet.delete(key);
              return nextSet;
            }),
          onToggleAll: (next) =>
            setSelected(next ? new Set(categories.data.items.map((c) => c.id)) : new Set()),
          label: (c) => c.label,
        }
      : undefined;

  function exportSelectedCategories() {
    if (categories.status !== "success") return;
    const chosen = categories.data.items.filter((c) => selected.has(c.id));
    downloadCsv(
      "categories.csv",
      ["Category", "Key", "Status", "Products"],
      chosen.map((c) => [c.label, c.key, c.is_active ? "Active" : "Inactive", String(c.line_count)]),
    );
  }

  const remove = useMutation((category: CategoryOut) => deleteCategory(category.id), {
    onSuccess: (_result, category) => {
      setDeleting(null);
      toast.success(`${category.label} deleted.`);
      categories.reload();
    },
  });

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        eyebrow="Storefront"
        title="Categories"
        description="How your catalogue is grouped for buyers — each category's products render on the buyer-facing store and in the theme editor's source picker."
      />

      {!canRead ? (
        <ErrorState
          tone="neutral"
          title="You can't see the catalogue"
          message="This needs storefront.catalogue.product.read at organization scope. An owner or a storefront admin can grant it."
        />
      ) : (
        <>
          {categories.status === "loading" ? <Skeleton className="h-64 w-full" /> : null}
          {categories.status === "error" ? (
            <ErrorState
              title="Couldn't load categories"
              message={problemMessage(categories.error)}
              action={<Button onClick={categories.reload}>Try again</Button>}
            />
          ) : null}

          {categories.status === "success" ? (
            <>
              <DataTable
                rows={categories.data.items}
                rowKey={(c) => c.id}
                mobileCard="auto"
                selection={categorySelection}
                bulkActions={
                  <Button variant="outline" size="sm" onClick={exportSelectedCategories}>
                    Export selected
                  </Button>
                }
                toolbar={
                  <div className="flex flex-wrap items-center justify-between gap-4">
                    <SearchInput
                      label="Search categories"
                      placeholder="Search categories"
                      value={rawQuery}
                      onChange={(e) => {
                        setRawQuery(e.target.value);
                        setPage(1);
                      }}
                    />
                    <Can permission={WRITE} scope="ORG">
                      <Button onClick={() => setCreating(true)}>New category</Button>
                    </Can>
                  </div>
                }
                emptyState={
                  <EmptyState
                    title={query ? "No categories match that search" : "No categories yet"}
                    description={
                      query
                        ? "Try a shorter search term."
                        : "Create the first one — the theme editor's product-source picker and the buyer-facing store both draw from this list."
                    }
                    action={
                      query ? (
                        <Button variant="outline" onClick={() => setRawQuery("")}>
                          Clear search
                        </Button>
                      ) : canWrite ? (
                        <Button onClick={() => setCreating(true)}>New category</Button>
                      ) : null
                    }
                  />
                }
                columns={
                  [
                    {
                      key: "label",
                      header: "Category",
                      mobile: "title",
                      render: (c) => (
                        <div className="flex flex-col gap-0.5">
                          <span className="font-bold text-ink">{c.label}</span>
                          <span className="text-xs text-faint tnum">{c.key}</span>
                        </div>
                      ),
                    },
                    {
                      key: "status",
                      header: "Status",
                      mobile: "status",
                      render: (c) => <StatusBadge status={c.is_active ? "active" : "inactive"} />,
                    },
                    {
                      key: "count",
                      header: "Products",
                      align: "right",
                      cellClassName: "tnum",
                      render: (c) => c.line_count,
                    },
                    {
                      key: "actions",
                      header: "",
                      align: "right",
                      mobile: "actions",
                      render: (c) => (
                        <RowActions
                          label={c.label}
                          actions={[
                            { label: "Edit", onSelect: () => setEditing(c), hidden: !canWrite },
                            {
                              label: "Delete",
                              tone: "danger",
                              onSelect: () => setDeleting(c),
                              hidden: !canWrite,
                              disabled: c.line_count > 0,
                              disabledReason: "Products still carry this category.",
                            },
                          ]}
                        />
                      ),
                    },
                  ] satisfies Column<CategoryOut>[]
                }
              />
              <Pagination
                page={page}
                pages={categories.data.pages}
                total={categories.data.total}
                size={size}
                noun="categories"
                onPageChange={setPage}
                onSizeChange={(n) => {
                  setSize(n);
                  setPage(1);
                }}
              />
            </>
          ) : null}
        </>
      )}

      {creating ? (
        <CategoryForm
          category={null}
          onClose={() => setCreating(false)}
          onSaved={(c) => {
            toast.success(`${c.label} created.`);
            categories.reload();
          }}
        />
      ) : null}
      {editing ? (
        <CategoryForm
          category={editing}
          onClose={() => setEditing(null)}
          onSaved={(c) => {
            toast.success(`${c.label} saved.`);
            categories.reload();
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
        body={`${deleting?.label} is removed. This can't be undone.`}
        confirmLabel="Delete"
        pendingLabel="Deleting…"
        pending={remove.pending}
        {...(remove.error instanceof ApiError ? { error: remove.error.message } : {})}
      />
    </div>
  );
}
