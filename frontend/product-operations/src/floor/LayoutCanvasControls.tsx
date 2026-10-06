import { CANVAS_ZOOM_STEPS } from "@/lib/floorLayoutEditor";
import { Button, Switch } from "@/ui";

interface LayoutCanvasControlsProps {
  zoomIndex: number;
  onZoomIndex: (index: number) => void;
  /** There is a plan to zoom into: zoom only means something once a location is placed. */
  zoomable: boolean;
  /** Shown only where the canvas can be edited: touch is irrelevant to a read-only plan. */
  touchEditable: boolean;
  touchEdit: boolean;
  onTouchEdit: (on: boolean) => void;
}

/**
 * Zoom and the touch switch, above the canvas.
 *
 * Zoom is the canvas's width: past "Fit" the plan is wider than its card and the card scrolls.
 * The touch switch settles the one thing a small screen cannot do both of: a finger that starts on
 * a shape either pans the plan or drags the shape. A tap always selects, adds a walk node or
 * connects one, switch or no switch, and a finger that starts anywhere but on the selected shape
 * always pans. Off (the default) the browser pans, everywhere, as it does for any page. On, the
 * canvas holds the touch so that a drag on the selected shape can move or resize it, and pans the
 * plan itself for every other drag. A mouse is never affected.
 */
export function LayoutCanvasControls({
  zoomIndex,
  onZoomIndex,
  zoomable,
  touchEditable,
  touchEdit,
  onTouchEdit,
}: LayoutCanvasControlsProps) {
  const last = CANVAS_ZOOM_STEPS.length - 1;
  const zoom = CANVAS_ZOOM_STEPS[zoomIndex] ?? 1;
  return (
    <div className="flex flex-col gap-2 px-2 pt-2 sm:flex-row sm:flex-wrap sm:items-start sm:justify-between">
      {zoomable ? (
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
      ) : null}
      {touchEditable ? (
        <Switch
          className="sm:max-w-xs"
          checked={touchEdit}
          onChange={onTouchEdit}
          label="Edit by touch"
          description={
            touchEdit
              ? "Drag the selected shape to move or resize it. Drag anywhere else to pan. Tap to select."
              : "Touch pans the plan and a tap selects. Turn on to drag the selected shape to move or resize it."
          }
        />
      ) : null}
    </div>
  );
}
