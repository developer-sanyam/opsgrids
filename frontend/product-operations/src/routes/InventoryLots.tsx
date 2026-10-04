import { useState } from "react";
import { useNavigate } from "react-router-dom";

import { listLots } from "@/api/tracking";
import type { LotQualityStatus } from "@/api/trackingTypes";
import type { InventoryItemOut } from "@/api/types";
import { Can, useCan } from "@opsgrids/client";
import { LotFormDialog } from "@/forms/LotFormDialog";
import { LOT_QUALITY_OPTIONS } from "@/lib/trackingLabels";
import { Button, Card, PageHeader, SearchInput, Select } from "@/ui";
import { PlusIcon, useDebouncedValue, useToast } from "@opsgrids/ui";
import { ItemPicker } from "@/tracking/ItemPicker";
import { LotsTable } from "@/tracking/LotsTable";
import { RequireStockRead } from "@/tracking/pageParts";
import { useCursorList } from "@/tracking/useCursorList";

/**
 * Lots across the organization (`GET /operations/inventory/lots`, `operations.inventory.stock.read`),
 * newest first, searchable by lot-number prefix and filterable by item and quality status. Creating
 * a lot needs `operations.inventory.lot.write`; a lot holds no stock until something is posted
 * into it.
 */
export function InventoryLotsPage() {
  return (
    <RequireStockRead what="lots">
      <LotsScreen />
    </RequireStockRead>
  );
}

function LotsScreen() {
  const navigate = useNavigate();
  const toast = useToast();
  const canWrite = useCan("operations.inventory.lot.write", "ORG");
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search);
  const [quality, setQuality] = useState<LotQualityStatus | "">("");
  const [item, setItem] = useState<InventoryItemOut | null>(null);
  const [creating, setCreating] = useState(false);

  const list = useCursorList(
    (cursor, signal) =>
      listLots({
        q: debounced.trim(),
        quality_status: quality === "" ? undefined : quality,
        item_id: item?.id,
        cursor,
        limit: 50,
        signal,
      }),
    [debounced, quality, item?.id],
  );
  const filtered = debounced.trim() !== "" || quality !== "" || item !== null;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Lots"
        description="Batches of an item, with where each came from and where it went."
        actions={
          <Can permission="operations.inventory.lot.write" scope="ORG">
            <Button onClick={() => setCreating(true)} leadingIcon={<PlusIcon className="size-4" />}>
              Create lot
            </Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-6">
        <form
          role="search"
          aria-label="Filter lots"
          onSubmit={(e) => e.preventDefault()}
          className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
        >
          <SearchInput
            label="Search lots by number"
            placeholder="Lot number starts with…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Select
            label="Quality status"
            value={quality}
            onChange={(e) => setQuality(e.target.value as LotQualityStatus | "")}
            placeholder="All quality statuses"
            options={LOT_QUALITY_OPTIONS}
          />
          <ItemPicker value={item} onChange={setItem} requires="track_lot" />
        </form>
      </Card>

      <LotsTable list={list} filtered={filtered} {...(canWrite ? { onCreate: () => setCreating(true) } : {})} />

      {creating ? (
        <LotFormDialog
          item={item}
          onClose={() => setCreating(false)}
          onSaved={(lot) => {
            setCreating(false);
            toast.success(`Lot ${lot.lot_no} created.`);
            navigate(`/inventory/lots/${lot.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
