import { useCallback, useEffect, useState } from "react";

/**
 * Light/dark theme, driven by a `data-theme` attribute on <html>. The choice is persisted per
 * browser in `localStorage` (a display preference, never anything sensitive — the access token
 * lives in memory only). An inline script in index.html applies the stored value before first
 * paint so there is no flash; this hook keeps React in sync and flips it on toggle.
 */
export type Theme = "light" | "dark";

const STORAGE_KEY = "atmora.finance.theme";

function readStored(): Theme {
  try {
    const value = localStorage.getItem(STORAGE_KEY);
    if (value === "dark" || value === "light") return value;
  } catch {
    // localStorage can throw in private modes — fall back to light.
  }
  return "light";
}

function apply(theme: Theme): void {
  document.documentElement.setAttribute("data-theme", theme);
  try {
    localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // Persistence is best-effort; the attribute still drives the current view.
  }
}

export function useTheme(): { theme: Theme; toggle: () => void } {
  const [theme, setTheme] = useState<Theme>(readStored);

  useEffect(() => {
    apply(theme);
  }, [theme]);

  const toggle = useCallback(() => {
    setTheme((prev) => (prev === "dark" ? "light" : "dark"));
  }, []);

  return { theme, toggle };
}
