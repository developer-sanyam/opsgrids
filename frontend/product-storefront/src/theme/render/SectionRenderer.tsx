import { Component } from "react";
import type { ErrorInfo, ReactNode } from "react";

import type { Category, Product } from "@/api/types";
import type { SectionInstance, StoreMode } from "../schema";
import { sectionDef } from "../sections";
import { StoreHeading, StoreText } from "./primitives";
import { sectionComponent } from "./registry";
import { T, step } from "./tokens";

/** The labelled stand-in for a section this build cannot draw. Never blank, never a crash. */
function SectionPlaceholder({ type, reason }: { type: string; reason: string }) {
  return (
    <section
      style={{
        border: `1px dashed ${T.border}`,
        borderRadius: T.radius,
        padding: step(1.2),
        display: "flex",
        flexDirection: "column",
        gap: step(0.3),
        height: "100%",
        justifyContent: "center",
      }}
    >
      <StoreHeading level={3} size="sm">
        Section unavailable
      </StoreHeading>
      <StoreText muted small>
        {reason}
      </StoreText>
      <StoreText muted small style={{ fontVariantNumeric: "tabular-nums" }}>
        Type: {type}
      </StoreText>
    </section>
  );
}

interface BoundaryProps {
  type: string;
  children: ReactNode;
}

interface BoundaryState {
  failed: boolean;
}

/**
 * One boundary per section.
 *
 * A store document is merchant data; a setting can be any shape a past build wrote. One section
 * that throws must cost that section and not the page a buyer is trying to order from — so the
 * boundary is here, around each instance, rather than once around the whole store.
 */
class SectionBoundary extends Component<BoundaryProps, BoundaryState> {
  override state: BoundaryState = { failed: false };

  static getDerivedStateFromError(): BoundaryState {
    return { failed: true };
  }

  override componentDidCatch(error: Error, info: ErrorInfo) {
    // Surfaced for the merchant's own debugging; the buyer sees the placeholder.
    console.error(`Storefront section "${this.props.type}" failed to render`, error, info);
  }

  override render() {
    if (this.state.failed) {
      return (
        <SectionPlaceholder
          type={this.props.type}
          reason="This block couldn't be drawn from the settings saved on it. Everything else on the page is unaffected."
        />
      );
    }
    return this.props.children;
  }
}

export interface SectionRendererProps {
  section: SectionInstance;
  products: Product[];
  categories: Category[];
  stacked: boolean;
  /** The canvas width in px — the fine-grained companion to `stacked`. */
  width: number;
  /** Retail or trade, already narrowed by `resolveMode`. */
  mode: StoreMode;
}

/** Resolves a section instance to its component, or to a labelled placeholder. */
export function SectionRenderer({
  section,
  products,
  categories,
  stacked,
  width,
  mode,
}: SectionRendererProps) {
  const Component_ = sectionComponent(section.type);

  if (!Component_) {
    return (
      <SectionPlaceholder
        type={section.type}
        reason={
          sectionDef(section.type)
            ? "This block is declared in the section library but has no renderer in this build."
            : "This block came from a newer version of the storefront and this build doesn't know how to draw it."
        }
      />
    );
  }

  return (
    <SectionBoundary type={section.type}>
      <Component_
        settings={section.settings}
        products={products}
        categories={categories}
        stacked={stacked}
        width={width}
        mode={mode}
      />
    </SectionBoundary>
  );
}
