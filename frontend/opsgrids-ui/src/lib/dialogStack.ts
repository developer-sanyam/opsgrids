/**
 * The stack of open dialogs.
 *
 * A dialog opened from inside another one — "add a team member" from a create-deal form — is the
 * case every per-instance keyboard handler gets wrong: both dialogs listen on the document, the
 * outer one registered first, so Escape closes the wrong dialog and Tab is trapped by whichever
 * panel answered first. The order of `addEventListener` calls is not the order the user sees.
 *
 * So the depth is tracked here instead, module-level and outside React: a dialog registers a layer
 * when it opens and drops it when it closes, and every handler asks `isTopDialogLayer` before it
 * acts. Only the topmost dialog closes on Escape, traps Tab and counts a click as outside; when it
 * goes, the dialog beneath is top again and resumes — without re-running an effect or re-rendering.
 *
 * Lives beside the other helpers rather than in `Dialog.tsx` so that file exports components only
 * and Fast Refresh keeps working — the same reason `field.ts` and `FormField.tsx` are two files.
 * It also means the stack survives a hot reload of the component.
 */

/** A dialog's place in the stack. Opaque: identity is the whole of it. */
export interface DialogLayer {
  readonly depth: number;
}

const layers: DialogLayer[] = [];

/** Register a newly opened dialog as the topmost one. */
export function openDialogLayer(): DialogLayer {
  const layer: DialogLayer = { depth: layers.length };
  layers.push(layer);
  return layer;
}

/**
 * Drop a dialog's layer. Removal is by identity, not by popping, so a dialog closing out of order
 * — a parent unmounting the whole tree while a nested dialog is open — leaves the rest intact.
 */
export function closeDialogLayer(layer: DialogLayer): void {
  const index = layers.indexOf(layer);
  if (index !== -1) layers.splice(index, 1);
}

/** Is this dialog the one the keyboard belongs to? False while a dialog opened from it is up. */
export function isTopDialogLayer(layer: DialogLayer): boolean {
  return layers.length > 0 && layers[layers.length - 1] === layer;
}

/** How many dialogs are open. For tests and for anything that needs to know the page is covered. */
export function openDialogCount(): number {
  return layers.length;
}
