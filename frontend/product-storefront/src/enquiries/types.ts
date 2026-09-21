/**
 * The merchant's enquiry inbox — `GET/PATCH /storefront/enquiries`, transcribed from
 * `services/storefront/app/schemas.py` `EnquiryOut` / `EnquiryPatch`.
 *
 * **Contact fields are masked by default** (`email_masked`, `phone_masked`) — never prefilled
 * into an editable input, and there is no unmask control here: `POST
 * .../enquiries/{id}:reveal` needs `storefront.sales.enquiry.unmask` *and* an OTP step-up this
 * SDK has no support for, and building a control that could never complete would be a dead one.
 */

export type EnquiryStatus = "new" | "contacted" | "won" | "lost";

export interface EnquiryLine {
  product_id: string;
  name: string;
  quantity: number;
}

export interface EnquiryOut {
  id: string;
  name: string;
  company: string;
  email_masked: string;
  phone_masked: string;
  gstin: string | null;
  requirement: string;
  quantity: string;
  lines: EnquiryLine[];
  status: EnquiryStatus;
  created_at: string;
  updated_at: string;
}

export interface PagedResult<T> {
  items: T[];
  total: number;
  page: number;
  pages: number;
  size: number;
}

export interface ListEnquiriesParams {
  status?: EnquiryStatus;
  q?: string;
  since?: string;
  page?: number;
  size?: number;
}

/** `new -> contacted -> won | lost`, and nothing else. Enforced again on the server — a 409
 *  `enquiry_transition_invalid` for anything this does not allow. */
export const ENQUIRY_TRANSITIONS: Record<EnquiryStatus, EnquiryStatus[]> = {
  new: ["contacted"],
  contacted: ["won", "lost"],
  won: [],
  lost: [],
};
