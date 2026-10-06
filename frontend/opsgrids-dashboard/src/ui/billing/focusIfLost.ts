/**
 * Move keyboard focus to `target` when the control that had it is gone: removed (a paid invoice's
 * button), disabled (every Pay now while a payment runs), or never returned (a provider's window
 * closing leaves focus on the page body). Focus somewhere the person put it is never taken.
 *
 * `focus()` also scrolls the target into view, so on a phone the notice that says what happened is
 * on screen rather than above the card the person tapped.
 */
export function focusIfLost(target: HTMLElement | null): void {
  if (!target) return;
  const active = document.activeElement;
  const lost =
    active === null ||
    active === document.body ||
    !active.isConnected ||
    (active instanceof HTMLButtonElement && active.disabled);
  if (lost) target.focus();
}
