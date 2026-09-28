/**
 * This app's own icon module — re-exports the shared set and adds the two glyphs nothing else in
 * the platform needs yet (`@opsgrids/ui`'s `icons.tsx` own doc comment: "a product that needs a
 * glyph nothing else needs adds it to its own `src/ui/icons.tsx`... it does not redraw one that
 * already exists"). Same viewBox, stroke width and cap/join as every icon in `@opsgrids/ui`, so a
 * `CameraIcon` sits beside a `PackageIcon` without looking like it came from somewhere else.
 */
import type { ReactNode, SVGProps } from "react";

// eslint-disable-next-line react-refresh/only-export-components -- every export in `@opsgrids/ui/icons`, and every export below, is an icon component; nothing non-component ever lands in this file.
export * from "@opsgrids/ui/icons";

type IconProps = SVGProps<SVGSVGElement> & { title?: string | undefined };

function LocalBase({ title, children, ...props }: IconProps & { children: ReactNode }) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={1.7}
      strokeLinecap="round"
      strokeLinejoin="round"
      width={20}
      height={20}
      aria-hidden={title ? undefined : true}
      role={title ? "img" : undefined}
      {...props}
    >
      {title ? <title>{title}</title> : null}
      {children}
    </svg>
  );
}

/** The scan screen's camera view — and the manual-entry toggle beside it. */
export const CameraIcon = (p: IconProps) => (
  <LocalBase {...p}>
    <path d="M4 8a2 2 0 0 1 2-2h1.5l1-1.5h7l1 1.5H18a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z" />
    <circle cx="12" cy="13" r="3.3" />
  </LocalBase>
);

/** The offline banner: scans are queued, not lost. */
export const WifiOffIcon = (p: IconProps) => (
  <LocalBase {...p}>
    <path d="M2 8.5c2-1.8 4.6-3 7.5-3.4M22 8.5a15.8 15.8 0 0 0-5-3.1M5 12.5a10.6 10.6 0 0 1 4.3-2.2M19 12.5a10.6 10.6 0 0 0-2.7-2M8.5 16.3a5.6 5.6 0 0 1 3.5-1.3c.7 0 1.4.13 2 .37M12 20h.01M2 2l20 20" />
  </LocalBase>
);
