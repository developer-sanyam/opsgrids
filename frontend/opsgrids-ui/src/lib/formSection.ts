/**
 * Whether a field sits inside a `FormSection`, kept apart from the component so Fast Refresh stays
 * happy — the same split as `toast.ts` / `Toaster.tsx`.
 *
 * It carries the required-marker convention. The owner's 2026-09-25 ruling puts a red asterisk on
 * required fields (the mockups), where the platform used to label only the optional ones. Forms
 * built from `FormSection` follow the new rule: a `TextField`, `TextArea` or `Select` with
 * `required` inside one shows the asterisk. Forms not yet rebuilt keep exactly the labels they
 * have, so no existing screen changes under anyone until it is redesigned on purpose.
 */

import { createContext, useContext } from "react";

export const FormSectionContext = createContext(false);

/** True inside a `FormSection`: a required control there is marked with an asterisk. */
export function useMarksRequired(): boolean {
  return useContext(FormSectionContext);
}
