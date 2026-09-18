/**
 * The renderer's public surface — what the routes, the gallery's miniature and the editor's canvas
 * all draw through. Nothing outside this folder should reach for an individual section component.
 */

export { PageRenderer } from "./PageRenderer";
export type { PageRendererProps } from "./PageRenderer";
export { SectionRenderer } from "./SectionRenderer";
export type { SectionRendererProps } from "./SectionRenderer";
export { StoreRenderer } from "./StoreRenderer";
export type { StoreRendererProps } from "./StoreRenderer";
export { storeHref } from "./href";
export { ASSET_BASE, resolveImageSrc } from "./assetSrc";
export { ThemeFrame } from "./ThemeFrame";
export { SECTION_COMPONENTS, sectionComponent } from "./registry";
export { resolveProducts, emptySourceHint } from "./products";
export { T, step, tokenStyle, TOKEN_VARS } from "./tokens";
export type { SectionComponent, SectionRenderProps } from "./types";
