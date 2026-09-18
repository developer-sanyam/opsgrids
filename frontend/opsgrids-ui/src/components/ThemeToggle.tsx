import { useTheme } from "../lib/theme";
import { MoonIcon, SunIcon } from "../icons";

/** Flip between the light and dark palettes. State lives in `<ThemeProvider>` (persisted per browser). */
export function ThemeToggle({ className }: { className?: string }) {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";
  return (
    <button
      type="button"
      onClick={toggle}
      aria-pressed={isDark}
      aria-label={isDark ? "Switch to light theme" : "Switch to dark theme"}
      title={isDark ? "Switch to light theme" : "Switch to dark theme"}
      className={
        "grid size-9 place-items-center rounded-full text-ink-soft hover:bg-ground transition-colors " +
        (className ?? "")
      }
    >
      {isDark ? <SunIcon className="size-5" /> : <MoonIcon className="size-5" />}
    </button>
  );
}
