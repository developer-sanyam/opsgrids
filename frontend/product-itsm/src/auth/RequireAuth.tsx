import type { ReactNode } from "react";

import { RequireAuth as BaseRequireAuth } from "@atmora/client";

import { LogoMark } from "@/ui/LogoMark";

/** The authenticated gate, wearing this product's mark while the session boots. */
export function RequireAuth({ children }: { children: ReactNode }) {
  return (
    <BaseRequireAuth
      logo={<LogoMark className="size-11 animate-pulse" />}
      message="Opening the service desk…"
    >
      {children}
    </BaseRequireAuth>
  );
}
