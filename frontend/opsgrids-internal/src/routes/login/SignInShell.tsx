import type { ReactNode, Ref } from "react";

import { LogoMark } from "@/ui/icons";

interface SignInShellProps {
  title: string;
  intro?: ReactNode | undefined;
  /** Where focus lands on mount for a step with no field to focus instead — `RecoveryStep`, whose
   *  heading is the first thing worth announcing. */
  titleRef?: Ref<HTMLHeadingElement> | undefined;
  children: ReactNode;
}

/**
 * The card every step of the operator sign-in lives in: the mark, the wordmark and an "Internal"
 * badge so staff never confuse it with a customer login, a soft accent glow over the console's
 * engineering grid, one `h1` per step, and the reminder that access is audited.
 */
export function SignInShell({ title, intro, titleRef, children }: SignInShellProps) {
  return (
    <div className="relative min-h-dvh overflow-hidden bg-ground">
      {/* Atmosphere: an accent glow over the console's engineering grid. */}
      <div aria-hidden className="pointer-events-none absolute inset-0">
        <div
          className="grid-field absolute inset-0 opacity-[0.5]"
          style={{
            maskImage: "radial-gradient(circle at 50% 34%, black, transparent 70%)",
            WebkitMaskImage: "radial-gradient(circle at 50% 34%, black, transparent 70%)",
          }}
        />
        <div
          className="absolute left-1/2 top-[-14rem] size-[40rem] -translate-x-1/2 rounded-full"
          style={{ background: "radial-gradient(closest-side, rgba(108,76,241,0.22), transparent)" }}
        />
        <div
          className="absolute bottom-[-12rem] right-[-8rem] size-[30rem] rounded-full"
          style={{ background: "radial-gradient(closest-side, rgba(108,76,241,0.12), transparent)" }}
        />
      </div>

      <main className="relative grid min-h-dvh place-items-center px-4 py-10">
        <div className="rise w-full max-w-[26rem]">
          <div className="mb-6 flex items-center justify-center gap-2.5">
            <LogoMark className="size-9" />
            <span className="text-[1.15rem] font-extrabold tracking-tight">OpsGrids</span>
            <span className="mono rounded-md bg-accent-soft px-2 py-0.5 text-caption font-semibold uppercase tracking-widest text-accent-ink">
              Internal
            </span>
          </div>

          <div className="rounded-card bg-surface p-7 sm:p-8" style={{ boxShadow: "var(--shadow-float)" }}>
            <header className="mb-6 flex flex-col gap-1.5">
              <h1 ref={titleRef} tabIndex={-1} className="display text-2xl font-extrabold focus:outline-none">
                {title}
              </h1>
              {intro ? <p className="text-sm text-muted">{intro}</p> : null}
            </header>

            {children}
          </div>

          <p className="mt-6 text-center text-xs text-faint">
            OpsGrids staff console · access is logged and audited
          </p>
        </div>
      </main>
    </div>
  );
}
