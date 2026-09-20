/**
 * What every section component is handed.
 *
 * A section must render correctly from this and nothing else: no fetches, and no knowledge of which
 * page or which theme it is on. That is what makes the same component usable by the buyer-facing
 * renderer, the gallery's miniature and the editor's canvas.
 *
 * **The one amendment, made deliberately.** This comment used to say "no context reads" without
 * qualification. It now says: a section may read **exactly one** context, `LineListContext`, and
 * only through `useLineList()` from `@/store/lineList`. Nothing else. The exception is narrow
 * enough to keep the guarantee above because that hook returns a **no-op implementation when no
 * provider is mounted** — an empty list whose `add` does nothing — so a section still renders
 * correctly from props alone, in the miniature, on the canvas and in a test with no provider in
 * sight. `StoreRenderer` is the only component that mounts the real provider.
 *
 * A second context would not have that property and is not permitted here. If a section needs
 * something else from its surroundings, it goes on `SectionRenderProps` — visible in the type,
 * threaded by the renderers, and impossible to read by accident. Adding a `useContext` inside a
 * section without amending this comment is how the rule dies quietly; amend it or thread it.
 */

import type { ReactNode } from "react";

import type { Category, Product } from "@/api/types";
import type { StoreMode } from "../schema";
import type { Settings } from "./settings";

export interface SectionRenderProps {
  settings: Settings;
  products: Product[];
  categories: Category[];
  /** True when the canvas is below `STACK_WIDTH` — sections that reflow read it. */
  stacked: boolean;
  /** The canvas width in px. `stacked` is the coarse answer; this is the fine one — a section
   *  designed at 390 cannot get there from a boolean that is also true at 767. */
  width: number;
  /** Retail or trade. Decides the card's primary action and the store's vocabulary. */
  mode: StoreMode;
}

export type SectionComponent = (props: SectionRenderProps) => ReactNode;
