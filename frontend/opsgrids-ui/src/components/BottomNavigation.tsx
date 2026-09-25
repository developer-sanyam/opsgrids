import type { ComponentType, SVGProps } from "react";
import { NavLink } from "react-router-dom";

import { cn } from "../lib/cn";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

export interface BottomNavItem {
  key: string;
  label: string;
  icon: IconType;
  /** A route: rendered as a `NavLink`, current-page aware. */
  to?: string | undefined;
  /** A disclosure instead of a route — Modules, the quick-action sheet, More. */
  onSelect?: (() => void) | undefined;
  /** The centre "+": raised, filled, above the bar. */
  raised?: boolean | undefined;
  /** For a disclosure button: whether the sheet it opens is open. */
  expanded?: boolean | undefined;
}

interface BottomNavigationProps {
  items: BottomNavItem[];
}

/**
 * The mobile bottom bar: up to five destinations, evenly spaced, safe-area padded. Fixed to the
 * viewport — `AppShell` reserves the matching space at the foot of the page
 * (`.shell-content-bottom-pad`) so nothing sits behind it.
 */
export function BottomNavigation({ items }: BottomNavigationProps) {
  return (
    <nav
      aria-label="Primary mobile navigation"
      className="shell-bottom-nav fixed inset-x-0 bottom-0 z-30 flex border-t border-line bg-surface md:hidden"
    >
      {items.map((item) => {
        const Icon = item.icon;
        if (item.to) {
          return (
            <NavLink
              key={item.key}
              to={item.to}
              end
              className={({ isActive }) =>
                cn(
                  "flex flex-1 flex-col items-center justify-center gap-0.5 text-caption font-semibold",
                  isActive ? "text-accent" : "text-muted",
                )
              }
            >
              <Icon className="size-5" aria-hidden />
              {item.label}
            </NavLink>
          );
        }
        return (
          <button
            key={item.key}
            type="button"
            onClick={item.onSelect}
            aria-expanded={item.expanded}
            className={cn(
              "flex flex-1 flex-col items-center justify-center gap-0.5 text-caption font-semibold text-muted",
              item.raised && "relative",
            )}
          >
            {item.raised ? (
              <span className="-mt-6 grid size-11 place-items-center rounded-full bg-accent text-on-accent shadow-float">
                <Icon className="size-5" aria-hidden />
              </span>
            ) : (
              <Icon className="size-5" aria-hidden />
            )}
            {item.label}
          </button>
        );
      })}
    </nav>
  );
}
