import { useCallback, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import { ThemeContext } from "../lib/theme";
import type { Theme } from "../lib/theme";

interface ThemeProviderProps {
  /**
   * The `localStorage` key this app persists its preference under — e.g. `atmora.crm.theme`.
   * One key per app, so two Atmora apps open in one browser keep separate preferences.
   */
  storageKey: string;
  children: ReactNode;
}

/** Owns the light/dark choice, mirrors it to `<html data-theme>` and remembers it per browser. */
export function ThemeProvider({ storageKey, children }: ThemeProviderProps) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      const value = localStorage.getItem(storageKey);
      if (value === "dark" || value === "light") return value;
    } catch {
      // localStorage can throw in private modes — fall back to light.
    }
    return "light";
  });

  useEffect(() => {
    document.documentElement.setAttribute("data-theme", theme);
    try {
      localStorage.setItem(storageKey, theme);
    } catch {
      // Persistence is best-effort; the attribute still drives the current view.
    }
  }, [theme, storageKey]);

  const toggle = useCallback(() => {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }, []);

  const value = useMemo(() => ({ theme, toggle }), [theme, toggle]);

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}
