import { useCallback, useState } from "react";
import { Link } from "react-router-dom";

import { resolveScan } from "@/api/scan";
import type { ScanOut } from "@/api/scan";
import { formatQty } from "@/lib/quantity";
import { planActions } from "@/lib/scanActions";
import type { ScanActionPlan } from "@/lib/scanActions";
import { parsePayload, ScanPayloadError } from "@/lib/scanPayload";
import { ApiError, problemMessage } from "@opsgrids/client";
import { BarcodeScanner } from "@/scan/BarcodeScanner";
import { StatusPanel, TransferPanel } from "@/scan/StockMovePanels";
import { Button, Card, InlineAlert, PageHeader, Tag } from "@/ui";

const TYPE_LABEL = { item: "Item", lot: "Lot", roll: "Roll", location: "Location" } as const;

type Panel = "transfer" | "status_change" | null;

/** What went wrong with a lookup, as one sentence a person on a warehouse floor can act on. */
function lookupMessage(error: unknown): string {
  if (error instanceof ScanPayloadError) return error.message;
  if (error instanceof ApiError) {
    if (error.status === 404) return "Nothing in this organization has that code.";
    if (error.status === 403) return "You are not allowed to look up stock.";
  }
  return problemMessage(error);
}

const ACTION_CLASS =
  "flex min-h-16 flex-col items-center justify-center gap-1 rounded-tile border border-field-edge " +
  "bg-surface px-3 py-3 text-center text-base font-semibold text-ink hover:bg-sunken active:bg-sunken";

function ActionTile({ plan, onPanel }: { plan: ScanActionPlan; onPanel: (action: Panel) => void }) {
  const body = (
    <>
      <span>{plan.label}</span>
      <span className="text-xs font-normal text-muted">{plan.hint}</span>
    </>
  );
  if (plan.kind === "link") {
    return (
      <Link to={plan.to} className={ACTION_CLASS}>
        {body}
      </Link>
    );
  }
  return (
    <button
      type="button"
      className={ACTION_CLASS}
      onClick={() => onPanel(plan.action === "transfer" ? "transfer" : "status_change")}
    >
      {body}
    </button>
  );
}

/**
 * `/inventory/scan` — the warehouse floor's front door (inventory Phase 9, ADR-0027).
 *
 * Scan or type a label; the server says what it is and what *this person* may do with it; this
 * screen draws only the actions the build can actually perform (`planActions`), each a large
 * touch target. Moving and holding stock reuse the stock endpoints; receiving, picking, counting
 * and inspecting link into the screens that own them — and are simply absent where a phase is
 * not built, never a dead link.
 */
export function ScanPage() {
  const [result, setResult] = useState<ScanOut | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [looking, setLooking] = useState(false);
  const [panel, setPanel] = useState<Panel>(null);
  const [done, setDone] = useState<string | null>(null);

  const lookUp = useCallback(async (text: string) => {
    setError(null);
    setDone(null);
    setPanel(null);
    setLooking(true);
    try {
      const parsed = parsePayload(text);
      const wire = parsed.type ? `og:${parsed.type}:${parsed.code}` : parsed.code;
      setResult(await resolveScan(wire));
    } catch (e) {
      setResult(null);
      setError(lookupMessage(e));
    } finally {
      setLooking(false);
    }
  }, []);

  const reset = () => {
    setResult(null);
    setError(null);
    setPanel(null);
  };

  const plans = result ? planActions(result.entity, result.allowed_actions) : [];

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-6">
      <PageHeader
        title="Scan"
        description="Scan a label to move, hold, count or look up stock."
      />

      {done ? (
        <InlineAlert tone="success" title={done}>
          Scan the next label to carry on.
        </InlineAlert>
      ) : null}

      {panel && result ? (
        <Card className="p-4">
          {panel === "transfer" ? (
            <TransferPanel
              entity={result.entity}
              onCancel={() => setPanel(null)}
              onDone={(message) => {
                setPanel(null);
                setResult(null);
                setDone(message);
              }}
            />
          ) : (
            <StatusPanel
              entity={result.entity}
              onCancel={() => setPanel(null)}
              onDone={(message) => {
                setPanel(null);
                setResult(null);
                setDone(message);
              }}
            />
          )}
        </Card>
      ) : result ? (
        <Card className="flex flex-col gap-4 p-4">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <p className="text-xs font-semibold uppercase tracking-wide text-muted">
                {TYPE_LABEL[result.type]}
              </p>
              <h2 className="truncate text-xl font-extrabold text-ink">{result.entity.title}</h2>
              <p className="text-sm text-ink-soft">{result.entity.subtitle}</p>
            </div>
            {result.entity.status ? <Tag>{result.entity.status.toLowerCase().replace("_", " ")}</Tag> : null}
          </div>
          {result.entity.on_hand_qty !== null ? (
            <p className="tnum text-3xl font-extrabold text-ink">
              {formatQty(result.entity.on_hand_qty)}{" "}
              <span className="text-lg font-semibold text-muted">{result.entity.uom}</span>
            </p>
          ) : null}
          <div className="grid grid-cols-2 gap-3">
            {plans.map((plan) => (
              <ActionTile key={plan.action} plan={plan} onPanel={setPanel} />
            ))}
          </div>
          <Button variant="secondary" className="min-h-14 w-full text-base" onClick={reset}>
            Scan another
          </Button>
        </Card>
      ) : (
        <Card className="p-4">
          <BarcodeScanner onScan={(text) => void lookUp(text)} busy={looking} />
        </Card>
      )}

      {error ? <InlineAlert tone="danger" title={error} /> : null}
    </div>
  );
}
