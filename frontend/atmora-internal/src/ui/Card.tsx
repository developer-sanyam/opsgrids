import type { HTMLAttributes } from "react";

import { cn } from "@/lib/cn";

type Tone = "surface" | "dark";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: Tone;
}

/**
 * The base surface: white, large radius, soft low shadow. `tone="dark"` is the emphasis card —
 * near-black with white text — used once or twice per screen for a headline metric.
 */
export function Card({ tone = "surface", className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-card",
        tone === "surface" && "bg-surface shadow-card",
        tone === "dark" && "bg-ink text-white",
        className,
      )}
      {...props}
    />
  );
}
