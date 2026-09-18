import { ActionLink, StoreHeading, StoreText } from "./primitives";
import { bool, choice, optional, str } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const TONES = ["surface", "accent", "contrast"] as const;

/** The closing ask. One action, said once. */
export function FooterCta({ settings, stacked }: SectionRenderProps) {
  const tone = choice(settings, "tone", TONES, "accent");
  const heading = optional(settings, "heading");
  const body = optional(settings, "body");
  const ctaLabel = optional(settings, "ctaLabel");
  const showBrand = bool(settings, "showBrand", true);

  const background = tone === "accent" ? T.accent : tone === "contrast" ? T.text : T.surface;
  const foreground = tone === "accent" ? T.accentText : tone === "contrast" ? T.bg : T.text;

  return (
    <section
      style={{
        background,
        color: foreground,
        border: tone === "surface" ? `1px solid ${T.border}` : "none",
        borderRadius: T.radius,
        padding: step(stacked ? 1.4 : 2),
        display: "flex",
        flexDirection: stacked ? "column" : "row",
        alignItems: stacked ? "flex-start" : "center",
        justifyContent: "space-between",
        gap: step(1.2),
        height: "100%",
      }}
    >
      <div style={{ display: "flex", flexDirection: "column", gap: step(0.4) }}>
        {heading ? <StoreHeading style={{ color: foreground }}>{heading}</StoreHeading> : null}
        {body ? <StoreText style={{ color: foreground, opacity: 0.88 }}>{body}</StoreText> : null}
        {showBrand ? (
          <StoreText small style={{ color: foreground, opacity: 0.7 }}>
            Trade enquiries welcome · GST invoice on every order
          </StoreText>
        ) : null}
      </div>
      {ctaLabel ? (
        <div style={{ flexShrink: 0 }}>
          {/* On an accent or contrast ground the solid button would vanish into it, so the
              closing action inverts: the page's own background becomes the button. */}
          <span style={{ display: "inline-block", background: tone === "surface" ? "transparent" : foreground, borderRadius: T.radius }}>
            <ActionLink href={str(settings, "ctaHref", "/store/catalogue")} variant={tone === "surface" ? "solid" : "outline"}>
              <span style={{ color: tone === "surface" ? T.accentText : background }}>{ctaLabel}</span>
            </ActionLink>
          </span>
        </div>
      ) : null}
    </section>
  );
}
