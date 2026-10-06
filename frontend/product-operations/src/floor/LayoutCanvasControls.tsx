import { CANVAS_ZOOM_STEPS } from "@/lib/floorLayoutEditor";
import { Button, Switch } from "@/ui";

interface LayoutCanvasControlsProps {
  zoomIndex: number;
  onZoomIndex: (index: number) => void;
  /** Shown only where the canvas can be edited: touch is irrelevant to a read-only plan. */
  touchEditable: boolean;
  touchEdit: boolean;
  onTouchEdit: (on: boolean) => void;
}

/**
 * Zoom and the touch switch, above the canvas.
 *
 * Zoom is the canvas's width: past "Fit" the plan is wider than its card and the card scrolls,
 * which is also how a finger pans it — the browser's own scrolling, no gesture code. The touch
 * switch settles the one thing a small screen cannot do both of: a finger that starts on the
 * canvas either scrolls the page or moves a shape. Off (the default) the page scrolls and a tap
 * selects; on, the canvas takes the touch, so shapes move and walk nodes can be drawn. A mouse or
 * pen is never affected.
 */
export function LayoutCanvasControls({
  zoomIndex,
  onZoomIndex,
  touchEditable,
  touchEdit,
  onTouchEdit,
}: LayoutCanvasControlsProps) {
  const last = CANVAS_ZOOM_STEPS.length - 1;
  const zoom = CANVAS_ZOOM_STEPS[zoomIndex] ?? 1;
  return (
    <div className="flex flex-col gap-2 px-2 pt-2 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Zoom">
        <Button size="sm" variant="secondary" onClick={() => onZoomIndex(zoomIndex - 1)} disabled={zoomIndex <= 0}>
          Zoom out
        </Button>
        <Button size="sm" variant="secondary" onClick={() => onZoomIndex(zoomIndex + 1)} disabled={zoomIndex >= last}>
          Zoom in
        </Button>
        <Button size="sm" variant="secondary" onClick={() => onZoomIndex(0)} disabled={zoomIndex === 0}>
          Fit
        </Button>
        <span className="tnum text-caption text-muted">{Math.round(zoom * 100)}%</span>
      </div>
      {touchEditable ? (
        <Switch
          className="sm:max-w-xs"
          checked={touchEdit}
          onChange={onTouchEdit}
          label="Edit by touch"
          description={
            touchEdit
              ? "Touch moves shapes and draws the walk graph, so it no longer scrolls the page here."
              : "Touch scrolls the page and selects shapes. Turn on to move shapes and draw the walk graph."
          }
        />
      ) : null}
    </div>
  );
}
