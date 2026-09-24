import type { ReactNode, Ref } from "react";

import { Card } from "./Card";

interface AuthCardProps {
  /** The mark and wordmark above the card — the product's own, so the door says which one it is. */
  brand: ReactNode;
  /** The screen's `h1`. Each step of a multi-step sign-in passes its own. */
  title: string;
  /** One sentence under the title, or a link to the neighbouring screen. */
  intro?: ReactNode | undefined;
  /** Small print below the card — "Access is logged and audited". */
  footer?: ReactNode | undefined;
  /**
   * The heading itself, for moving focus to it when a step is replaced by another and nothing on
   * the new one deserves focus more than its title (recovery codes, a lockout). Makes the heading
   * focusable by script only; it stays out of the tab order.
   */
  titleRef?: Ref<HTMLHeadingElement> | undefined;
  children: ReactNode;
}

/**
 * The card an unauthenticated screen lives in: one column centred on the ground, the brand above
 * it, one `h1`, and the form. Sign-in, its second-factor step and enrolment share it so they read
 * as one door. It carries the page's `<main>`, because a screen with no signed-in shell around it
 * has no other landmark.
 */
export function AuthCard({ brand, title, intro, footer, titleRef, children }: AuthCardProps) {
  return (
    <div className="min-h-dvh bg-ground">
      <main className="grid min-h-dvh place-items-center px-4 py-10">
        <div className="rise w-full max-w-md">
          <div className="mb-6 flex items-center justify-center gap-2.5">{brand}</div>
          <Card className="p-6 shadow-float sm:p-8">
            <header className="mb-6 flex flex-col gap-2">
              <h1
                ref={titleRef}
                tabIndex={-1}
                className="display text-2xl font-extrabold focus:outline-none"
              >
                {title}
              </h1>
              {intro ? <p className="text-sm text-muted">{intro}</p> : null}
            </header>
            {children}
          </Card>
          {footer ? <p className="mt-6 text-center text-xs text-faint">{footer}</p> : null}
        </div>
      </main>
    </div>
  );
}
