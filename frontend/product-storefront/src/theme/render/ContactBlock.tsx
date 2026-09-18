import { StoreHeading } from "./primitives";
import { lines, optional } from "./settings";
import { T, step } from "./tokens";
import type { SectionRenderProps } from "./types";

/** Address, phone, email, GSTIN, hours — the facts a buyer copies onto a purchase order. */
export function ContactBlock({ settings }: SectionRenderProps) {
  const heading = optional(settings, "heading");
  const address = lines(settings, "address");
  const phone = optional(settings, "phone");
  const email = optional(settings, "email");
  const gstin = optional(settings, "gstin");
  const hours = optional(settings, "hours");

  const rowStyle = { fontFamily: T.fontBody, fontSize: "0.9375rem", color: T.text, lineHeight: 1.5 } as const;
  const termStyle = {
    fontFamily: T.fontBody,
    fontSize: "0.6875rem",
    letterSpacing: "0.09em",
    textTransform: "uppercase",
    color: T.muted,
    fontWeight: 700,
  } as const;
  const linkStyle = { color: T.accent, textDecoration: "none", fontWeight: 600 } as const;

  return (
    <section
      style={{
        background: T.surface,
        border: `1px solid ${T.border}`,
        borderRadius: T.radius,
        padding: step(1.2),
        display: "flex",
        flexDirection: "column",
        gap: step(0.9),
        height: "100%",
      }}
    >
      {heading ? <StoreHeading size="md">{heading}</StoreHeading> : null}
      <dl style={{ margin: 0, display: "flex", flexDirection: "column", gap: step(0.7) }}>
        {address.length > 0 ? (
          <div>
            <dt style={termStyle}>Address</dt>
            <dd style={{ ...rowStyle, margin: 0 }}>
              {address.map((line) => (
                <span key={line} style={{ display: "block" }}>
                  {line}
                </span>
              ))}
            </dd>
          </div>
        ) : null}
        {phone ? (
          <div>
            <dt style={termStyle}>Phone</dt>
            <dd style={{ ...rowStyle, margin: 0 }}>
              <a href={`tel:${phone.replace(/\s/g, "")}`} style={linkStyle}>
                {phone}
              </a>
            </dd>
          </div>
        ) : null}
        {email ? (
          <div>
            <dt style={termStyle}>Email</dt>
            <dd style={{ ...rowStyle, margin: 0 }}>
              <a href={`mailto:${email}`} style={linkStyle}>
                {email}
              </a>
            </dd>
          </div>
        ) : null}
        {gstin ? (
          <div>
            <dt style={termStyle}>GSTIN</dt>
            <dd style={{ ...rowStyle, margin: 0, fontVariantNumeric: "tabular-nums" }}>{gstin}</dd>
          </div>
        ) : null}
        {hours ? (
          <div>
            <dt style={termStyle}>Hours</dt>
            <dd style={{ ...rowStyle, margin: 0 }}>{hours}</dd>
          </div>
        ) : null}
      </dl>
    </section>
  );
}
