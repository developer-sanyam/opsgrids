/**
 * Small curated reference lists for the admin forms' selects. Not the design system's concern —
 * this is data, not a component — and kept short deliberately: a country or timezone picker with
 * every value in the standard is a wall a customer never needed, and `Intl.supportedValuesOf`
 * cannot be relied on to exist in every runtime this app is tested under.
 */

export const COUNTRIES: { value: string; label: string }[] = [
  { value: "IN", label: "India" },
  { value: "US", label: "United States" },
  { value: "GB", label: "United Kingdom" },
  { value: "AE", label: "United Arab Emirates" },
  { value: "SG", label: "Singapore" },
  { value: "AU", label: "Australia" },
  { value: "CA", label: "Canada" },
  { value: "DE", label: "Germany" },
  { value: "FR", label: "France" },
  { value: "NL", label: "Netherlands" },
  { value: "JP", label: "Japan" },
  { value: "ZA", label: "South Africa" },
  { value: "BR", label: "Brazil" },
  { value: "MX", label: "Mexico" },
  { value: "NZ", label: "New Zealand" },
];

export const TIMEZONES: { value: string; label: string }[] = [
  { value: "Asia/Kolkata", label: "Asia/Kolkata (IST)" },
  { value: "Asia/Dubai", label: "Asia/Dubai (GST)" },
  { value: "Asia/Singapore", label: "Asia/Singapore (SGT)" },
  { value: "Asia/Tokyo", label: "Asia/Tokyo (JST)" },
  { value: "Europe/London", label: "Europe/London (GMT/BST)" },
  { value: "Europe/Berlin", label: "Europe/Berlin (CET)" },
  { value: "Europe/Paris", label: "Europe/Paris (CET)" },
  { value: "America/New_York", label: "America/New_York (ET)" },
  { value: "America/Chicago", label: "America/Chicago (CT)" },
  { value: "America/Denver", label: "America/Denver (MT)" },
  { value: "America/Los_Angeles", label: "America/Los_Angeles (PT)" },
  { value: "America/Sao_Paulo", label: "America/Sao_Paulo (BRT)" },
  { value: "Australia/Sydney", label: "Australia/Sydney (AEST)" },
  { value: "Pacific/Auckland", label: "Pacific/Auckland (NZST)" },
  { value: "UTC", label: "UTC" },
];

export const LOCALES: { value: string; label: string }[] = [
  { value: "en-IN", label: "English (India)" },
  { value: "en-US", label: "English (United States)" },
  { value: "en-GB", label: "English (United Kingdom)" },
  { value: "en-AU", label: "English (Australia)" },
  { value: "fr-FR", label: "Français (France)" },
  { value: "de-DE", label: "Deutsch (Germany)" },
  { value: "pt-BR", label: "Português (Brazil)" },
  { value: "ja-JP", label: "日本語 (Japan)" },
];

export const MONTHS: { value: string; label: string }[] = [
  { value: "1", label: "January" },
  { value: "2", label: "February" },
  { value: "3", label: "March" },
  { value: "4", label: "April" },
  { value: "5", label: "May" },
  { value: "6", label: "June" },
  { value: "7", label: "July" },
  { value: "8", label: "August" },
  { value: "9", label: "September" },
  { value: "10", label: "October" },
  { value: "11", label: "November" },
  { value: "12", label: "December" },
];
