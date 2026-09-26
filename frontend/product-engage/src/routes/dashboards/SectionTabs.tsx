import { NavLink } from "react-router-dom";

import { cn } from "@opsgrids/ui";

/**
 * The tab row the Communication screens share: real links, so each tab is a page a person can open
 * in a new tab, and the router marks the current one with `aria-current="page"`. Not `Tabs`: that
 * component switches a panel in place, and these switch screens.
 */

export type Section = "hub" | "whatsapp" | "email";

interface SectionLink {
  label: string;
  to: string;
  end?: boolean;
}

const SECTIONS: Record<Section, { name: string; links: SectionLink[] }> = {
  hub: {
    name: "Communication",
    links: [
      { label: "Overview", to: "/", end: true },
      { label: "WhatsApp", to: "/whatsapp", end: true },
      { label: "Email", to: "/email", end: true },
    ],
  },
  whatsapp: {
    name: "WhatsApp",
    links: [
      { label: "Overview", to: "/whatsapp", end: true },
      { label: "Conversations", to: "/whatsapp/inbox" },
      { label: "Templates", to: "/whatsapp/templates" },
      { label: "Automations", to: "/automations" },
      { label: "Analytics", to: "/whatsapp/analytics" },
      { label: "Settings", to: "/whatsapp/settings" },
    ],
  },
  email: {
    name: "Email",
    links: [
      { label: "Overview", to: "/email", end: true },
      { label: "Inbox", to: "/email/inbox" },
      { label: "Campaigns", to: "/email/campaigns" },
      { label: "Templates", to: "/email/templates" },
      { label: "Settings", to: "/email/settings" },
    ],
  },
};

export function SectionTabs({ section }: { section: Section }) {
  const { name, links } = SECTIONS[section];
  return (
    <nav
      aria-label={`${name} sections`}
      className="flex max-w-full gap-1 overflow-x-auto border-b border-line"
    >
      {links.map((link) => (
        <NavLink
          key={link.to}
          to={link.to}
          {...(link.end === true ? { end: true } : {})}
          className={({ isActive }) =>
            cn(
              "relative inline-flex h-11 shrink-0 items-center px-3 text-sm font-semibold whitespace-nowrap transition-colors",
              "after:absolute after:inset-x-3 after:bottom-0 after:h-0.5 after:rounded-full",
              "focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent",
              isActive ? "text-ink after:bg-accent" : "text-muted after:bg-transparent hover:text-ink",
            )
          }
        >
          {link.label}
        </NavLink>
      ))}
    </nav>
  );
}
