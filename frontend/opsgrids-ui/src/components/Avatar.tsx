import { cn } from "../lib/cn";
import { initials } from "../lib/format";

export type AvatarSize = "xs" | "sm" | "md" | "lg";

const SIZES: Record<AvatarSize, string> = {
  xs: "size-6 text-caption",
  sm: "size-8 text-xs",
  md: "size-10 text-sm",
  lg: "size-12 text-base",
};

// The token families' soft/ink pairs — every one is AA in both themes (styles/tokens.test.ts).
// Deterministic per name so a person's colour is stable. Identity, not status: the tint says
// nothing about the person, so it never borrows the danger red.
const TINTS = [
  "bg-accent-soft text-accent-ink",
  "bg-neutral-soft text-neutral",
  "bg-late-soft text-late",
  "bg-present-soft text-present",
  "bg-info-soft text-info",
  "bg-cyan-soft text-cyan",
];

function tintFor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return TINTS[hash % TINTS.length] ?? TINTS[0] ?? "";
}

interface AvatarProps {
  name: string;
  size?: AvatarSize | undefined;
  className?: string | undefined;
}

/** An initials avatar. Alt/label carried by the surrounding name; marked decorative here. */
export function Avatar({ name, size = "md", className }: AvatarProps) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center justify-center rounded-full font-bold select-none",
        SIZES[size],
        tintFor(name),
        className,
      )}
      aria-hidden
      title={name}
    >
      {initials(name)}
    </span>
  );
}
