import { createContext, useContext } from "react";

/**
 * Light/dark theme, driven by a `data-theme` attribute on <html>. The choice is persisted per
 * browser in `localStorage` (a display preference, never anything sensitive — the access token
 * lives in memory only). An inline script in index.html applies the stored value before first
 * paint so there is no flash; the provider keeps React in sync and flips it on toggle.
 *
 * The context and its hook live here, apart from the provider component, so React Fast Refresh
 * stays happy.
 */
export type Theme = "light" | "dark";

export interface ThemeControls {
  theme: Theme;
  toggle: () => void;
}

export const ThemeContext = createContext<ThemeControls | null>(null);

/** The current theme and a toggle. Requires a `<ThemeProvider>` above it. */
export function useTheme(): ThemeControls {
  const value = useContext(ThemeContext);
  if (!value) throw new Error("useTheme must be used inside a <ThemeProvider>");
  return value;
}
