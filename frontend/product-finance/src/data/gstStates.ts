/**
 * The GST state-code vocabulary — the first two digits of every GSTIN, and the state a supply's
 * place-of-supply names. Statutory, not tenant data, so it lives beside the domain rather than
 * behind an endpoint (mirrors the withholding-rate schedule: global vocabulary with no
 * `organization_id`).
 */

export const GST_STATE_NAME: Readonly<Record<string, string>> = {
  "01": "Jammu & Kashmir",
  "02": "Himachal Pradesh",
  "03": "Punjab",
  "04": "Chandigarh",
  "05": "Uttarakhand",
  "06": "Haryana",
  "07": "Delhi",
  "08": "Rajasthan",
  "09": "Uttar Pradesh",
  "10": "Bihar",
  "11": "Sikkim",
  "12": "Arunachal Pradesh",
  "13": "Nagaland",
  "14": "Manipur",
  "15": "Mizoram",
  "16": "Tripura",
  "17": "Meghalaya",
  "18": "Assam",
  "19": "West Bengal",
  "20": "Jharkhand",
  "21": "Odisha",
  "22": "Chhattisgarh",
  "23": "Madhya Pradesh",
  "24": "Gujarat",
  "25": "Daman & Diu",
  "26": "Dadra & Nagar Haveli",
  "27": "Maharashtra",
  "28": "Andhra Pradesh (old)",
  "29": "Karnataka",
  "30": "Goa",
  "31": "Lakshadweep",
  "32": "Kerala",
  "33": "Tamil Nadu",
  "34": "Puducherry",
  "35": "Andaman & Nicobar Islands",
  "36": "Telangana",
  "37": "Andhra Pradesh",
  "38": "Ladakh",
};

/** `Select` options, code-ordered: `"27 — Maharashtra"`. */
export const GST_STATE_OPTIONS: readonly { value: string; label: string }[] = Object.entries(
  GST_STATE_NAME,
).map(([code, name]) => ({ value: code, label: `${code} — ${name}` }));

/** The plain state names, for a free-text `state` field's own picker (customer directory). */
export const INDIAN_STATE_OPTIONS: readonly { value: string; label: string }[] = [
  ...new Set(Object.values(GST_STATE_NAME)),
]
  .filter((name) => !name.endsWith("(old)"))
  .sort((a, b) => a.localeCompare(b))
  .map((name) => ({ value: name, label: name }));

export function gstStateName(code: string): string {
  return GST_STATE_NAME[code] ?? code;
}
