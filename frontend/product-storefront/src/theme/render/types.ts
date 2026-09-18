/**
 * What every section component is handed.
 *
 * A section must render correctly from this and nothing else: no context reads, no fetches, no
 * knowledge of which page or which theme it is on. That is what makes the same component usable by
 * the buyer-facing renderer, the gallery's miniature and the editor's canvas.
 */

import type { ReactNode } from "react";

import type { Category, Product } from "@/api/types";
import type { Settings } from "./settings";

export interface SectionRenderProps {
  settings: Settings;
  products: Product[];
  categories: Category[];
  /** True when the canvas is below `STACK_WIDTH` — sections that reflow read it. */
  stacked: boolean;
}

export type SectionComponent = (props: SectionRenderProps) => ReactNode;
