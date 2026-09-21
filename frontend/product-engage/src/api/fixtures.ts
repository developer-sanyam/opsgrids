/**
 * Sample sending identity, for rendering the app with no gateway behind it.
 *
 * Every value here is shaped like one the service would return: the DKIM selector is `og` plus
 * twelve hex characters as `services.dkim_selector` derives it, the record set is the same five
 * records in the same order as `domain.dns_records_for`, and each domain's `status` is what
 * `domain.fold_domain_status` would fold its records to.
 *
 * **Nothing here stands in for a credential.** There is no `secret`, no mask and no last-four,
 * because a fixture is where a leak starts: a fabricated `"••••1234"` is a field a screen learns
 * to render, and then renders against the real connection the day the flag flips.
 */

import type {
  ConnectionOut,
  ContactListOut,
  ContactOut,
  DnsRecordOut,
  DomainOut,
  ProviderTemplateOut,
  SenderOut,
  SuppressionOut,
  WhatsAppConnectionOut,
} from "./types";

/** A Workspace connection — the common case, and the one whose 2,000/day cap a campaign hits. */
export const CONNECTION: ConnectionOut = {
  kind: "google",
  username: "notifications@vaayu-logistics.example.com",
  host: "smtp.gmail.com",
  port: 587,
  status: "connected",
  daily_cap: 2_000,
};

/**
 * The record set for a domain whose customer has published everything required.
 *
 * DMARC is unverified on purpose: it is the one record `required: false`, so the domain folds to
 * `verified` and sends while the security team is still arguing about `p=`. A fixture where all
 * five are green would hide the only case in this vocabulary where "verified" and "everything is
 * published" are not the same sentence.
 */
const VERIFIED_RECORDS: DnsRecordOut[] = [
  {
    kind: "spf",
    type: "TXT",
    host: "vaayu-logistics.example.com",
    value: "v=spf1 include:_spf.opsgrids.com ~all",
    required: true,
    verified: true,
  },
  {
    kind: "dkim",
    type: "TXT",
    host: "og4f1c9ad83b27._domainkey.vaayu-logistics.example.com",
    value:
      "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAu1xK0Yb7VqJ8mQ3pN2fR" +
      "wZ6tD9sXcL4hVgB1yPoE5aTnUmK7jH0dSfCvA3rXqW8ZbN6MlGyIuOpQ2eRtF4xDkJ9vS1cHnA7mB3gYwP",  // pragma: allowlist secret
    required: true,
    verified: true,
  },
  {
    kind: "dmarc",
    type: "TXT",
    host: "_dmarc.vaayu-logistics.example.com",
    value: "v=DMARC1; p=none; rua=mailto:dmarc@opsgrids.com",
    required: false,
    verified: false,
  },
  {
    kind: "return_path",
    type: "CNAME",
    host: "bounce.vaayu-logistics.example.com",
    value: "bounce.opsgrids.com",
    required: true,
    verified: true,
  },
  {
    kind: "tracking",
    type: "CNAME",
    host: "track.vaayu-logistics.example.com",
    value: "track.opsgrids.com",
    required: true,
    verified: true,
  },
];

/** A domain mid-setup: SPF is up, the other three required records are not, so it cannot send. */
const PENDING_RECORDS: DnsRecordOut[] = [
  {
    kind: "spf",
    type: "TXT",
    host: "vaayu-freight.example.com",
    value: "v=spf1 include:_spf.opsgrids.com ~all",
    required: true,
    verified: true,
  },
  {
    kind: "dkim",
    type: "TXT",
    host: "og8b30e57c14da._domainkey.vaayu-freight.example.com",
    value:
      "v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEAq7ZmT3wXyR0nE8cVdH2p" +
      "LkAoS5jB9fUgQ6tYzNxCrM1vJhW4KePbD7saG0iOl3RuXn2FmZqYtA8eCcV5BdLwI6pKjS9gHoN4TfE1xU",  // pragma: allowlist secret
    required: true,
    verified: false,
  },
  {
    kind: "dmarc",
    type: "TXT",
    host: "_dmarc.vaayu-freight.example.com",
    value: "v=DMARC1; p=none; rua=mailto:dmarc@opsgrids.com",
    required: false,
    verified: false,
  },
  {
    kind: "return_path",
    type: "CNAME",
    host: "bounce.vaayu-freight.example.com",
    value: "bounce.opsgrids.com",
    required: true,
    verified: false,
  },
  {
    kind: "tracking",
    type: "CNAME",
    host: "track.vaayu-freight.example.com",
    value: "track.opsgrids.com",
    required: true,
    verified: false,
  },
];

