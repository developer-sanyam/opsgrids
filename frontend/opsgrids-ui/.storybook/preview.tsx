import type { Decorator, Preview } from "@storybook/react-vite";

import { ThemeProvider } from "../src/components/ThemeProvider";
import "./preview.css";

/** The workshop's own preference slot, apart from every app's `opsgrids.<app>.theme`. */
const THEME_KEY = "opsgrids.workshop.theme";

/**
 * Every story renders inside the real `ThemeProvider`, driven by the toolbar's `theme` global (or
 * `&globals=theme:dark` in the iframe URL, which is how the visual harness asks for dark).
 *
 * The provider reads its initial theme from `localStorage` and owns `<html data-theme>` from then
 * on, so the global is written there first and the provider is re-keyed on it: switching the
 * toolbar remounts the story in the new palette instead of fighting the provider's state.
 */
const withTheme: Decorator = (Story, context) => {
  const theme = context.globals["theme"] === "dark" ? "dark" : "light";
  try {
    localStorage.setItem(THEME_KEY, theme);
  } catch {
    // Storage can be unavailable; the provider falls back to light, which the toolbar shows.
  }
  return (
    <ThemeProvider key={theme} storageKey={THEME_KEY}>
      <Story />
    </ThemeProvider>
  );
};

const preview: Preview = {
  globalTypes: {
    theme: {
      description: "Light or dark palette",
      toolbar: {
        title: "Theme",
        icon: "mirror",
        items: [
          { value: "light", title: "Light" },
          { value: "dark", title: "Dark" },
        ],
        dynamicTitle: true,
      },
    },
  },
  initialGlobals: { theme: "light" },
  decorators: [withTheme],
  parameters: {
    layout: "padded",
    // The ground colour is the theme's (`body` in console.css), not a workshop swatch.
    backgrounds: { disable: true },
    controls: { expanded: true },
  },
};

export default preview;
