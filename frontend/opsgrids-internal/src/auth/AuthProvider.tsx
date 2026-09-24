import type { ReactNode } from "react";

import { USE_FIXTURES } from "@/config";
import { OperatorAuthProvider } from "./OperatorAuthProvider";
import type { OperatorMe } from "./types";

/**
 * A stand-in operator for fixtures mode (`VITE_USE_FIXTURES` defaults on in a dev build), so the
 * whole console renders with no admin-gateway and no live staff registry. Holding every operator
 * role, it shows the whole console offline. Never reached against a live gateway: `USE_FIXTURES`
 * is compiled out of a production build.
 */
const FIXTURE_OPERATOR: OperatorMe = {
  staff_id: "8f21a0c4-5b1e-4d3a-9c77-2e6a4b0f1d10",
  email: "priya.venkatesan@opsgrids.com",
  display_name: "Priya Venkatesan",
  operator_roles: ["owner", "billing_ops", "support"],
  amr: ["pwd", "otp"],
};

/** The operator session, wired to this app's fixture identity. The logic is `OperatorAuthProvider`'s. */
export function AuthProvider({ children }: { children: ReactNode }) {
  return (
    <OperatorAuthProvider fixtureOperator={USE_FIXTURES ? FIXTURE_OPERATOR : null}>
      {children}
    </OperatorAuthProvider>
  );
}
