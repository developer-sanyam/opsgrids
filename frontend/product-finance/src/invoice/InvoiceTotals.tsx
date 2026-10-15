import { amountInWords } from "@/lib/amountInWords";
import { formatMoney } from "@/lib/money";
import { Switch } from "@/ui";

import type { InvoicePreview } from "./gstMath";

function Row({ label, value, strong = false }: { label: string; value: string; strong?: boolean }) {
  return (
    <div className={strong ? "flex justify-between border-t border-line pt-2" : "flex justify-between"}>
      <dt className={strong ? "font-bold text-ink" : "text-muted"}>{label}</dt>
      <dd className={strong ? "money text-lg font-extrabold text-ink" : "money font-semibold text-ink"}>{value}</dd>
    </div>
  );
}

const rate = (pct: number) => `${Number(pct.toFixed(2))}%`;

/**
 * The live totals beside the invoice form: taxable value, the tax heads the two states decide,
 * GST grouped by rate, the round-off, and the total in figures and in words — the same blocks
 * the printed tax invoice carries, so what is reviewed here is what the customer receives.
 */
export function InvoiceTotals({
  preview,
  intraState,
  roundOff,
  onRoundOffChange,
}: {
  preview: InvoicePreview;
  intraState: boolean;
  roundOff: boolean;
  onRoundOffChange: (value: boolean) => void;
}) {
  return (
    <div className="flex flex-col gap-4">
      <dl className="flex flex-col gap-2 text-sm">
        <Row label="Taxable value" value={formatMoney(preview.taxable)} />
        {intraState ? (
          <>
            <Row label="CGST" value={formatMoney(preview.cgst)} />
            <Row label="SGST" value={formatMoney(preview.sgst)} />
          </>
        ) : (
          <Row label="IGST" value={formatMoney(preview.igst)} />
        )}
        {roundOff ? <Row label="Round-off" value={formatMoney(preview.roundOff)} /> : null}
        <Row label="Total" value={formatMoney(preview.total)} strong />
      </dl>

      {preview.byRate.length > 0 ? (
        <table className="w-full border-collapse text-xs tnum">
          <caption className="pb-1 text-left text-xs font-semibold uppercase tracking-wide text-faint">
            GST by rate
          </caption>
          <thead>
            <tr className="border-b border-line text-muted">
              <th scope="col" className="py-1 text-left font-semibold">Rate</th>
              <th scope="col" className="py-1 text-right font-semibold">Taxable</th>
              <th scope="col" className="py-1 text-right font-semibold">{intraState ? "CGST + SGST" : "IGST"}</th>
            </tr>
          </thead>
          <tbody>
            {preview.byRate.map((row) => (
              <tr key={row.ratePct} className="border-b border-line">
                <td className="py-1 text-left">{rate(row.ratePct)}</td>
                <td className="py-1 text-right">{formatMoney(row.taxable)}</td>
                <td className="py-1 text-right">{formatMoney(row.cgst + row.sgst + row.igst)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : null}

      <Switch
        checked={roundOff}
        onChange={onRoundOffChange}
        label="Round the total to whole rupees"
        description="The adjustment is shown on the invoice as round-off."
      />

      <p className="text-xs text-muted">
        <span className="font-semibold text-ink-soft">In words: </span>
        {amountInWords(Math.abs(preview.total))}
      </p>
    </div>
  );
}
