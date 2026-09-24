import type { StorybookConfig } from "@storybook/react-vite";

/**
 * The design system's workshop: every exported primitive, in every state worth seeing, rendered
 * from props alone. `frontend/VISUAL-TESTING.md` says how it is used; the short version is that
 * `opsgrids-visual` screenshots and axe-checks every story in both themes at 375/768/1280, so a
 * story here is also a regression test.
 *
 * Stories sit beside their component (`Button.tsx` → `Button.stories.tsx`) and
 * `src/stories.test.ts` fails if an exported component has none.
 */
const config: StorybookConfig = {
  framework: "@storybook/react-vite",
  stories: ["../src/**/*.stories.tsx"],
  core: {
    // Offline and deterministic: no usage beacons, no release-notes fetch.
    disableTelemetry: true,
    disableWhatsNewNotifications: true,
  },
  // No autodocs pages, so no prop-table extraction to pay for on every build.
  typescript: { reactDocgen: false },
  async viteFinal(viteConfig) {
    const { mergeConfig } = await import("vite");
    const { default: react } = await import("@vitejs/plugin-react");
    const { default: tailwindcss } = await import("@tailwindcss/vite");
    // The same two plugins every app's vite.config.ts uses, so a component renders here exactly
    // as it does in the apps. The chunk-size warning is about what ships to users; the workshop
    // ships to nobody.
    return mergeConfig(viteConfig, {
      plugins: [react(), tailwindcss()],
      build: { chunkSizeWarningLimit: 4096 },
    });
  },
};

export default config;
