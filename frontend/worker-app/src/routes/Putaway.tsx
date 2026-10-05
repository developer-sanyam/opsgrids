import { useRef, useState } from "react";

import { useAsync } from "@opsgrids/client";
import { Button, Card, InlineAlert, PageHeader, TextField } from "@opsgrids/ui";

import { suggestPutawayBin } from "@/api/client";
import type { StockRowOut } from "@/api/types";
import { RequireGrant } from "@/auth/RequireGrant";
import { geoSkipNotice, requestGeoFix } from "@/lib/geo";
import { buildPutawayBody, matchesSuggestion, quantityProblem, stockDetail, stockLabel } from "@/lib/putawayForm";
import type { PutawayCompletion } from "@/lib/putawayForm";
import { usePutawayQueue } from "@/lib/putawayQueueContext";
import { formatQty } from "@/lib/quantity";
import { useAvailableStock } from "@/lib/useAvailableStock";
import { PutawayBinScan } from "@/ui/PutawayBinScan";
import { PutawayQueuePanel } from "@/ui/PutawayQueuePanel";
import { PutawayResult } from "@/ui/PutawayResult";
import { PutawayStockPicker } from "@/ui/PutawayStockPicker";
import { PutawaySuggestedBin } from "@/ui/PutawaySuggestedBin";

export function PutawayPage() {
  return (
    <RequireGrant permission="operations.inventory.stock.transfer" scope="ORG" what="put away">
      <PutawayContent />
    </RequireGrant>
  );
}

function PutawayContent() {
  const [completion, setCompletion] = useState<PutawayCompletion | null>(null);

  return (
    <div className="rise flex flex-col gap-6">
      <PageHeader title="Put away" description="Move stock from receiving or staging into its storage bin." />
      <PutawayQueuePanel />
      {completion ? (
        <PutawayResult completion={completion} onAnother={() => setCompletion(null)} />
      ) : (
        <PutawayForm onDone={setCompletion} />
      )}
    </div>
  );
}

type Phase = "idle" | "locating" | "saving";

/**
 * Pick the stock, see where it should go, scan the bin it is actually going into, confirm. The bin
 * label is the location of record; the phone's position is taken once, at confirm, as evidence and
 * can never hold the put-away up — if it is denied, slow, unsupported or offline the put-away goes
 * through without it and says so.
 */
