import { cn } from "@/lib/cn";
import { formatMoney } from "@/lib/money";

interface MoneyProps {
  minor: number;
  currency: string;
  /** Compact notation (₹17.6L) for headline figures. */
  compact?: boolean;
  /** Show minor-unit fraction digits (invoice lines). */
  precise?: boolean;
  /** Tint a negative amount (a consumption/reversal) with the semantic negative colour. */
  signed?: boolean;
  className?: string;
}

/**
 * A monospaced money figure — the console reads amounts in a tabular, control-panel face so columns
 * of figures align to the digit. Always an integer minor amount + currency; never a float.
 */
export function Money({ minor, currency, compact, precise, signed, className }: MoneyProps) {
  const negative = minor < 0;
  const text = formatMoney(minor, currency, { compact: compact ?? false, precise: precise ?? false });
  return (
    <span className={cn("mono", signed && negative && "text-negative", className)}>{text}</span>
  );
}
