import type { CSSProperties, ReactNode } from "react";
import { Link } from "react-router-dom";

import { classifyHref } from "./linkList";

/**
 * The outside of a merchant-authored tile.
 *
 * Three cases, one rule: **a tile is a control only when it has somewhere to go.**
 *
 * - An internal path routes through `Link`, so the preview does not do a full page load.
 * - A permitted absolute URL, a `tel:` or a `mailto:` is a plain `<a>` — the router cannot take
 *   anyone there and would treat it as a path on this origin.
 * - No destination at all renders a `<div>`. Not an `<a>` with an empty `href`: a merchant who
 *   typed a title and left the link blank has authored a picture, and a control that looks
 *   tappable and swallows the tap is the exact failure these sections exist to avoid. It is also
 *   a real accessibility bug — an `<a>` with no `href` is not focusable and announces as nothing.
 *
 * **A destination the href policy refuses takes that same third branch**, and for the same reason
 * rather than a new one: `javascript:` is not somewhere a tile can go, so the tile is not a
 * control. The merchant's title and image still render — the tile does not vanish — it simply
 * stops being tappable, and `classifyHref` names the refused value in the dev console.
 */
export function TileShell({
  href,
  style,
  children,
}: {
  href: string | null;
  style: CSSProperties;
  children: ReactNode;
}) {
  const decision = href === null ? null : classifyHref(href);
  if (decision === null || decision.kind === "refused") return <div style={style}>{children}</div>;
  if (decision.kind === "external") {
    return (
      <a href={decision.href} style={style}>
        {children}
      </a>
    );
  }
  return (
    <Link to={decision.href} style={style}>
      {children}
    </Link>
  );
}