const VERIFIED_DOMAIN: DomainOut = {
  id: "3f6a1c20-7d84-4e1b-9c53-0a2e8b74d611",
  domain: "vaayu-logistics.example.com",
  status: "verified",
  records: VERIFIED_RECORDS,
  last_checked_at: "2026-09-19T06:12:44Z",
  created_at: "2026-08-28T11:04:09Z",
};

const PENDING_DOMAIN: DomainOut = {
  id: "b90d5e47-2c31-4a88-8f70-61d4c9a5e223",
  domain: "vaayu-freight.example.com",
  status: "pending",
  records: PENDING_RECORDS,
  // Checked, and the answer was "not yet" — which is a different fact from never checked, and
  // the reason the field is nullable rather than defaulted to the creation time.
  last_checked_at: "2026-09-20T15:41:02Z",
  created_at: "2026-09-20T15:38:57Z",
};

export const DOMAINS: DomainOut[] = [VERIFIED_DOMAIN, PENDING_DOMAIN];

/**
 * Two senders on the verified domain, because the only distinction that matters here is whether a
 * test message has actually arrived. `usable` is the service's derivation of `verified_at`, so the
 * two agree in both rows — a fixture where they disagreed would be a shape the service cannot
 * produce, and a screen written against it would be written against nothing.
 */
export const SENDERS: SenderOut[] = [
  {
    id: "5c8e2a19-4b6f-4d02-a37e-9f15c0b8d744",
    domain_id: VERIFIED_DOMAIN.id,
    name: "Vaayu Logistics Support",
    address: "support@vaayu-logistics.example.com",
    reply_to: "support@vaayu-logistics.example.com",
    signature: "Vaayu Logistics · Support desk · 24×7",
    department: "Support",
    usable: true,
    verified_at: "2026-08-29T09:22:31Z",
    created_at: "2026-08-29T09:18:02Z",
  },
  {
    id: "1a47f0d6-88b3-4c95-b214-6e7d3f92ac58",
    domain_id: VERIFIED_DOMAIN.id,
    name: "Priya Raghavan",
    address: "priya.raghavan@vaayu-logistics.example.com",
    reply_to: "",
    signature: "",
    department: "Key accounts",
    // Added but never tested: the domain is proven, this identity is not, and a campaign may not
    // pick it until a test message has reached the provider.
    usable: false,
    verified_at: null,
    created_at: "2026-09-18T13:47:20Z",
  },
];

/** A connected WhatsApp Business account. **No `access_token` field exists on this shape** —
 *  same rule as `CONNECTION`'s: a fixture is where a leak starts, and there is nothing here to
 *  render even by accident. */
export const WHATSAPP_CONNECTION: WhatsAppConnectionOut = {
  waba_id: "102938475610234",
  phone_number_id: "109827364501928",
  display_number: "+91 98765 43210",
  status: "connected",
};

/**
 * Templates in every status the vocabulary carries, so a fixture-mode screen exercises the same
 * rendering an unlucky real sync would. None of this is invented approval: it is what
 * `ProviderTemplateOut` looks like once a status arrives, transcribed rather than upgraded — the
 * pending one stays pending here, exactly as it would while a fixture-mode reviewer waited on
 * Meta.
 */
