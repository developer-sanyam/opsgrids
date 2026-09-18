/**
 * The product catalogue.
 *
 * This is the marketing site's single source of truth: the index, every product page,
 * the pricing table and the sitemap are all generated from it. **Adding a product is an
 * entry here, not a new page** — which is the frontend half of the promise ADR-0001 makes
 * about the backend.
 *
 * Keep `status` honest. A product marked `available` that a customer cannot actually buy
 * is the fastest way to lose a deal at the demo, so `beta` and `soon` exist and the
 * templates render them differently.
 */

export type ProductStatus = 'available' | 'beta' | 'soon';

export interface PlanTier {
  /** Shown on the card. */
  name: string;
  /** Minor units per seat per month, in INR. Null means "talk to us". */
  pricePerSeatMinor: number | null;
  blurb: string;
  includes: string[];
}

export interface Product {
  /** URL segment and the platform's product key — they are deliberately the same string. */
  slug: string;
  name: string;
  /** One line. Appears in the nav, the card and the meta description. */
  tagline: string;
  /** The problem, in the customer's words rather than ours. */
  problem: string;
  category: 'People' | 'Revenue' | 'Work' | 'Operations' | 'Finance';
  status: ProductStatus;
  /** Two to five. More than five and nothing stands out. */
  features: { title: string; body: string }[];
  /** What it does with the rest of the ecosystem. This is the whole pitch. */
  connects: string[];
  plans: PlanTier[];
}

