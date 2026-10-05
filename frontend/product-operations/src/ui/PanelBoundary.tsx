import { Component } from "react";
import type { ReactNode } from "react";

import { ErrorState } from "@opsgrids/ui";

interface PanelBoundaryProps {
  /** What the panel is, for the heading: "The sites map", "The floor plan". */
  title: string;
  /** Called once per failure with nothing but the fact of it. Never hand it the error: a Maps
   *  loader error can embed the script URL, and the URL embeds the key. */
  onFail?: (() => void) | undefined;
  /** Replaces the default panel when a failure should fall back to something useful instead. */
  fallback?: ((retry: () => void) => ReactNode) | undefined;
  children: ReactNode;
}

interface PanelBoundaryState {
  failed: boolean;
}

/**
 * The line a map, a 3-D scene, a stock lookup or a drawer must not cross: a render failure inside
 * the panel takes the panel down and nothing else. The rest of the Operations screen — its table,
 * its filters, its navigation — keeps working, and the panel offers a retry.
 *
 * React reports the error to the console by itself; nothing here duplicates that or reads it.
 */
export class PanelBoundary extends Component<PanelBoundaryProps, PanelBoundaryState> {
  override state: PanelBoundaryState = { failed: false };

  static getDerivedStateFromError(): PanelBoundaryState {
    return { failed: true };
  }

  override componentDidCatch(): void {
    this.props.onFail?.();
  }

  private retry = (): void => {
    this.setState({ failed: false });
  };

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    if (this.props.fallback) return this.props.fallback(this.retry);
    return (
      <ErrorState
        title={`${this.props.title} couldn't be shown`}
        message="Something went wrong drawing this panel. The rest of the page is unaffected."
        onRetry={this.retry}
      />
    );
  }
}
