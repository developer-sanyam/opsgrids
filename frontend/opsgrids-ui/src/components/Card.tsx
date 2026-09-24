import type { HTMLAttributes } from "react";

import { cn } from "../lib/cn";

export type CardTone = "surface" | "dark";

interface CardProps extends HTMLAttributes<HTMLDivElement> {
  tone?: CardTone | undefined;
}

/**
 * The base surface: white, 12px radius, border-first. The hairline edge and the whisper of shadow
 * both come from `shadow-card`, so a card needs no border class and cannot drift from the chips
 * and fields that share the token.
 *
 * `tone="dark"` is the emphasis card — the ink colour with the surface colour as its text, which
 * inverts correctly in the dark theme — used once or twice per screen for a single focal panel.
 */
export function Card({ tone = "surface", className, ...props }: CardProps) {
  return (
    <div
      className={cn(
        "rounded-card",
        tone === "surface" && "bg-surface shadow-card",
        tone === "dark" && "bg-ink text-surface",
        className,
      )}
      {...props}
    />
  );
}
