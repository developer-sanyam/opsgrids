import { Component, Suspense, lazy, useState } from "react";
import type { ComponentType, ReactNode } from "react";

import { Card, ErrorState, RouteFallback } from "@opsgrids/ui";


interface ScreenBoundaryProps {
  /** What the screen is called, for the heading: "Put away". */
  name: string;
  onRetry: () => void;
  children: ReactNode;
}

/**
 * Catches a screen that did not arrive. Without one, a failed download falls through to the
 * router's default error page, which replaces the whole app — the shell, and the put-away queue's
 * drainer that lives in it, with it. That is what a worker meets on opening a screen they had not
 * opened before the signal went. It is deliberately not told what went wrong: a failed chunk and a
 * screen that threw both end here, and the message does not pretend to know which.
 */
class ScreenBoundary extends Component<ScreenBoundaryProps, { failed: boolean }> {
  override state = { failed: false };

  static getDerivedStateFromError(): { failed: boolean } {
    return { failed: true };
  }

  override render(): ReactNode {
    if (!this.state.failed) return this.props.children;
    return (
      <Card className="p-2">
        <ErrorState
          title={`${this.props.name} couldn't be opened`}
          message="A screen is downloaded the first time it is opened, so without a signal it may not load. Check your connection and try again. Put-aways already saved on this phone keep syncing."
          onRetry={this.props.onRetry}
        />
      </Card>
    );
  }
}

/**
 * A lazily loaded screen that says when it could not be loaded and can try again.
 *
 * `lazy` remembers a failed import for good: rendering the same lazy component again rethrows the
 * old rejection without asking for the module. So Retry makes a new one, and remounts under a new
 * key. The first one is made here, once, so a screen that has loaded opens again instantly.
 */
export function lazyScreen(name: string, load: () => Promise<{ default: ComponentType }>): ComponentType {
  let Screen = lazy(load);
  return function LazyScreen() {
    const [attempt, setAttempt] = useState(0);
    return (
      <ScreenBoundary
        key={attempt}
        name={name}
        onRetry={() => {
          Screen = lazy(load);
          setAttempt((previous) => previous + 1);
        }}
      >
        <Suspense fallback={<RouteFallback />}>
          <Screen />
        </Suspense>
      </ScreenBoundary>
    );
  };
}
