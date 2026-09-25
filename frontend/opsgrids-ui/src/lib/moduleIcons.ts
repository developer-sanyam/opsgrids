/**
 * Resolves `@opsgrids/client`'s platform product catalogue to one of this design system's own
 * icons — without either package importing the other (`.dependency-cruiser.cjs`: ui and client
 * never import each other). `usePlatformModules()` hands the shell a plain string `iconKey`
 * ("briefcase", "box"…); this is the one place that string becomes a component.
 */
import type { ComponentType, SVGProps } from "react";

import {
  ActivityIcon,
  BoxIcon,
  BriefcaseIcon,
  GlobeIcon,
  GridIcon,
  LifeBuoyIcon,
  MegaphoneIcon,
  NetworkIcon,
  ReceiptIcon,
  ShieldIcon,
  StoreIcon,
  UsersIcon,
} from "../icons";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

/** Keyed by `PlatformProduct.iconKey` (`opsgrids-client/src/platform/catalogue.ts`). */
const MODULE_ICONS: Record<string, IconType> = {
  briefcase: BriefcaseIcon,
  box: BoxIcon,
  users: UsersIcon,
  receipt: ReceiptIcon,
  network: NetworkIcon,
  "life-buoy": LifeBuoyIcon,
  globe: GlobeIcon,
  shield: ShieldIcon,
  store: StoreIcon,
  megaphone: MegaphoneIcon,
  activity: ActivityIcon,
};

/** A generic grid glyph for a key this set doesn't recognise yet — never a thrown error over a
 *  nav icon. */
export function moduleIcon(key: string): IconType {
  return MODULE_ICONS[key] ?? GridIcon;
}
