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
  ContactOut,
  ConversationMessageOut,
  ConversationOut,
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
/**
 * Contacts, masked exactly as `_contact_out` masks them — a local part's first character, then
 * `•••`, then the domain. **Nothing here is a real address with the middle chopped out**: each
 * mask is invented independently of any string a browser would otherwise need to hold, which is
 * the same reason `CONNECTION` carries no `secret_hint` above.
 */
export const CONTACTS: ContactOut[] = [
  {
    id: "c1a0f2e4-9b3d-4a7c-8e1f-2d6b9c4a5e10",
    email: "p•••@vaayu-logistics.example.com",
    name: "Priya Raghavan",
    attributes: { plan: "enterprise", region: "west" },
    created_at: "2026-08-29T09:18:02Z",
    updated_at: "2026-08-29T09:18:02Z",
  },
  {
    id: "d2b1e3f5-8c4e-4b8d-9f2a-3e7c0d5b6f21",
    email: "a•••@vaayu-freight.example.com",
    name: "Arjun Mehta",
    attributes: { plan: "growth" },
    created_at: "2026-09-10T14:02:11Z",
    updated_at: "2026-09-18T08:44:59Z",
  },
];

/**
 * Suppressions in three of the four reasons the vocabulary carries — `manual` twice, since it is
 * the only one this screen ever writes or lifts, and one from each automatic source so the reason
 * filter has something real to filter on.
 */
export const SUPPRESSIONS: SuppressionOut[] = [
  {
    id: "e3c2f4a6-7d5f-4c9e-8a3b-4f8d1e6c7a32",
    email: "b•••@vaayu-logistics.example.com",
    reason: "bounced",
    created_at: "2026-09-05T11:30:00Z",
  },
  {
    id: "f4d3a5b7-6e6a-4dae-9b4c-5a9e2f7d8b43",
    email: "u•••@vaayu-freight.example.com",
    reason: "unsubscribed",
    created_at: "2026-09-12T16:20:44Z",
  },
  {
    id: "a5e4b6c8-5f7b-4ebf-ac5d-6b0f3a8e9c54",
    email: "s•••@vaayu-logistics.example.com",
    reason: "manual",
    created_at: "2026-09-19T10:05:19Z",
  },
];

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

/* ------------------------------------------ inbox ------------------------------------------ */

/**
 * Computed relative to the moment the module loads rather than pinned to a date, unlike every
 * other fixture above: the whole point of these three rows is to exercise the 24-hour window at
 * three different distances from its edge, and a fixed timestamp would drift out of true the day
 * after it was written — exactly the bug `test_inbound_webhook.py`'s own fixtures found first.
 */
const NOW = Date.now();
const hoursAgo = (h: number) => new Date(NOW - h * 60 * 60 * 1000).toISOString();

/** Freshly active: plenty of the window left, and matched to a CRM contact. */
export const CONVERSATION_OPEN: ConversationOut = {
  id: "b1a2c3d4-1111-4a1a-8a1a-000000000001",
  channel: "whatsapp",
  wa_address: "•••••• 4210",
  contact_id: "c1a0f2e4-9b3d-4a7c-8e1f-2d6b9c4a5e10",
  last_customer_message_at: hoursAgo(2),
  within_service_window: true,
  status: "open",
  assignee_membership_id: null,
  created_at: hoursAgo(30),
  updated_at: hoursAgo(2),
};

/** Ten minutes from the window closing — the composer's countdown has something to count down
 *  to, and a stranger: no CRM contact has ever matched this number. */
export const CONVERSATION_CLOSING_SOON: ConversationOut = {
  id: "b1a2c3d4-2222-4a1a-8a1a-000000000002",
  channel: "whatsapp",
  wa_address: "•••••• 7788",
  contact_id: null,
  last_customer_message_at: hoursAgo(23 + 50 / 60),
  within_service_window: true,
  status: "open",
  assignee_membership_id: null,
  created_at: hoursAgo(23 + 50 / 60),
  updated_at: hoursAgo(23 + 50 / 60),
};

/** The window closed a few hours ago and nobody has replied since — the composer's template-picker
 *  state, on a conversation still `open` (an operator has not triaged it, the window just lapsed). */
export const CONVERSATION_EXPIRED: ConversationOut = {
  id: "b1a2c3d4-3333-4a1a-8a1a-000000000003",
  channel: "whatsapp",
  wa_address: "•••••• 5599",
  contact_id: "d2b1e3f5-8c4e-4b8d-9f2a-3e7c0d5b6f21",
  last_customer_message_at: hoursAgo(28),
  within_service_window: false,
  status: "open",
  assignee_membership_id: null,
  created_at: hoursAgo(50),
  updated_at: hoursAgo(28),
};

/** A closed conversation — triaged and done, until the customer writes back. */
export const CONVERSATION_CLOSED: ConversationOut = {
  id: "b1a2c3d4-4444-4a1a-8a1a-000000000004",
  channel: "whatsapp",
  wa_address: "•••••• 1023",
  contact_id: "c1a0f2e4-9b3d-4a7c-8e1f-2d6b9c4a5e10",
  last_customer_message_at: hoursAgo(72),
  within_service_window: false,
  status: "closed",
  assignee_membership_id: "11111111-2222-4333-8444-555555555555",
  created_at: hoursAgo(96),
  updated_at: hoursAgo(70),
};

export const CONVERSATIONS: ConversationOut[] = [
  CONVERSATION_OPEN,
  CONVERSATION_CLOSING_SOON,
  CONVERSATION_EXPIRED,
  CONVERSATION_CLOSED,
];

export const CONVERSATION_MESSAGES: Record<string, ConversationMessageOut[]> = {
  [CONVERSATION_OPEN.id]: [
    {
      id: "m1",
      direction: "inbound",
      message_type: "text",
      body: "Hi, is my order still on track for tomorrow?",
      occurred_at: hoursAgo(2.2),
      provider_message_id: "wamid.fixture1",
      profile_name: "Priya Raghavan",
    },
    {
      id: "m2",
      direction: "outbound",
      message_type: "text",
      body: "Yes — it's out for delivery and should arrive by 6pm.",
      occurred_at: hoursAgo(2),
      provider_message_id: "wamid.fixture2",
      profile_name: null,
    },
  ],
  [CONVERSATION_CLOSING_SOON.id]: [
    {
      id: "m3",
      direction: "inbound",
      message_type: "text",
      body: "Do you deliver to Whitefield?",
      occurred_at: hoursAgo(23 + 50 / 60),
      provider_message_id: "wamid.fixture3",
      profile_name: null,
    },
  ],
  [CONVERSATION_EXPIRED.id]: [
    {
      id: "m4",
      direction: "inbound",
      message_type: "text",
      body: "Can I get a refund for the damaged item?",
      occurred_at: hoursAgo(28),
      provider_message_id: "wamid.fixture4",
      profile_name: "Arjun Mehta",
    },
  ],
  [CONVERSATION_CLOSED.id]: [
    {
      id: "m5",
      direction: "inbound",
      message_type: "text",
      body: "Thanks for sorting that out!",
      occurred_at: hoursAgo(72),
      provider_message_id: "wamid.fixture5",
      profile_name: "Priya Raghavan",
    },
    {
      id: "m6",
      direction: "outbound",
      message_type: "text",
      body: "Anytime — glad it's resolved.",
      occurred_at: hoursAgo(70),
      provider_message_id: "wamid.fixture6",
      profile_name: null,
    },
  ],
};
