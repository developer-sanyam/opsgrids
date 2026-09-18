export type DeviceKey = "desktop" | "tablet" | "mobile";

/**
 * The preview widths.
 *
 * `desktop` is `null` — it means "whatever the browser gives us", so the desktop preview is the
 * genuine responsive case rather than a second fixed frame. The two fixed widths are the ones that
 * matter for this product: 834 keeps the 12-column grid, 390 is below `STACK_WIDTH` and therefore
 * stacks, which is the behaviour a merchant most needs to see before they publish.
 */
export const DEVICE_WIDTHS: Record<DeviceKey, number | null> = {
  desktop: null,
  tablet: 834,
  mobile: 390,
};
