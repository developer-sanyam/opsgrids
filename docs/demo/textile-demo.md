# Textile demo walkthrough (Hitesh Textiles)

A 15-minute click-through for a textile trader who needs inventory, GST billing, WhatsApp
automation and Meta ads. Everything here is seeded by `tools/demo/seed_textile.py` into the demo
org **Hitesh Textiles (Demo)**. Say only what the platform does today: the "Say instead" notes
below mark every place where the honest answer is "not yet".

## Before you start

| App | URL |
|---|---|
| Inventory (Operations) | http://localhost:4380 |
| Billing (Finance) | http://localhost:4360 |
| Meta ads (Marketing) | http://localhost:4370 |
| WhatsApp and email (Engage) | http://localhost:4420 |

Login on every app: `hitesh.demo@example.com` / `Hitesh-Demo-2026!x`. Type it in; do not use the
"Demo access / Tap to fill" button, which fills a different account (`*@opsgrids-demo.example.com`)
that does not exist in the Hitesh demo database. Open all four in separate
tabs and log in once each before the call. Loading the data on another machine is described in
`tools/demo/demo-stack.sh` and `tools/demo/seed_textile.py` (the seeder is idempotent).

## 1. Inventory (about 5 min) — app on :4380

1. **Overview.** Three warehouses: Surat Fabric Godown, Bhiwandi Distribution Centre, Tiruppur
   Yarn & Trims Store. "One view across all your godowns."
2. **Inventory list.** 25 items across fabric, yarn and trims, each with an HSN code. Search a
   fabric; filter by category (**Item Categories** shows the eight groupings).
   - Cotton poplin white (COT-POP-58-WHT): 4,850 m
   - Rayon printed (RAY-44-PRT): 8,200 m
   - Georgette black (GEO-44-BLK): 5,100 m
   - Dupion silk (SLK-DUP-44-GLD): 620 m
   - Cotton yarn 30s (YRN-CTN-30S-CMB): 5,200 kg
   - Buttons (BTN-POLY-18L-WHT): 48,000 pcs
3. **Low stock.** Switch the list to the low-stock filter, then open Cotton twill khaki (COT-TWL-58-KHK): 640 m against a reorder point of
   1,200 m, with purchase order PO-2026-0305 already open.
4. **Item detail.** Open Indigo denim (DEN-58-IND-10): 6,400 m on hand, 1,800 m reserved. Show the
   fabric attributes (GSM, width, composition, colour, weave) and the movement history, an
   append-only ledger where every receipt, adjustment and count leaves a row. Show the -35 m
   denim damage adjustment.
5. **Purchase orders.** PO-2026-0301 to 0308 in mixed states: received, approved, one overdue,
   one draft awaiting approval. Open a received PO to show that receiving stock posts to the
   ledger. Open **Suppliers** for the eight mills.

**Say instead**
- Fabric-roll tracking, batch/lot numbers and cut-to-length are not built. "Stock is tracked per
  item and warehouse in metres; roll and batch tracking is the next phase."
- Stock transfers between godowns, stocktake, stock valuation and low-stock alerts are also on
  the roadmap. Low stock is visible on the item, not pushed as a notification.
- Do not offer import/export or printed labels.

## 2. GST billing (about 4 min) — app on :4360

1. **Invoices.** Nine invoices and one credit note (CN-2026-0501) in mixed states.
   - INV-2026-0501: paid, ₹2,51,160 (CGST/SGST, in-state buyer)
   - INV-2026-0502: overdue, ₹2,73,105 (IGST, out-of-state buyer)
   - INV-2026-0503: overdue, 60% paid, ₹2,58,300
   - INV-2026-0505: 50% advance received, ₹8,25,300
   - INV-2026-0508: SEZ buyer, ₹4,31,600, charged IGST even though the buyer is in Gujarat
   - INV-2026-0509: draft
