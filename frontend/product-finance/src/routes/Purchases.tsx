import { useMemo, useState } from "react";

import { listPurchases } from "@/api/client";
import type { PurchaseDocument } from "@/api/types";
import { formatDate } from "@/lib/format";
import { formatAmount } from "@/lib/money";
import { Can, problemMessage, useAsync } from "@opsgrids/client";
import {
  Button,
  Card,
  DataTable,
  EmptyState,
  ErrorState,
  PageHeader,
  Pagination,
  SearchInput,
  Skeleton,
  Tag,
} from "@/ui";
import type { Column } from "@/ui";
import { BanknoteIcon, PlusIcon, useDebouncedValue, useToast } from "@opsgrids/ui";
import { PurchaseFormDialog } from "@/forms/PurchaseFormDialog";

export function PurchasesPage() {
  const toast = useToast();
  const [search, setSearch] = useState("");
  const debouncedSearch = useDebouncedValue(search);
  const [page, setPage] = useState(1);
  const [size, setSize] = useState(25);
  const [creating, setCreating] = useState(false);

  const purchases = useAsync(
    () => listPurchases({ search: debouncedSearch, page, size }),
    [debouncedSearch, page, size],
  );
  const rows = useMemo(() => (purchases.status === "success" ? purchases.data.items : []), [purchases]);

  const columns: Column<PurchaseDocument>[] = [
    {
      key: "supplier",
      header: "Supplier",
      render: (p) => (
        <div className="flex flex-col leading-tight">
          <span className="font-semibold text-ink">{p.supplier_name || "—"}</span>
          <span className="money text-xs text-muted">{p.supplier_gstin}</span>
        </div>
      ),
    },
    {
      key: "number",
      header: "Document",
      render: (p) => (
        <div className="flex flex-col leading-tight">
          <span className="money text-sm font-semibold text-ink-soft">{p.number}</span>
          <span className="text-xs text-muted tnum">{formatDate(p.document_date)}</span>
        </div>
      ),
    },
    {
      key: "type",
      header: "Type",
      cellClassName: "hidden sm:table-cell",
      headerClassName: "hidden sm:table-cell",
      render: (p) => <Tag>{p.document_type === "credit_note" ? "Credit note" : "Invoice"}</Tag>,
    },
    {
      key: "taxable",
      header: "Taxable",
      align: "right",
      cellClassName: "whitespace-nowrap hidden md:table-cell",
      headerClassName: "hidden md:table-cell",
      render: (p) => <span className="money text-ink-soft">₹{formatAmount(p.taxable_minor)}</span>,
    },
    {
      key: "total",
      header: "Total",
      align: "right",
      cellClassName: "whitespace-nowrap",
      render: (p) => <span className="money font-semibold text-ink">₹{formatAmount(p.total_minor)}</span>,
    },
  ];

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Accounts payable"
        title="Purchases"
        actions={
          <Can permission="finance.billing.purchase.write" scope="ORG">
            <Button leadingIcon={<PlusIcon className="size-4" />} onClick={() => setCreating(true)}>
              Record purchase
            </Button>
          </Can>
        }
      />

      <p className="-mt-2 max-w-2xl text-sm text-muted">
        What suppliers billed this organization — the local record every IMS suggestion is matched
        against. No update or delete: a supplier's own correction is their credit or debit note.
      </p>

      <Card className="p-4 sm:p-5">
        <SearchInput
          label="Search purchases"
          placeholder="Search by supplier, GSTIN or document number…"
          value={search}
          onChange={(e) => {
            setSearch(e.target.value);
            setPage(1);
          }}
          containerClassName="sm:max-w-sm"
        />
      </Card>

      <Card className="p-2 sm:p-4">
        {purchases.status === "loading" ? (
          <div className="flex flex-col gap-2 p-2">
            {Array.from({ length: 6 }).map((_, i) => (
              <Skeleton key={i} className="h-12 w-full rounded-tile" />
            ))}
          </div>
        ) : null}
        {purchases.status === "error" ? (
          <ErrorState
            title="Couldn't load purchases"
            message={problemMessage(purchases.error)}
            onRetry={purchases.reload}
          />
        ) : null}
        {purchases.status === "success" ? (
          <>
            <DataTable
              columns={columns}
              rows={rows}
              rowKey={(p) => p.id}
              caption="Purchase documents recorded"
              emptyState={
                <EmptyState
                  icon={<BanknoteIcon className="size-6" />}
                  title={debouncedSearch ? "No purchases match that search" : "No purchases recorded yet"}
                  description={
                    debouncedSearch
                      ? "Try a different supplier, GSTIN or number."
                      : "Record the first one so IMS suggestions have something to match against."
                  }
                  action={
                    debouncedSearch ? null : (
                      <Can permission="finance.billing.purchase.write" scope="ORG">
                        <Button size="sm" onClick={() => setCreating(true)}>
                          Record purchase
                        </Button>
                      </Can>
                    )
                  }
                />
              }
            />
            <Pagination
              page={purchases.data.page}
              pages={purchases.data.pages}
              total={purchases.data.total}
              size={purchases.data.size}
              noun="purchases"
              onPageChange={setPage}
              onSizeChange={(n) => {
                setSize(n);
                setPage(1);
              }}
            />
          </>
        ) : null}
      </Card>

      {creating ? (
        <PurchaseFormDialog
          onClose={() => setCreating(false)}
          onSaved={(purchase) => {
            setCreating(false);
            toast.success(`${purchase.number} recorded.`);
            purchases.reload();
          }}
        />
      ) : null}
    </div>
  );
}
