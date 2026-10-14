import { Button } from "@opsgrids/ui";

/**
 * The way out of a screen the person is not allowed to use. A denied state says no and, without
 * this, leaves them on a dead end; the history step is what the browser's own Back would do.
 */
export function GoBackButton() {
  return (
    <Button variant="secondary" onClick={() => window.history.back()}>
      Go back
    </Button>
  );
}
