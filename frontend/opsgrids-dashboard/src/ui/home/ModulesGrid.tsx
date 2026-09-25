/**
 * The phone-only "Your Modules" launcher (mock-2, mock-4): the products this caller can actually
 * open, from `usePlatformModules()` — entitled **and** granted, the same fan-out AppShell v2's own
 * nav model uses. `usePlatformModules` hands back an `iconKey` string rather than a component, so
 * that `@opsgrids/client` never imports the design system; this is the one place in this app that
 * maps it back onto `@opsgrids/ui`'s icon set.
 *
 * Desktop and tablet already list every product in the sidebar, so this section renders only
 * below the brief's tablet boundary (768px) — `md:hidden`, not a media-query hook, because it is a
 * visibility switch on one tree, not two different DOMs to pick between.
 */
import type { ComponentType } from "react";
import { Link } from "react-router-dom";

import { usePlatformModules } from "@opsgrids/client";
import {
  ActivityIcon,
  BoxIcon,
  BriefcaseIcon,
  GlobeIcon,
  LifeBuoyIcon,
  MegaphoneIcon,
  NetworkIcon,
  ReceiptIcon,
  ShieldIcon,
  UsersIcon,
} from "@opsgrids/ui";
import type { IconProps } from "@opsgrids/ui";

/** `PlatformProduct.iconKey` (`opsgrids-client/src/platform/catalogue.ts`) → this design system's
 * icon. `storefront`'s `"store"` key has no dedicated icon yet, so it falls back to `BoxIcon`. */
const ICONS: Record<string, ComponentType<IconProps>> = {
  briefcase: BriefcaseIcon,
  box: BoxIcon,
  users: UsersIcon,
  receipt: ReceiptIcon,
  network: NetworkIcon,
  "life-buoy": LifeBuoyIcon,
  globe: GlobeIcon,
  shield: ShieldIcon,
  megaphone: MegaphoneIcon,
  activity: ActivityIcon,
};

/** Nothing to launch (no product both activated and granted): the section does not render at
 * all, rather than an empty grid with a heading over it. */
export function ModulesGrid() {
  const modules = usePlatformModules();
  if (modules.length === 0) return null;

  return (
    <section aria-label="Your modules" className="flex flex-col gap-3 md:hidden">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-bold text-ink">Your Modules</h2>
        <Link to="/products" className="text-sm font-semibold text-accent-ink transition-colors hover:text-accent">
          View All
        </Link>
      </div>
      <ul className="grid list-none grid-cols-3 gap-3 p-0">
        {modules.map((module) => {
          const Icon = ICONS[module.iconKey] ?? BoxIcon;
          return (
            <li key={module.key}>
              <a
                href={module.href}
                className="flex flex-col items-center gap-2 rounded-card bg-surface p-4 text-center shadow-card transition-colors hover:bg-ground focus-visible:bg-ground"
              >
                <span aria-hidden className="grid size-10 place-items-center rounded-tile bg-accent-soft text-accent-ink">
                  <Icon className="size-5" />
                </span>
                <span className="text-xs font-semibold text-ink-soft">{module.label}</span>
              </a>
            </li>
          );
        })}
      </ul>
    </section>
  );
}