function PutawayForm({ onDone }: { onDone: (completion: PutawayCompletion) => void }) {
  const queue = usePutawayQueue();
  const stock = useAvailableStock();
  const [selected, setSelected] = useState<StockRowOut | null>(null);
  const [quantity, setQuantity] = useState("");
  const [quantityError, setQuantityError] = useState<string | undefined>(undefined);
  const [bin, setBin] = useState<string | null>(null);
  const [failure, setFailure] = useState<string | null>(null);
  const [phase, setPhase] = useState<Phase>("idle");
  // Latched synchronously: `phase` lands a render after the click, one render too late to stop a
  // double tap from confirming the same put-away twice.
  const busy = useRef(false);

  // Each step replaces the control that had focus, which would otherwise drop it to the page. The
  // bin step takes focus itself once stock is chosen (`PutawayBinScan`: the quantity is already
  // filled in with what is available, and a keyboard-wedge scanner types into whatever has focus,
  // so the next scan has to land in the bin field); a worker who went back to change stock gets
  // the search.
  const [searchOnReturn, setSearchOnReturn] = useState(false);

  const suggestionState = useAsync(
    () =>
      selected
        ? suggestPutawayBin({
            inventory_item_id: selected.inventory_item_id,
            warehouse_id: selected.warehouse_id,
            lot_id: selected.lot_id ?? undefined,
          })
        : Promise.resolve(null),
    [selected?.id],
  );
  const suggestion = suggestionState.status === "success" ? (suggestionState.data?.suggestion ?? null) : null;
  const differsFromSuggestion = suggestion !== null && bin !== null && !matchesSuggestion(bin, suggestion);

  function select(row: StockRowOut) {
    setSelected(row);
    setQuantity(row.available_qty);
    setQuantityError(undefined);
    setBin(null);
    setFailure(null);
  }

  async function confirm() {
    if (!selected || bin === null || busy.current) return;
    const problem = quantityProblem(quantity, selected.available_qty, selected.uom);
    if (problem) {
      setQuantityError(problem);
      return;
    }
    busy.current = true;
    setQuantityError(undefined);
    setFailure(null);
    // The moment of confirming, taken before the position is awaited so a slow fix can't move it.
    const capturedAt = new Date().toISOString();
    try {
      setPhase("locating");
      const geo = await requestGeoFix({ online: queue.online });
      setPhase("saving");
      const body = buildPutawayBody({
        row: selected,
        quantity,
        toBarcode: bin,
        suggestion,
        capturedAt,
        geo: geo.kind === "fix" ? geo.fix : null,
      });
      const summary = {
        sku: selected.sku,
        itemName: selected.item_name,
        uom: selected.uom,
        fromLocationCode: selected.location_code,
        toBarcode: bin,
      };
      const outcome = await queue.submit({ body, summary });
      const geoNotice = geo.kind === "skipped" ? geoSkipNotice(geo.reason) : null;

      if (outcome.kind === "rejected") {
        // Nothing was recorded and it has left the queue; the worker fixes it and confirms again.
        if (outcome.status === 404) setBin(null);
        setFailure(outcome.message);
        return;
      }
      onDone(
        outcome.kind === "synced"
          ? { kind: "synced", result: outcome.result, summary, quantity: body.quantity, geoNotice }
          : { kind: "queued", reason: outcome.reason, summary, quantity: body.quantity, geoNotice },
      );
    } finally {
      busy.current = false;
      setPhase("idle");
    }
  }

  return (
    <>
      {selected === null ? (
        <PutawayStockPicker stock={stock} onSelect={select} focusSearch={searchOnReturn} />
      ) : (
        <>
          <Card className="flex flex-col gap-4 p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="flex min-w-0 flex-col gap-1">
                <p className="text-xs font-semibold text-muted">Stock</p>
                <p className="text-base font-bold text-ink">{stockLabel(selected)}</p>
                <p className="text-sm text-muted">{stockDetail(selected)}</p>
              </div>
              <Button
                variant="secondary"
                size="sm"
                aria-label="Change stock"
                onClick={() => {
                  setSearchOnReturn(true);
                  setSelected(null);
                }}
                disabled={phase !== "idle"}
              >
                Change
              </Button>
            </div>
            <TextField
              label={`Quantity to put away (${selected.uom})`}
              inputMode="decimal"
              value={quantity}
              onChange={(event) => {
                setQuantity(event.target.value);
                setQuantityError(undefined);
              }}
              helper={`Up to ${formatQty(selected.available_qty)} ${selected.uom}.`}
              className="text-lg"
              required
              {...(quantityError !== undefined ? { error: quantityError } : {})}
            />
          </Card>

          <Card className="flex flex-col gap-4 p-4">
            <PutawaySuggestedBin
              status={suggestionState.status}
              suggestion={suggestion}
              onRetry={suggestionState.reload}
            />
            <PutawayBinScan label={bin} onAccept={setBin} onClear={() => setBin(null)} />

            {differsFromSuggestion ? (
              <InlineAlert tone="warning" title="That isn't the suggested bin">
                {`The system suggested ${suggestion.code}. Check you are at the right rack, then confirm to put it here anyway.`}
              </InlineAlert>
            ) : null}
            {failure ? <InlineAlert tone="danger" title={failure} /> : null}

            {bin !== null ? (
              <Button onClick={() => void confirm()} loading={phase !== "idle"}>
                {phase === "locating"
                  ? "Getting your location…"
                  : phase === "saving"
                    ? "Saving…"
                    : differsFromSuggestion
                      ? "Put away here anyway"
                      : "Put away"}
              </Button>
            ) : null}
          </Card>
        </>
      )}
    </>
  );
}
