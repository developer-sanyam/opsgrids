/**
 * WebGL feature detection for the warehouse 3D view (Fulfilment F9 MVP).
 *
 * F9 has no server dependency of its own to fail on — the only thing that can stop the 3D canvas
 * from working is the browser itself (WebGL disabled, an old device, a locked-down kiosk). This
 * is checked once, before `@react-three/fiber`'s `Canvas` is ever mounted, so a browser without
 * WebGL never pays for the `three`/`@react-three/fiber`/`@react-three/drei` chunk at all — the
 * route lazy-imports the scene only after this returns `true` (`Warehouse3D.tsx`).
 */
export function hasWebGLSupport(doc: Document = document): boolean {
  try {
    const canvas = doc.createElement("canvas");
    const gl = canvas.getContext("webgl2") ?? canvas.getContext("webgl");
    return gl !== null;
  } catch {
    // A handful of browsers throw rather than return null for a disabled/blocklisted context.
    return false;
  }
}
