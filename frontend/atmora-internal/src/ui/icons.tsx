/**
 * A small inline-SVG icon set. Thin, rounded strokes for a consistent line weight; `currentColor`
 * throughout so colour comes from the surrounding text class.
 */
import type { SVGProps } from "react";

type IconProps = SVGProps<SVGSVGElement> & { title?: string };

function Base({ title, children, ...props }: IconProps & { children: React.ReactNode }) {
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

export const ArrowUp = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 19V5M6 11l6-6 6 6" />
  </Base>
);

export const ArrowDown = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 5v14M6 13l6 6 6-6" />
  </Base>
);

export const ArrowRightIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 12h14M13 6l6 6-6 6" />
  </Base>
);

export const ArrowLeftIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M19 12H5M11 6l-6 6 6 6" />
  </Base>
);

export const SearchIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="11" cy="11" r="7" />
    <path d="m20 20-3-3" />
  </Base>
);

export const ChevronDown = (p: IconProps) => (
  <Base {...p}>
    <path d="m6 9 6 6 6-6" />
  </Base>
);

export const ChevronRight = (p: IconProps) => (
  <Base {...p}>
    <path d="m9 6 6 6-6 6" />
  </Base>
);

export const ChevronsUpDown = (p: IconProps) => (
  <Base {...p}>
    <path d="m7 15 5 5 5-5M7 9l5-5 5 5" />
  </Base>
);

/** Overview — a gauge / speedometer. */
export const GaugeIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 13 16 9" />
    <path d="M4.5 18a9 9 0 1 1 15 0" />
    <circle cx="12" cy="13" r="1.4" fill="currentColor" stroke="none" />
  </Base>
);

/** Accounts — a pair of buildings. */
export const BuildingsIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 21V8l6-3v16" />
    <path d="M9 21V9l10 3v9" />
    <path d="M3 21h18M13 15h2M13 18h2" />
  </Base>
);

/** Subscriptions — stacked layers. */
export const LayersIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="m12 3 9 5-9 5-9-5 9-5Z" />
    <path d="m3 13 9 5 9-5M3 17l9 5 9-5" />
  </Base>
);

/** Plans & pricing — a price tag. */
export const TagIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 12V4a1 1 0 0 1 1-1h8l9 9-9 9-9-9Z" />
    <circle cx="7.5" cy="7.5" r="1.5" fill="currentColor" stroke="none" />
  </Base>
);

/** Grants & credits — a gift. */
export const GiftIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M20 12v8a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1v-8" />
    <path d="M2 8h20v4H2zM12 21V8M12 8S10.5 4 8 4a2 2 0 1 0 0 4Zm0 0s1.5-4 4-4a2 2 0 1 1 0 4Z" />
  </Base>
);

/** Audit — a shield with a check. */
export const ShieldIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3 5 6v5c0 4.5 3 8 7 10 4-2 7-5.5 7-10V6l-7-3Z" />
    <path d="m9 12 2 2 4-4" />
  </Base>
);

export const PauseIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 5v14M15 5v14" />
  </Base>
);

export const PlayIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M7 5v14l12-7-12-7Z" />
  </Base>
);

export const RepeatIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M17 3l4 4-4 4" />
    <path d="M21 7H8a4 4 0 0 0-4 4v1M7 21l-4-4 4-4" />
    <path d="M3 17h13a4 4 0 0 0 4-4v-1" />
  </Base>
);

export const ReceiptIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M5 3v18l2-1.5L9 21l2-1.5L13 21l2-1.5L17 21l2-1.5V3l-2 1.5L15 3l-2 1.5L11 3 9 4.5 7 3 5 4.5Z" />
    <path d="M8 8h8M8 12h8M8 16h5" />
  </Base>
);

export const WalletIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 7a2 2 0 0 1 2-2h13a1 1 0 0 1 1 1v2" />
    <path d="M3 7v10a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-6a2 2 0 0 0-2-2H5a2 2 0 0 1-2-2Z" />
    <circle cx="16" cy="13" r="1.2" fill="currentColor" stroke="none" />
  </Base>
);

