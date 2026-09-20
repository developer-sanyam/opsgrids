/**
 * The drag payload the palette writes and the canvas reads.
 *
 * A custom MIME type rather than `text/plain`: a section type dragged out of the editor and
 * dropped into a text field should do nothing, and a stray string dragged in from the desktop
 * must never look like a section.
 */
export const SECTION_DRAG_MIME = "application/x-opsgrids-section";

/**
 * The same payload again, as a MIME suffix.
 *
 * `dataTransfer.getData` returns an empty string during `dragover` in every browser — the data is
 * protected until the drop — but `dataTransfer.types` is always readable. Writing the section type
 * into a second, empty entry is how the canvas can draw an accurately-sized drop outline before
 * anything has been dropped. Types are already lowercase in the schema; browsers lowercase the
 * whole list regardless, which is why this cannot carry anything but a type key.
 */
export function sectionDragHint(type: string): string {
  return `${SECTION_DRAG_MIME}+${type.toLowerCase()}`;
}

/** The section type a drag is carrying, read from `dataTransfer.types`. */
export function dragTypeFrom(types: readonly string[]): string | null {
  const prefix = `${SECTION_DRAG_MIME}+`;
  const hint = types.find((entry) => entry.startsWith(prefix));
  return hint === undefined ? null : hint.slice(prefix.length);
}
