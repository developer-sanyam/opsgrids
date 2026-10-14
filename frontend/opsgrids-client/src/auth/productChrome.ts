/**
 * What a product app puts on the screen that holds while its session boots. A `.ts` file with no
 * component in it so React Fast Refresh stays happy — the provider that fills it lives in
 * `ProductAuthProvider.tsx`.
 */

import { createContext, useContext } from "react";
import type { ReactNode } from "react";

export interface ProductChrome {
  mark: ReactNode;
  bootMessage: string | undefined;
}

export const ProductChromeContext = createContext<ProductChrome>({ mark: null, bootMessage: undefined });

/** The mark and message `RequireAuth` shows while the session boots, as the app declared them. */
export function useProductChrome(): ProductChrome {
  return useContext(ProductChromeContext);
}
