import { useEffect } from "react";

/**
 * The three pieces of the browser document a live site owns: the tab's title, the tab's icon, and
 * the colour behind the page.
 *
 * Everything here is restored on unmount. This route is a window into a merchant's store from
 * inside their console — leaving the console's tab wearing a dealer's initials after the merchant
 * navigates back to the editor would be a bug, not a feature.
 */
export interface LiveDocument {
  /** The tab's text. */
  title: string;
  /** A `data:` URI for the tab's icon. */
  favicon: string;
  /**
   * The store's own page colour, applied to `<body>`.
   *
   * The store fills the viewport, so this shows only where the browser paints past the document —
   * overscroll rubber-banding on iOS and macOS. Without it that band is the console's grey, which
   * is exactly the kind of chrome leak this route exists to remove.
   */
  background: string;
}

const ICON_SELECTOR = 'link[rel="icon"]';

/** Apply a store's identity to the browser document while this route is mounted. */
export function useLiveDocument(live: LiveDocument | null): void {
  const title = live?.title ?? null;
  const favicon = live?.favicon ?? null;
  const background = live?.background ?? null;

  useEffect(() => {
    if (title === null || favicon === null || background === null) return;

    const previousTitle = document.title;
    document.title = title;

    const icon = document.querySelector<HTMLLinkElement>(ICON_SELECTOR);
    const previousIcon = icon?.getAttribute("href") ?? null;
    icon?.setAttribute("href", favicon);

    const previousBackground = document.body.style.backgroundColor;
    document.body.style.backgroundColor = background;

    return () => {
      document.title = previousTitle;
      if (icon) {
        if (previousIcon === null) icon.removeAttribute("href");
        else icon.setAttribute("href", previousIcon);
      }
      document.body.style.backgroundColor = previousBackground;
    };
  }, [title, favicon, background]);
}