export const ClockIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="M12 7v5l3 2" />
  </Base>
);

export const MapPinIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M20 10c0 6-8 12-8 12s-8-6-8-12a8 8 0 0 1 16 0Z" />
    <circle cx="12" cy="10" r="2.5" />
  </Base>
);

export const UsersIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2" />
    <circle cx="9" cy="7" r="4" />
    <path d="M22 21v-2a4 4 0 0 0-3-3.87M16 3.13a4 4 0 0 1 0 7.75" />
  </Base>
);

export const LockIcon = (p: IconProps) => (
  <Base {...p}>
    <rect x="4" y="10" width="16" height="11" rx="2" />
    <path d="M8 10V7a4 4 0 0 1 8 0v3" />
    <circle cx="12" cy="15.5" r="1.2" fill="currentColor" stroke="none" />
  </Base>
);

export const AlertTriangleIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 3 2 20h20L12 3Z" />
    <path d="M12 9v5" />
    <circle cx="12" cy="17" r="1" fill="currentColor" stroke="none" />
  </Base>
);

export const CheckIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M20 6 9 17l-5-5" />
  </Base>
);

export const CheckCircleIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="9" />
    <path d="m8.5 12 2.5 2.5 4.5-5" />
  </Base>
);

export const ExternalLinkIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M14 4h6v6M20 4l-9 9M18 14v5a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V7a1 1 0 0 1 1-1h5" />
  </Base>
);

export const MoreHorizontalIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="5" cy="12" r="1.4" fill="currentColor" stroke="none" />
    <circle cx="12" cy="12" r="1.4" fill="currentColor" stroke="none" />
    <circle cx="19" cy="12" r="1.4" fill="currentColor" stroke="none" />
  </Base>
);

export const CommandIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M15 6a3 3 0 1 1 3 3h-3V6zM9 6a3 3 0 1 0-3 3h3V6zm0 12a3 3 0 1 1-3-3h3v3zm6 0a3 3 0 1 0 3-3h-3v3zM9 9h6v6H9z" />
  </Base>
);

export const FilterIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 5h18l-7 8v6l-4-2v-4z" />
  </Base>
);

export const PlusIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M12 5v14M5 12h14" />
  </Base>
);

export const SunIcon = (p: IconProps) => (
  <Base {...p}>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2m0 16v2M4.9 4.9l1.4 1.4m11.4 11.4 1.4 1.4M2 12h2m16 0h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
  </Base>
);

export const MoonIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
  </Base>
);

export const MenuIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M4 6h16M4 12h16M4 18h16" />
  </Base>
);

export const CloseIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M6 6l12 12M18 6 6 18" />
  </Base>
);

export const EyeIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z" />
    <circle cx="12" cy="12" r="3" />
  </Base>
);

export const EyeOffIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M3 3l18 18M10.6 10.6a3 3 0 0 0 4.2 4.2M9.4 5.2A9.6 9.6 0 0 1 12 5c6.5 0 10 7 10 7a17 17 0 0 1-3 3.9M6 6.4A17 17 0 0 0 2 12s3.5 7 10 7a9.7 9.7 0 0 0 3.4-.6" />
  </Base>
);

export const LogOutIcon = (p: IconProps) => (
  <Base {...p}>
    <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4M16 17l5-5-5-5M21 12H9" />
  </Base>
);

/** The operator logomark — a violet square with a control-sliders glyph (matches the favicon). */
export const LogoMark = (props: SVGProps<SVGSVGElement>) => (
  <svg viewBox="0 0 32 32" width={32} height={32} aria-hidden {...props}>
    <rect width="32" height="32" rx="9" fill="var(--color-accent)" />
    <g fill="none" stroke="#ffffff" strokeWidth={2.2} strokeLinecap="round">
      <path d="M8 11h16M8 16h16M8 21h16" />
      <circle cx="13" cy="11" r="2.4" fill="var(--color-accent)" />
      <circle cx="20" cy="16" r="2.4" fill="var(--color-accent)" />
      <circle cx="12" cy="21" r="2.4" fill="var(--color-accent)" />
    </g>
  </svg>
);
