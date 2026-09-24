import type { ReactNode, Ref } from "react";

import { AuthCard } from "@opsgrids/ui";

import { LogoMark } from "@/ui/LogoMark";

/**
 * The card every step of the operator sign-in lives in: the mark, the wordmark and an "Internal"
 * badge so staff never confuse it with a customer login, one `h1` per step, and the reminder that
 * access is audited.
 */
export function SignInCard({
  title,
  intro,
  titleRef,
  children,
}: {
  title: string;
  intro?: ReactNode | undefined;
  titleRef?: Ref<HTMLHeadingElement> | undefined;
  children: ReactNode;
}) {
  return (
    <AuthCard
      brand={
        <>
          <LogoMark className="size-9" />
          <span className="text-xl font-extrabold tracking-tight">OpsGrids</span>
          <span className="money rounded-md bg-accent-soft px-2 py-0.5 text-xs font-semibold uppercase tracking-widest text-accent-ink">
            Internal
          </span>
        </>
      }
      title={title}
      intro={intro}
      titleRef={titleRef}
      footer="OpsGrids staff console · access is logged and audited"
    >
      {children}
    </AuthCard>
  );
}