2. **Invoice detail.** Open a CGST/SGST invoice and an IGST invoice side by side. Point at the HSN
   codes (5208, 5209, 5407, 5513, and 9606 for buttons) and the mix of 5% fabric and 18% trims
   lines.
3. **Payments and Customers.** Eight buyers with valid GSTINs. Show partial payments allocated to
   invoices.
4. **New invoice.** Open **InvoiceNew** to show the create form. Do not save a real one during
   the demo.
5. **GST Returns, E-way bills, Ledger.** Show they exist for the compliance story.

**Say instead**
- No invoice PDF or print yet. "Invoices are on screen; PDF output is coming."
- The government e-invoice (IRN) submission is not live. Do not click any e-invoice action.
- Foreign-export invoices are not modelled yet.
- The invoice totals block shows a single GST line rather than a CGST/SGST split.

## 3. WhatsApp and email automation (about 3 min) — app on :4420

1. **WhatsApp Templates.** Four templates: order_confirmation, payment_reminder,
   dispatch_update, invoice_issued_alert. They are marked "not submitted to Meta".
2. **WhatsApp Connection.** Show where a business connects its WhatsApp Business account.
3. **Inbox.** The shared inbox for inbound WhatsApp: reply, assign, close.
4. **Campaigns, Lists, Contacts.** Ten contacts in three lists for email campaigns, plus
   sender-domain checks.

**Say instead**
- Nothing sends on WhatsApp until a real WhatsApp Business account is connected and Meta approves
  the templates. That is a setup step for his business, not a code change.
- Automation rules exist in the backend (invoice issued, payment received, purchase order
  received) but there is no screen to create them yet, and rules have no conditions or
  time-based triggers such as "remind 3 days before due".
- Customer-facing WhatsApp on order confirmation and dispatch is not possible yet: those
  events and customer phone numbers are not wired. Position it as the next milestone.
- Do not attempt a live send.

## 4. Meta ads (about 3 min) — app on :4370

1. **Overview.** Spend, revenue, leads and the 8-month revenue-versus-spend chart.
2. **Campaigns.** Filter to "Meta | ..." campaigns:
   - Meta | Wholesale lead ads: ₹4.85L spent, 412 leads, 21 conversions
   - Meta | Click-to-WhatsApp: ₹2.7L spent, 338 leads, 19 conversions
   - Meta | Instagram Reels denim: ₹3.1L spent, 265 leads, 11 conversions
   - Meta | Retargeting: ₹1.55L spent, 144 leads, 14 conversions
   - Also a paused lookalike, an ended festive push and a draft Reels campaign.
   Open one campaign for its budget, audience and results.
3. **Audiences and Channels.** Six audiences; channel roll-up comparing Meta with Google search,
   the Surat Textile Expo stall and a Gulf catalogue mailer.

**Say instead**
- It is a campaign and results tracker. It does not connect to Meta Ads Manager, so it does not
  pull spend or create ads. "Numbers are entered in the platform; a live Meta sync is a
  follow-on."
- Meta campaigns appear under the "Social" channel.
- Do not click **Automations**, **Integrations**, **Reports**, **Settings** or **Help** in this
  app. They are "coming soon" placeholders.

## Close (1 min)

What is ready today: multi-godown stock ledger and purchase orders, GST invoicing with payments,
a shared WhatsApp inbox and templates, and a campaign tracker. What we build next for him, in
order: roll and batch tracking with cut-to-length, invoice PDF, WhatsApp automation for orders
and dispatch on his own Meta account, and a live Meta Ads connection.

## Do not click (all apps)

- Any e-invoice or IRN action in Finance.
- Marketing: Automations, Integrations, Reports, Settings, Help.
- Any "send" on WhatsApp.
- Delete buttons on seeded records. The seeder is idempotent, so re-running it restores what is
  missing, but not what you edited.

## Caveats

The data is synthetic. Screens were not clicked through end to end before this script was
written; open each tab once before the call.