export const PRODUCTS: Product[] = [
  {
    slug: 'hrms',
    name: 'HRMS',
    tagline: 'Attendance, leave and payroll that agree with each other',
    problem:
      'Attendance lives in a biometric device, leave lives in a spreadsheet, and payroll is ' +
      'someone reconciling the two on the 28th. Every month.',
    category: 'People',
    status: 'soon',
    features: [
      {
        title: 'Attendance that survives an audit',
        body:
          'Biometric readers are the record for office staff; a geo-fenced mobile punch covers ' +
          'field teams and dead devices. Raw punches are immutable and the working day is derived ' +
          'from them, so a correction never rewrites what a device reported.',
      },
      {
        title: 'A leave balance you can explain',
        body:
          'Balance is the sum of a ledger, not a number someone edited. Every accrual, every ' +
          'deduction and every reversal is a row with a date and an actor — so a disputed balance ' +
          'is a query, not an argument.',
      },
      {
        title: 'Payroll on the same numbers',
        body:
          'Payroll reads the attendance summary directly. Statutory rates are versioned by ' +
          'effective date, so re-running March uses March’s PF and ESI rather than today’s.',
      },
      {
        title: 'Joining and leaving, handled',
        body:
          'Onboarding checklists gate activation. Exit revokes access across every product the ' +
          'person could reach, and keeps the history.',
      },
    ],
    connects: [
      'A new employee automatically gets an account, a department and access to the products their role needs',
      'Approved leave re-derives that day’s attendance without anyone re-keying it',
      'Someone leaving loses access everywhere at once — not once per product',
    ],
    plans: [
      {
        name: 'Starter',
        pricePerSeatMinor: 6_000,
        blurb: 'Attendance and leave for a growing team.',
        includes: ['Biometric and mobile attendance', 'Leave policies and approvals', 'Employee records', 'Up to 100 people'],
      },
      {
        name: 'Business',
        pricePerSeatMinor: 12_000,
        blurb: 'Adds payroll and the statutory paperwork.',
        includes: ['Everything in Starter', 'Payroll runs and payslips', 'PF, ESI, PT and TDS', 'Shift rosters and geo-fencing'],
      },
      {
        name: 'Enterprise',
        pricePerSeatMinor: null,
        blurb: 'Multi-location, custom approvals, SSO.',
        includes: ['Everything in Business', 'SAML and OIDC single sign-on', 'Custom approval matrices', 'Priority support'],
      },
    ],
  },
  {
    slug: 'crm',
    name: 'CRM',
    tagline: 'One customer record, shared by every team that touches them',
    problem:
      'Sales has a pipeline, delivery has a client list, finance has a billing name. Three ' +
      'spellings of the same company, and nobody knows which is right.',
    category: 'Revenue',
    status: 'soon',
    features: [
      {
        title: 'The customer record is the platform’s, not the CRM’s',
        body:
          'Customers and contacts live once. The project tool, the invoice and the campaign all ' +
          'point at the same record, so a change of address happens in one place.',
      },
      {
        title: 'Pipelines that match how you sell',
        body: 'Stages, probabilities and activities you configure — not a template you work around.',
      },
      {
        title: 'Scope that reflects your org chart',
        body:
          'A rep sees their accounts, a manager sees their team’s, a director sees the region. ' +
          'Enforced in the query, not hidden in the interface.',
      },
    ],
    connects: [
      'Win a deal and the project already knows which customer it is for',
      'Invoices bill the same customer record sales sold to',
      'Territory follows the reporting line maintained in HRMS',
    ],
    plans: [
      {
        name: 'Free',
        pricePerSeatMinor: 0,
        blurb: 'Two users, one pipeline. Enough to decide.',
        includes: ['2 users', '1 pipeline', 'Contacts and activities'],
      },
      {
        name: 'Starter',
        pricePerSeatMinor: 8_000,
        blurb: 'For a working sales team.',
        includes: ['Unlimited pipelines', 'Custom fields', 'Email logging', 'Reports'],
      },
      {
        name: 'Professional',
        pricePerSeatMinor: 16_000,
        blurb: 'Forecasting, automation and territories.',
        includes: ['Everything in Starter', 'Forecasting', 'Automation rules', 'Territory management'],
      },
      {
        name: 'Enterprise',
        pricePerSeatMinor: null,
        blurb: 'Custom objects, SSO, and an implementation team.',
        includes: ['Everything in Professional', 'Custom objects', 'SSO', 'Dedicated support'],
      },
    ],
  },
  {
    slug: 'pulseboard',
    name: 'PulseBoard',
    tagline: 'What the team is doing, what it cost, what it earned',
    problem:
      'You know the project shipped. You do not know whether it made money, because the hours ' +
      'are in one tool and the invoice is in another.',
    category: 'Work',
    status: 'soon',
    features: [
      {
        title: 'Time against the work, not against a timesheet',
        body: 'Hours attach to the work item. Cost and margin come out of that without a second entry.',
      },
      {
        title: 'Daily updates that are not a meeting',
        body: 'Written standups with blockers surfaced, and a digest for whoever needs the summary.',
      },
      {
        title: 'Quotation to invoice',
        body: 'Quote the work, deliver it, invoice it — with GST handled, against the same customer.',
      },
    ],
    connects: [
      'Projects are for CRM customers, not a separate client list',
      'People and reporting lines come from HRMS',
      'Leave shows on the delivery calendar automatically',
    ],
    plans: [
      {
        name: 'Free',
        pricePerSeatMinor: 0,
        blurb: 'Up to 5 people. Standups and boards.',
        includes: ['5 users', 'Boards and work items', 'Daily updates'],
      },
      {
        name: 'Team',
        pricePerSeatMinor: 5_000,
        blurb: 'Time tracking and project costing.',
        includes: ['Unlimited users', 'Time tracking', 'Project cost and margin', 'Client reporting'],
      },
      {
        name: 'Business',
        pricePerSeatMinor: 9_000,
        blurb: 'Quotations, invoicing and approvals.',
        includes: ['Everything in Team', 'Quotations and invoices', 'Approval workflows', 'Custom fields'],
      },
    ],
  },
  {
    slug: 'projects',
    name: 'Project Management',
    tagline: 'Plans, boards and workflows without the rest of the suite',
    problem: 'You want the delivery half of PulseBoard and none of the marketing surface.',
    category: 'Work',
    status: 'soon',
    features: [
      { title: 'Workflows you define', body: 'States and transitions per work-item type, not a fixed lifecycle.' },
      { title: 'Boards, backlogs and timelines', body: 'The same work, in whichever shape the conversation needs.' },
      { title: 'Capacity that knows about leave', body: 'Planning reads real availability, including approved leave.' },
    ],
    connects: ['Shares people and teams with HRMS', 'Shares customers with CRM'],
    plans: [
      { name: 'Team', pricePerSeatMinor: 4_000, blurb: 'Boards, workflows, timelines.', includes: ['Unlimited projects', 'Custom workflows', 'Timeline view'] },
      { name: 'Business', pricePerSeatMinor: 7_500, blurb: 'Capacity planning and reporting.', includes: ['Everything in Team', 'Capacity planning', 'Portfolio reporting'] },
    ],
  },
  {
    slug: 'support',
    name: 'Support & IT',
    tagline: 'One queue for customers and staff, with SLAs that mean something',
    problem: 'Requests arrive by email, WhatsApp and someone walking over. None of it is measurable.',
    category: 'Operations',
    status: 'soon',
    features: [
      { title: 'Tickets with real SLAs', body: 'Targets by priority and customer, measured — not aspirational.' },
      { title: 'Customer and internal in one place', body: 'The same tooling for a client issue and a broken laptop, with different visibility.' },
      { title: 'Context on arrival', body: 'The agent sees the customer, their subscription and their open work before replying.' },
    ],
    connects: ['Tickets attach to CRM customers', 'Staff requests route by department from HRMS'],
    plans: [
      { name: 'Starter', pricePerSeatMinor: 4_000, blurb: 'Queues, priorities, email intake.', includes: ['Unlimited tickets', 'Email intake', 'Priorities'] },
      { name: 'Business', pricePerSeatMinor: 8_000, blurb: 'SLAs, automation and a portal.', includes: ['Everything in Starter', 'SLA policies', 'Automation', 'Customer portal'] },
    ],
  },
  {
    slug: 'marketing',
    name: 'Marketing',
    tagline: 'Campaigns measured against revenue, not impressions',
    problem: 'You can report what a campaign cost. You cannot report what it earned.',
    category: 'Revenue',
    status: 'soon',
    features: [
      { title: 'Campaigns with budget and attribution', body: 'Spend and attributed revenue on the same object, so ROI is a column.' },
      { title: 'A calendar the whole team can see', body: 'Content, channels and approvals in one place.' },
      { title: 'Goals that track themselves', body: 'Measured from platform data rather than a monthly copy-paste.' },
    ],
    connects: ['Attribution reads deals from CRM', 'Campaign work and cost come from PulseBoard'],
    plans: [
      { name: 'Starter', pricePerSeatMinor: 5_000, blurb: 'Calendar, campaigns, channels.', includes: ['Campaign calendar', 'Channel management', 'Content approvals'] },
      { name: 'Business', pricePerSeatMinor: 10_000, blurb: 'Attribution and goal tracking.', includes: ['Everything in Starter', 'Revenue attribution', 'Goal tracking', 'Automation'] },
    ],
  },
  {
    slug: 'finance',
    name: 'Finance',
    tagline: 'Quotations, invoices and GST that reconcile',
    problem: 'Invoicing lives in a spreadsheet and the numbers stop matching the work in month two.',
    category: 'Finance',
    status: 'soon',
    features: [
      { title: 'GST handled properly', body: 'CGST and SGST within a state, IGST across, zero-rated export under LUT — with the paperwork.' },
      { title: 'Invoices are immutable', body: 'A correction is a credit note. An invoice you can edit is one you cannot defend.' },
      { title: 'Billed from the work', body: 'Lines come from delivered work and tracked time, not retyped.' },
    ],
    connects: ['Invoices the CRM customer', 'Bills the work recorded in PulseBoard'],
    plans: [
      { name: 'Business', pricePerSeatMinor: 7_000, blurb: 'Quotations, invoices, GST.', includes: ['Quotations', 'GST invoicing', 'Credit notes', 'Payment tracking'] },
      { name: 'Enterprise', pricePerSeatMinor: null, blurb: 'Multi-entity and e-invoicing.', includes: ['Everything in Business', 'Multiple legal entities', 'E-invoicing (IRN)', 'Custom reporting'] },
    ],
  },
  {
    slug: 'operations',
    name: 'Operations',
    tagline: 'Automation and reporting across every product you run',
    problem: 'The work that matters happens between the tools, and today a person is the integration.',
    category: 'Operations',
    status: 'soon',
    features: [
      { title: 'Rules across products', body: 'When someone joins, when a deal closes, when a department changes — do this.' },
      { title: 'Bulk import and export', body: 'Move data in and out at volume, with a record of what happened.' },
      { title: 'Reporting over everything', body: 'One place to ask a question that spans two products.' },
    ],
    connects: ['Reacts to events from every product', 'Reports across every product you have activated'],
    plans: [
      { name: 'Business', pricePerSeatMinor: 3_000, blurb: 'Automation and cross-product reports.', includes: ['Automation rules', 'Bulk operations', 'Cross-product reporting'] },
    ],
  },
  {
    slug: 'sales-crm',
    name: 'Sales CRM',
    tagline: 'The pipeline, and nothing else',
    problem: 'You want leads and deals without configuring a platform first.',
    category: 'Revenue',
    status: 'soon',
    features: [
      { title: 'Live in ten minutes', body: 'Leads, stages, activities. No implementation project.' },
      { title: 'Grows into the full CRM', body: 'Same records — when you need territories and forecasting, activate CRM and nothing migrates.' },
    ],
    connects: ['Upgrades to CRM without moving a single record'],
    plans: [
      { name: 'Free', pricePerSeatMinor: 0, blurb: 'Up to 3 users.', includes: ['3 users', 'Leads and deals', 'Activity log'] },
      { name: 'Starter', pricePerSeatMinor: 5_000, blurb: 'Unlimited users and reports.', includes: ['Unlimited users', 'Custom stages', 'Reports'] },
    ],
  },
];

export const bySlug = (slug: string): Product | undefined =>
  PRODUCTS.find((p) => p.slug === slug);

export const CATEGORIES = ['People', 'Revenue', 'Work', 'Operations', 'Finance'] as const;

export const byCategory = (): { category: string; products: Product[] }[] =>
  CATEGORIES.map((category) => ({
    category,
    products: PRODUCTS.filter((p) => p.category === category),
  })).filter((g) => g.products.length > 0);

/** Rupees from minor units, for display only — never for arithmetic. */
export const rupees = (minor: number): string =>
  new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 0 })
    .format(minor / 100);
