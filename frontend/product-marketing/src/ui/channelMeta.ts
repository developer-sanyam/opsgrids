import type { ComponentType, SVGProps } from "react";

import type { Channel } from "@/api/types";
import { MailIcon, SearchIcon, Share2Icon, TicketIcon } from "./icons";

type IconType = ComponentType<SVGProps<SVGSVGElement>>;

/**
 * Each channel's fixed identity — a colour drawn from the semantic palette (never the violet
 * accent, which is reserved for actions) and an icon. These maps are the single source of truth so
 * the badge, the channel table and the breakdown bars all agree. Kept in a non-component module so
 * React Fast Refresh stays happy.
 */
export const CHANNEL_COLOR: Record<Channel, string> = {
  search: "var(--color-info)",
  social: "var(--color-cyan)",
  email: "var(--color-late)",
  events: "var(--color-present)",
};

export const CHANNEL_ICON: Record<Channel, IconType> = {
  email: MailIcon,
  social: Share2Icon,
  search: SearchIcon,
  events: TicketIcon,
};

export const CHANNEL_TONE: Record<Channel, string> = {
  search: "bg-info-soft text-info",
  social: "bg-cyan-soft text-cyan",
  email: "bg-late-soft text-late",
  events: "bg-present-soft text-present",
};
