import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";

import { listRolls } from "@/api/tracking";
import type { RollState } from "@/api/trackingTypes";
import type { InventoryItemOut } from "@/api/types";
import { Can, useCan } from "@opsgrids/client";
import { RollFormDialog } from "@/forms/RollFormDialog";
import { RollMergeDialog } from "@/forms/RollMergeDialog";
import { ROLL_STATE_LABEL } from "@/lib/trackingLabels";
import { Button, Card, PageHeader, SearchInput, Select, TextField } from "@/ui";
import type { TableSelection } from "@/ui";
import { PlusIcon, useDebouncedValue, useToast } from "@opsgrids/ui";
import { ItemPicker } from "@/tracking/ItemPicker";
import { RequireStockRead } from "@/tracking/pageParts";
import { RollsTable } from "@/tracking/RollsTable";
import { useCursorList } from "@/tracking/useCursorList";
import type { RollOut } from "@/api/trackingTypes";

const STATE_OPTIONS = (Object.keys(ROLL_STATE_LABEL) as RollState[]).map((value) => ({
  value,
  label: ROLL_STATE_LABEL[value],
}));

/**
 * Rolls across the organization (`GET /operations/inventory/rolls`,
 * `operations.inventory.stock.read`), newest first, searchable by roll-number prefix and
 * filterable by item, state, shade and grade. With `operations.inventory.roll.write` a roll can be
 * registered and several ticked rolls merged into one; opening a roll shows its split and merge
 * history.
 */
export function InventoryRollsPage() {
  return (
    <RequireStockRead what="rolls">
      <RollsScreen />
    </RequireStockRead>
  );
}

function RollsScreen() {
  const navigate = useNavigate();
  const toast = useToast();
  const canWrite = useCan("operations.inventory.roll.write", "ORG");
  const [search, setSearch] = useState("");
  const debounced = useDebouncedValue(search);
  const [state, setState] = useState<RollState | "">("");
  const [shade, setShade] = useState("");
  const debouncedShade = useDebouncedValue(shade);
  const [grade, setGrade] = useState("");
  const debouncedGrade = useDebouncedValue(grade);
  const [item, setItem] = useState<InventoryItemOut | null>(null);
  const [creating, setCreating] = useState(false);
  const [merging, setMerging] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());

  const list = useCursorList(
    (cursor, signal) =>
      listRolls({
        q: debounced.trim(),
        state: state === "" ? undefined : state,
        shade: debouncedShade.trim(),
        grade: debouncedGrade.trim(),
        item_id: item?.id,
        cursor,
        limit: 50,
        signal,
      }),
    [debounced, state, debouncedShade, debouncedGrade, item?.id],
  );
  const filtered =
    debounced.trim() !== "" ||
    state !== "" ||
    debouncedShade.trim() !== "" ||
    debouncedGrade.trim() !== "" ||
    item !== null;

  // A new filter is a new list; ticks on rows that are no longer there would merge rolls the
  // person cannot see.
  useEffect(() => {
    setSelected(new Set());
  }, [debounced, state, debouncedShade, debouncedGrade, item?.id]);

  const selectedRolls = list.items.filter((r) => selected.has(r.id));
  const selection: TableSelection<RollOut> | undefined = canWrite
    ? {
        selected,
        onToggle: (key, next) =>
          setSelected((current) => {
            const copy = new Set(current);
            if (next) copy.add(key);
            else copy.delete(key);
            return copy;
          }),
        onToggleAll: (next) => setSelected(next ? new Set(list.items.map((r) => r.id)) : new Set()),
        label: (r) => `roll ${r.roll_no}`,
      }
    : undefined;

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader
        eyebrow="Inventory"
        title="Rolls"
        description="Individual pieces of fabric, with their split and merge history."
        actions={
          <Can permission="operations.inventory.roll.write" scope="ORG">
            <Button onClick={() => setCreating(true)} leadingIcon={<PlusIcon className="size-4" />}>
              Create roll
            </Button>
          </Can>
        }
      />

      <Card className="p-4 sm:p-6">
        <form
          role="search"
          aria-label="Filter rolls"
          onSubmit={(e) => e.preventDefault()}
          className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3"
        >
          <SearchInput
            label="Search rolls by number"
            placeholder="Roll number starts with…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
          <Select
            label="State"
            value={state}
            onChange={(e) => setState(e.target.value as RollState | "")}
            placeholder="All states"
            options={STATE_OPTIONS}
          />
          <ItemPicker value={item} onChange={setItem} requires="track_roll" />
          <TextField label="Shade" value={shade} onChange={(e) => setShade(e.target.value)} maxLength={40} />
          <TextField label="Grade" value={grade} onChange={(e) => setGrade(e.target.value)} maxLength={20} />
        </form>
      </Card>

      <RollsTable
        list={list}
        filtered={filtered}
        selection={selection}
        bulkActions={
          <Button size="sm" onClick={() => setMerging(true)} disabled={selectedRolls.length < 2}>
            Merge rolls
          </Button>
        }
        {...(canWrite ? { onCreate: () => setCreating(true) } : {})}
      />

      {creating ? (
        <RollFormDialog
          item={item}
          onClose={() => setCreating(false)}
          onSaved={(roll) => {
            setCreating(false);
            toast.success(`Roll ${roll.roll_no} created.`);
            navigate(`/inventory/rolls/${roll.id}`);
          }}
        />
      ) : null}
      {merging ? (
        <RollMergeDialog
          rolls={selectedRolls}
          onClose={() => setMerging(false)}
          onSaved={(result) => {
            setMerging(false);
            setSelected(new Set());
            toast.success(`Merged into roll ${result.result.roll_no}.`);
            navigate(`/inventory/rolls/${result.result.id}`);
          }}
        />
      ) : null}
    </div>
  );
}
