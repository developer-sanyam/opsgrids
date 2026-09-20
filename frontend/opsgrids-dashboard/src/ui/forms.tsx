/**
 * The pieces the four unauthenticated screens share — one card centred on the ground, a labelled
 * field, one inline error — so sign-up, sign-in, forgot and reset read as the same door. Classes
 * are the Console tokens `@opsgrids/ui` defines; nothing here invents a colour.
 */
import type { ReactNode } from "react";

import { LogoMark } from "./LogoMark";

export const inputClass =
  "h-12 rounded-tile bg-ground px-4 text-sm text-ink placeholder:text-faint focus:outline-none focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink";

export function AuthCard({
  title,
  intro,
  children,
}: {
  title: string;
  intro?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="min-h-dvh bg-ground">
      <main className="grid min-h-dvh place-items-center px-4 py-10">
        <div className="rise w-full max-w-[26rem]">
          <div className="mb-6 flex items-center justify-center gap-2.5">
            <LogoMark className="size-9" />
            <span className="text-[1.15rem] font-extrabold tracking-tight">OpsGrids</span>
          </div>
          <div className="rounded-card bg-surface p-7 sm:p-8" style={{ boxShadow: "var(--shadow-float)" }}>
            <header className="mb-6 flex flex-col gap-1.5">
              <h1 className="display text-2xl font-extrabold">{title}</h1>
              {intro ? <p className="text-sm text-muted">{intro}</p> : null}
            </header>
            {children}
          </div>
        </div>
      </main>
    </div>
  );
}

export function Field({
  id,
  label,
  hint,
  children,
}: {
  id: string;
  label: string;
  hint?: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-semibold text-ink-soft">
        {label}
      </label>
      {children}
      {hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export function InlineError({ children }: { children: ReactNode }) {
  return (
    <p role="alert" className="rounded-tile bg-danger-soft px-4 py-3 text-sm font-medium text-danger">
      {children}
    </p>
  );
}
