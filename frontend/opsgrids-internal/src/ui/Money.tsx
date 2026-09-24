import { cn } from "@opsgrids/ui";

import { formatMoney } from "@/lib/money";

interface MoneyProps {
  minor: number;
  currency: string;
  /** Compact notation (₹17.6L) for headline figures. */
  compact?: boolean | undefined;
  /** Show minor-unit fraction digits (invoice lines). */
  precise?: boolean | undefined;
  /** Tint a negative amount (a consumption or a reversal) with the semantic negative colour. */
  signed?: boolean | undefined;
  className?: string | undefined;
}

/**
 * A monospaced money figure — the console reads amounts in a tabular face so columns of figures
 * align to the digit. Always an integer minor amount and a currency, never a float; the text is
 * the sign as well as the colour, so a negative never rests on hue alone.
 */
export function Money({ minor, currency, compact, precise, signed, className }: MoneyProps) {
  const negative = minor < 0;
  const text = formatMoney(minor, currency, { compact: compact ?? false, precise: precise ?? false });
  return <span className={cn("money", signed && negative && "text-negative", className)}>{text}</span>;
}