export const WHATSAPP_TEMPLATES: ProviderTemplateOut[] = [
  {
    id: "delivery_confirmed",
    channel: "whatsapp",
    provider_name: "meta",
    provider_template_id: "1187364502983471",
    language: "en",
    category: "utility",
    parameter_format: "positional",
    status: "approved",
    status_reason: null,
    status_checked_at: "2026-09-21T04:12:09Z",
    variables: [
      { key: "customer_name", component: "body", position: 1, button_index: 0, kind: "text", parameter_name: "" },
      { key: "tracking_url", component: "button", position: 0, button_index: 0, kind: "payload", parameter_name: "" },
    ],
    preview_text: "Hi {{1}}, your shipment is out for delivery today.",
    sendable: true,
    created_at: "2026-08-30T09:14:22Z",
    updated_at: "2026-09-21T04:12:09Z",
  },
  {
    id: "payment_reminder",
    channel: "whatsapp",
    provider_name: "meta",
    provider_template_id: null,
    language: "en",
    category: "utility",
    parameter_format: "positional",
    status: "pending",
    status_reason: null,
    status_checked_at: "2026-09-20T11:03:44Z",
    variables: [
      { key: "amount_due", component: "body", position: 1, button_index: 0, kind: "text", parameter_name: "" },
    ],
    preview_text: "Reminder: {{1}} is due on your account.",
    sendable: false,
    created_at: "2026-09-18T15:40:02Z",
    updated_at: "2026-09-20T11:03:44Z",
  },
  {
    id: "promo_diwali",
    channel: "whatsapp",
    provider_name: "meta",
    provider_template_id: "1298374650192837",
    language: "en",
    category: "marketing",
    parameter_format: "positional",
    status: "rejected",
    status_reason: "Promotional content requires an opt-in confirmation step.",
    status_checked_at: "2026-09-15T07:22:51Z",
    variables: [],
    preview_text: "Celebrate with us — 20% off this Diwali.",
    sendable: false,
    created_at: "2026-09-10T10:00:00Z",
    updated_at: "2026-09-15T07:22:51Z",
  },
];

/* -------------------------------- audience -------------------------------- */

/** Three contacts: one ordinary, one suppressed (so the suppression list is not empty in
 *  fixture mode either), one whose only distinguishing fact is being on a list. */
export const CONTACTS: ContactOut[] = [
  {
    id: "8d3f0a11-6b2e-4c7a-9d51-2f8e6a0c4b17",
    email: "priya.raghavan@vaayu-logistics.example.com",
    name: "Priya Raghavan",
    attributes: { company: "Vaayu Logistics", title: "Ops Lead" },
    created_at: "2026-09-01T09:00:00Z",
    updated_at: "2026-09-01T09:00:00Z",
  },
  {
    id: "b6f1c4d8-9e22-4a6f-8b3d-1a7c5e0f9d24",
    email: "arjun.mehta@vaayu-freight.example.com",
    name: "Arjun Mehta",
    attributes: { company: "Vaayu Freight" },
    created_at: "2026-09-05T11:30:00Z",
    updated_at: "2026-09-05T11:30:00Z",
  },
  {
    id: "f2a9d7e3-4c1b-4f88-9a06-3e5d8c1b7a90",
    email: "bounced@unreachable.example.com",
    name: "Bounced Address",
    attributes: {},
    created_at: "2026-08-20T08:15:00Z",
    updated_at: "2026-09-10T14:00:00Z",
  },
];

export const CONTACT_LISTS: ContactListOut[] = [
  {
    id: "1c9e6b4a-7d2f-4a83-b0e5-6f2a1d9c8b34",
    name: "Newsletter",
    description: "Monthly product updates.",
    created_at: "2026-08-15T10:00:00Z",
  },
  {
    id: "9a4d2f7c-3b1e-4d95-8c6a-0e7b3f2d1a56",
    name: "VIP customers",
    description: "",
    created_at: "2026-09-02T13:20:00Z",
  },
];

/** Which contacts (by id) each list holds — kept beside the fixtures rather than on `ContactOut`
 *  itself, matching the service's own shape: membership is a join table, not a column. */
export const CONTACT_LIST_MEMBERS: Record<string, string[]> = {
  [CONTACT_LISTS[0]!.id]: [CONTACTS[0]!.id, CONTACTS[1]!.id],
  [CONTACT_LISTS[1]!.id]: [CONTACTS[0]!.id],
};

export const SUPPRESSIONS: SuppressionOut[] = [
  {
    id: "5e8b1d4a-2f6c-4a97-b3d0-8c1e7a4f9b62",
    contact_id: CONTACTS[2]!.id,
    reason: "bounced",
    note: "",
    created_at: "2026-09-10T14:00:00Z",
  },
];
