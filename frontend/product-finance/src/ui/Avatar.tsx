import { initials } from "@/lib/format";
import { cn } from "@/lib/cn";

type Size = "sm" | "md" | "lg";

const SIZES: Record<Size, string> = {
  sm: "size-8 text-xs",
  md: "size-10 text-sm",
  lg: "size-12 text-base",
};

// A few restrained, on-brand tints. Deterministic per name so a party's colour is stable.
const TINTS = [
  "bg-accent-soft text-accent-ink",
  "bg-ink text-white",
  "bg-late-soft text-late",
  "bg-present-soft text-present",
  "bg-[#e7eef7] text-[#2a4a6b]",
  "bg-[#f0e7f7] text-[#5a3a6b]",
];

function tintFor(name: string): string {
  let hash = 0;
  for (let i = 0; i < name.length; i += 1) hash = (hash * 31 + name.charCodeAt(i)) >>> 0;
  return TINTS[hash % TINTS.length]!;
}

interface AvatarProps {
  name: string;
  size?: Size;
  className?: string;
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
