/**
 * "This is taking longer than usual." Shown under a skeleton that has been up for 8 seconds
 * (`useSlowLoad`), so a slow network reads as slow rather than as broken. A polite live region that
 * is always in the page and only fills in, so the announcement is made when the words arrive.
 */
export function SlowLoadNotice({ show, what }: { show: boolean; what: string }) {
  return (
    <p role="status" aria-live="polite" className="text-sm text-muted">
      {show ? `Loading ${what} is taking longer than usual. It will appear as soon as it arrives.` : ""}
    </p>
  );
}
