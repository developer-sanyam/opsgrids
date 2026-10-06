import { useCallback, useRef } from "react";

import { TAP_SLOP_PX } from "@/lib/floorLayoutEditor";

interface PointerPoint {
  pointerId: number;
  clientX: number;
  clientY: number;
}

interface Pending extends PointerPoint {
  act: () => void;
  /** It has left the tap's slop: this is a pan, and will not be a tap whatever it does next. */
  panning: boolean;
  lastX: number;
  lastY: number;
}

/**
 * Telling a finger's tap from its pan, and deferring the tap's action to the moment it lifts.
 *
 * A finger that lands on the plan has not yet said which it is. So a touch must not select, add a
 * walk node or connect one when it lands: a pan that happens to start on a node would do all
 * three. `begin` keeps the action; `end` runs it only if the same pointer lifted without ever
 * leaving `TAP_SLOP_PX` of where it landed; `cancel` drops it (the browser took the gesture over
 * and is panning by itself, and signals that with `pointercancel`, never a `pointerup`). A mouse
 * needs none of this and acts on press.
 *
 * Where the canvas holds the touch itself (Edit by touch), nothing pans the plan unless it does,
 * so `move` reports how far the pointer travelled since the last call once it has left the slop.
 */
export function usePointerTap() {
  const pending = useRef<Pending | null>(null);

  const begin = useCallback((down: PointerPoint, act: () => void) => {
    pending.current = {
      pointerId: down.pointerId,
      clientX: down.clientX,
      clientY: down.clientY,
      act,
      panning: false,
      lastX: down.clientX,
      lastY: down.clientY,
    };
  }, []);

  const move = useCallback((at: PointerPoint): { dx: number; dy: number } | null => {
    const gesture = pending.current;
    if (gesture === null || gesture.pointerId !== at.pointerId) return null;
    const travelled = Math.hypot(at.clientX - gesture.clientX, at.clientY - gesture.clientY);
    if (!gesture.panning && travelled <= TAP_SLOP_PX) return null;
    const delta = { dx: at.clientX - gesture.lastX, dy: at.clientY - gesture.lastY };
    gesture.panning = true;
    gesture.lastX = at.clientX;
    gesture.lastY = at.clientY;
    return delta;
  }, []);

  const end = useCallback((up: PointerPoint) => {
    const gesture = pending.current;
    pending.current = null;
    if (gesture === null || gesture.pointerId !== up.pointerId || gesture.panning) return;
    if (Math.hypot(up.clientX - gesture.clientX, up.clientY - gesture.clientY) <= TAP_SLOP_PX) gesture.act();
  }, []);

  const cancel = useCallback(() => {
    pending.current = null;
  }, []);

  return { begin, move, end, cancel };
}
