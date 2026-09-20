import { resolveImageSrc } from "./assetSrc";
import { SectionEmpty, StoreHeading, StoreText } from "./primitives";
import { clampNum, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

const SLOTS = [1, 2, 3, 4, 5, 6] as const;

/**
 * Certifications, standards and awards: the Certifications page, and the proof strip a Quality &
 * Manufacturing or Why Choose Us page ends on.
 *
 * **Why this is not a flag on `logo-wall`.** `logo-wall` is one textarea of *customer* names set as
 * wordmarks, and the reason it refuses uploaded artwork is a licensing one: a merchant almost never
 * has permission to display their customer's mark. A certification is the opposite case — it is the
 * merchant's own, they hold the certificate, and the mark is theirs to show. It also carries three
 * facts rather than one: the standard, what it certifies, and who issued it and until when. A
 * toggle on `logo-wall` could not express any of that without turning one textarea into four
 * numbered ones, which is a different section wearing the old section's name.
 *
 * The mark falls back to a wordmark of the short code — "ISO 9001" set in the heading face inside a
 * bordered square — so a merchant with no artwork still gets a strip that reads as credentials
 * rather than a list of empty boxes.
 */
export function CredentialStrip({ settings, stacked, width }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const columns = clampNum(settings, "columns", 3, 2, 4);
  const mobileColumns = clampNum(settings, "mobileColumns", 1, 1, 2);

  const items = SLOTS.map((slot) => ({
    slot,
    // Resolved here rather than at the tag, so the truthiness check below narrows the *resolved*
    // address and an `/a/…` upload reaches this `<img>` the same way an absolute URL does.
    image: resolveImageSrc(optional(settings, `item${slot}Image`)),
    mark: optional(settings, `item${slot}Mark`),
    label: optional(settings, `item${slot}Label`),
    issuer: optional(settings, `item${slot}Issuer`),
  })).filter((item) => item.mark !== null || item.label !== null);

  const cols = stacked ? mobileColumns : columns;

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: step(1) }}>
      {heading ? <StoreHeading width={width}>{heading}</StoreHeading> : null}
      {items.length === 0 ? (
        <SectionEmpty
          title="No certifications yet"
          hint="Add a standard's short code and what it certifies in this section's settings."
        />
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "grid",
            gridTemplateColumns: `repeat(${Math.min(cols, items.length)}, minmax(0, 1fr))`,
            gap: step(0.8),
          }}
        >
          {items.map((item) => (
            <li
              key={item.slot}
              style={{
                display: "flex",
                gap: step(0.8),
                alignItems: "flex-start",
                background: T.surface,
                border: `1px solid ${T.border}`,
                borderRadius: T.radius,
                padding: step(0.9),
              }}
            >
              <span
                style={{
                  flex: "0 0 auto",
                  display: "inline-flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: step(3),
                  height: step(3),
                  border: `1px solid ${T.border}`,
                  borderRadius: `calc(${T.radius} / 1.5)`,
                  overflow: "hidden",
                  background: T.bg,
                }}
              >
                {item.image ? (
                  /* The merchant's own certification mark. `alt=""` because the standard's name is
                     already the first line of text beside it — an alt would say it twice. */
                  <img
                    src={item.image}
                    alt=""
                    style={{ width: "100%", height: "100%", objectFit: "contain", display: "block" }}
                  />
                ) : (
                  <span
                    aria-hidden
                    style={{
                      fontFamily: T.fontHeading,
                      fontSize: "0.6875rem",
                      fontWeight: 800,
                      lineHeight: 1.1,
                      letterSpacing: "-0.01em",
                      textAlign: "center",
                      color: T.muted,
                      padding: step(0.2),
                      overflowWrap: "anywhere",
                    }}
                  >
                    {item.mark ?? ""}
                  </span>
                )}
              </span>
              {/* A `<div>`: the issuer line is a paragraph, and phrasing content cannot hold one. */}
              <div style={{ display: "flex", flexDirection: "column", gap: step(0.2), minWidth: 0 }}>
                {item.mark ? (
                  <StoreHeading level={3} size="sm" style={{ overflowWrap: "anywhere" }}>
                    {item.mark}
                  </StoreHeading>
                ) : null}
                {item.label ? (
                  <span
                    style={{
                      fontFamily: T.fontBody,
                      fontSize: "0.875rem",
                      fontWeight: 600,
                      color: T.text,
                      lineHeight: 1.4,
                    }}
                  >
                    {item.label}
                  </span>
                ) : null}
                {item.issuer ? (
                  <StoreText muted small style={{ maxWidth: "none" }}>
                    {item.issuer}
                  </StoreText>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
