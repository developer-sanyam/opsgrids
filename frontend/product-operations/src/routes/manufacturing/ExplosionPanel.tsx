import { useRef, useState } from "react";

import { explodeBomVersion } from "@/api/client";
import type { ExplosionNodeOut, ExplosionOut, ExplosionTotalOut } from "@/api/types";
import { formatQty, qtyFieldError } from "@/lib/quantity";
import { problemMessage, useMutation } from "@opsgrids/client";
import { Button, DataTable, EmptyState, ErrorState, Skeleton, Tag, TextField } from "@/ui";
import type { Column } from "@/ui";

interface ExplosionPanelProps {
  bomId: string;
  versionId: string;
  /** The version's batch size — the quantity the panel starts on. */
  defaultQuantity: string;
  /** The produced item's unit, for the helper line. */
  uom: string;
}

function TreeNode({ node }: { node: ExplosionNodeOut }) {
  return (
    <li className="flex flex-col gap-1">
      <div className="flex flex-wrap items-baseline gap-2 text-sm">
        <span className="font-semibold text-ink">{node.sku}</span>
        <span className="text-muted">{node.name}</span>
        <span className="tnum text-ink">
          {formatQty(node.quantity)} {node.uom}
        </span>
        {node.is_produced ? <Tag tone="info">Produced</Tag> : null}
      </div>
      {node.children.length > 0 ? (
        <ul className="ml-4 flex flex-col gap-2 border-l border-line pl-4">
          {node.children.map((child) => (
            <TreeNode key={`${child.item_id}-${child.level}-${child.sku}`} node={child} />
          ))}
        </ul>
      ) : null}
    </li>
  );
}

const TOTAL_COLUMNS: Column<ExplosionTotalOut>[] = [
  { key: "sku", header: "SKU", render: (t) => <span className="font-semibold text-ink">{t.sku}</span> },
  { key: "name", header: "Item", render: (t) => <span className="text-muted">{t.name}</span> },
  {
    key: "quantity",
    header: "Total needed",
    align: "right",
    render: (t) => (
      <span className="tnum">
        {formatQty(t.quantity)} {t.uom}
      </span>
    ),
  },
];

/**
 * Multi-level explosion (`GET .../versions/{id}/explosion?quantity=`): what a quantity of the
 * produced item takes, as an indented tree, and the purchased (leaf) items it adds up to. The
 * quantity goes to the server as typed text and the figures come back as decimal strings; nothing
 * here multiplies anything.
 */
export function ExplosionPanel({ bomId, versionId, defaultQuantity, uom }: ExplosionPanelProps) {
  const [quantity, setQuantity] = useState(defaultQuantity);
  const [quantityError, setQuantityError] = useState<string | undefined>(undefined);
  const [result, setResult] = useState<ExplosionOut | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  const explode = useMutation((q: string) => explodeBomVersion(bomId, versionId, q), {
    onSuccess: (out) => setResult(out),
  });

  function submit() {
    const error = qtyFieldError(quantity);
    setQuantityError(error);
    if (error) {
      inputRef.current?.focus();
      return;
    }
    void explode.mutate(quantity.trim());
  }

  return (
    <div className="flex flex-col gap-6">
      <form
        className="flex flex-wrap items-end gap-3"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          submit();
        }}
      >
        <TextField
          ref={inputRef}
          label="Quantity to produce"
          inputMode="decimal"
          value={quantity}
          onChange={(e) => setQuantity(e.target.value)}
          helper={`In ${uom}.`}
          required
          error={quantityError}
          containerClassName="w-48"
        />
        <Button type="submit" loading={explode.pending}>
          Explode
        </Button>
      </form>

      <div aria-live="polite" className="flex flex-col gap-6">
        {explode.pending && result === null ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-6 w-64 rounded-tile" />
            <Skeleton className="h-6 w-48 rounded-tile" />
            <Skeleton className="h-6 w-56 rounded-tile" />
          </div>
        ) : null}
        {explode.error ? (
          <ErrorState
            title="Couldn't explode this version"
            message={problemMessage(explode.error)}
            onRetry={() => explode.mutate(quantity.trim())}
          />
        ) : null}
        {result === null && !explode.pending && !explode.error ? (
          <EmptyState
            title="No explosion yet"
            description="Enter a quantity and choose Explode to see every material it takes."
          />
        ) : null}
        {result !== null && !explode.error ? (
          result.root.children.length === 0 ? (
            <EmptyState title="Nothing to explode" description="This version has no components yet." />
          ) : (
            <>
              <section aria-label="Requirement tree" className="flex flex-col gap-3">
                <h3 className="text-card-title">
                  For {formatQty(result.quantity)} {result.root.uom} of {result.root.sku}
                </h3>
                <ul className="flex flex-col gap-2">
                  <TreeNode node={result.root} />
                </ul>
              </section>
              <section aria-label="Purchased items" className="flex flex-col gap-3">
                <h3 className="text-card-title">Purchased items needed</h3>
                <DataTable
                  columns={TOTAL_COLUMNS}
                  rows={result.leaf_totals}
                  rowKey={(t) => t.item_id}
                  caption="Purchased item totals"
                  emptyState={
                    <EmptyState title="No purchased items" description="Every component is itself produced." />
                  }
                />
              </section>
            </>
          )
        ) : null}
      </div>
    </div>
  );
}
