/** The nearest ancestor of `from` that scrolls vertically, else the page itself. */
function verticalScroller(from: Element): Element {
  for (let node = from.parentElement; node !== null; node = node.parentElement) {
    const { overflowY } = getComputedStyle(node);
    if ((overflowY === "auto" || overflowY === "scroll") && node.scrollHeight > node.clientHeight) return node;
  }
  return document.scrollingElement ?? document.documentElement;
}

/**
 * Moves the view by a finger's travel: the canvas's own horizontal scroller by `dx`, whatever
 * scrolls the page vertically by `dy`.
 *
 * This is what a touch that the canvas has taken for itself (`touch-action: none`, which is how a
 * drag can move a shape) does in place of the browser's own panning. Chrome ignores `touch-action`
 * on the shapes *inside* an SVG, so the canvas cannot be left to pan natively and still hand a
 * shape the drags aimed at it: it takes every touch, and pans here.
 */
export function panCanvas(region: HTMLElement | null, dx: number, dy: number): void {
  if (region === null) return;
  region.scrollLeft -= dx;
  const scroller = verticalScroller(region);
  scroller.scrollTop -= dy;
}
