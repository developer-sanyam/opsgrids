import { Suspense } from "react";
import type { ReactNode } from "react";

import { RouteFallback } from "../components/RouteScreens";

/**
 * A route whose screen is a lazy component: the chunk downloads on first visit and the
 * shell-shaped `RouteFallback` holds the space meanwhile.
 *
 *     lazyRoute("employees", <EmployeesPage />)
 */
export function lazyRoute(path: string, element: ReactNode): { path: string; element: ReactNode } {
  return { path, element: <Suspense fallback={<RouteFallback />}>{element}</Suspense> };
}
