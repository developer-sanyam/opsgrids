import { createElement, lazy } from "react";
import type { ComponentType, ReactElement } from "react";

export interface RetryableLazy<P extends object> {
  /** Stands in for the lazy component. Render it inside a `Suspense` and a `PanelBoundary`. */
  Panel: (props: P) => ReactElement;
  /** Forget a failed download so the next mount asks for the chunk again. */
  reset: () => void;
}

/**
 * `React.lazy` for a panel whose chunk can fail to download and be tried again.
 *
 * A lazy component remembers its first outcome for good: once the import rejects, every later
 * render re-throws that same error without asking the network again, so a "Try again" button that
 * only re-renders the panel can never recover. `reset` swaps in a fresh lazy component, which makes
 * a fresh `import()` the next time the panel mounts. Call it from the boundary's `onFail`, so
 * whichever retry the user reaches (the boundary's own, or a keyed remount by its owner) finds a
 * component that has not failed yet; a lazy that has not been rendered has asked for nothing.
 */
export function retryableLazy<P extends object>(load: () => Promise<{ default: ComponentType<P> }>): RetryableLazy<P> {
  let current = lazy(load);
  return {
    Panel: (props) => createElement(current, props),
    reset: () => {
      current = lazy(load);
    },
  };
}
