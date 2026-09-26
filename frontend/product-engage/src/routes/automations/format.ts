/**
 * Number and duration presentation for the automation screens. The service decides every figure;
 * these only choose how to print it.
 */

/**
 * A success rate as a percentage. The contract types it as a float and does not pin the scale, so
 * a value up to 1 is read as a fraction (`0.893` is 89.3%) and anything larger is already a
 * percentage. `null` means the automation has not run in the window, which is not 0%.
 */
export function formatRate(rate: number | null): string {
  if (rate === null) return "—";
  const percent = rate <= 1 ? rate * 100 : rate;
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(percent)}%`;
}

/** The rate as a 0 to 100 number, for a meter. */
export function ratePercent(rate: number): number {
  return Math.min(100, Math.max(0, rate <= 1 ? rate * 100 : rate));
}

/** `4200` becomes `4.2s`; under a second stays in milliseconds. `null` is "no runs to average". */
export function formatDurationMs(ms: number | null): string {
  if (ms === null) return "—";
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) {
    return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(seconds)}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round(seconds % 60);
  return rest === 0 ? `${minutes}m` : `${minutes}m ${rest}s`;
}

/** An ISO instant as a date only, in the viewer's zone. */
export function formatDay(iso: string): string {
  return new Date(iso).toLocaleDateString(undefined, { day: "numeric", month: "short", year: "numeric" });
}
