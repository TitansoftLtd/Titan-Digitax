# Titan Digitax

Files ERPNext Sales Invoices and credit notes with **KRA eTIMS** (Kenya Revenue Authority)
through **[DigiTax](https://ke.docs.digitax.tech)**, and prints the KRA-verified invoice.

```
Sales Invoice submitted in ERPNext ──► Titan Digitax ──► DigiTax API ──► KRA eTIMS
                                           ▲                  │
                                           └── callback ◄─────┘  (sale ID, receipt URL, QR)
```

It works for any ERPNext company. Nothing in this app is specific to one kind of business.
Other apps can extend it (for example, a school app that pulls invoices from its student
system), but you don't need any other app to file invoices.

- **Several companies on one site**: each company has its own DigiTax account, credentials
  and settings. One company's invoices are never filed under another company's account.
- **Automatic or manual sending**: invoices can go to DigiTax the moment they're submitted,
  or be held and sent by hand, one at a time or by date range.
- **Retries and recovery**: failed sends are retried hourly, and sends whose DigiTax
  callback never arrived are checked and fixed.

---

## Contents

1. [Before you start](#1-before-you-start)
2. [Installation](#2-installation)
3. [Quick setup checklist](#3-quick-setup-checklist)
4. [Digitax Company Settings: every field explained](#4-digitax-company-settings-every-field-explained)
5. [Digitax Items: what DigiTax sells under](#5-digitax-items-what-digitax-sells-under)
6. [Sending invoices](#6-sending-invoices)
7. [Correcting a filed invoice](#7-correcting-a-filed-invoice)
8. [Printing](#8-printing)
9. [Monitoring and scheduled jobs](#9-monitoring-and-scheduled-jobs)
10. [Troubleshooting](#10-troubleshooting)
11. [For developers: extending this app](#11-for-developers-extending-this-app)
12. [Reference links](#12-reference-links)

---

## 1. Before you start

You need:

- **Frappe / ERPNext v16** (the app declares `frappe >=16, <17`).
- **A DigiTax account per company**, with the business registered for KRA eTIMS. DigiTax
  has a **sandbox** (testing) environment and a **LIVE** environment. Use sandbox first.
- **A DigiTax API key** for each company. In the DigiTax dashboard, go to **Integrations → Add API
  KEY**, name it, click **Generate key**, and copy it straight away. DigiTax won't show it
  again ([how to](https://ke.docs.digitax.tech/docs/start-using-the-api.md)).
- **A site DigiTax can reach from the internet.** DigiTax reports the final result of each
  sale by calling back to your site, using the site's own URL. A site that only runs on
  `localhost` can send, but never receives callbacks. The hourly reconciliation job (section 9)
  still picks the results up eventually.
- **Each company's Country set to Kenya** in ERPNext (Company form), since that's where
  DigiTax files.

---

## 2. Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench --site your-site.example.com install-app titan_digitax
bench --site your-site.example.com migrate
```

Then **switch DigiTax on for the site**. Every send, retry and callback checks this
site-level switch first, so nothing is filed until it's set:

```bash
bench --site your-site.example.com set-config sync_with_digitax 1
```

On Frappe Cloud, add `sync_with_digitax` = `1` under **Site → Site Config**. To stop all
DigiTax traffic on a site at once (for example on a copy of production), set it to `0` or
remove it.

Installing the app also:

- **Adds a "Digitax" tab** to **Sales Invoice** (sale ID, status, receipt links, error
  message, retry count, amendment history) and to **Item** (the link to a Digitax Item).
- **Creates a service user**, `digitaxsyncuser@example.com` ("Digitax Sync User"). Every
  DigiTax action runs as this user (see [Role Settings](#tab-role-settings)).
- **Adds the scheduled jobs** listed in [section 9](#9-monitoring-and-scheduled-jobs).

---

## 3. Quick setup checklist

For each company that files with DigiTax:

1. **Company form:** Country = Kenya, and fill in **Tax ID** (the company's KRA PIN),
   phone, email and logo, which appear on printed invoices.
2. **Digitax Company Settings → New**, pick the company, then:
   - **Credentials tab:** Base URL and API Key.
   - **Role Settings tab:** Send Role, Item Sync Role and Virtual Amendment Role. Check the
     Digitax Sync User is set.
   - **General Settings tab:** choose the Payment Type Code, and decide whether to tick
     **Block Automatic Invoice Sending**.
   - **Item Settings tab:** only if most of your items share the same codes (see
     [Item Settings](#tab-item-settings)).
3. **Create Digitax Items** (section 5), one per kind of thing you sell. Click **Sync to Digitax**
   on each one.
4. **Link your ERPNext Items** to their Digitax Item. The field is on the Item's **Digitax** tab.
5. **Test** on the DigiTax sandbox: submit an invoice, then check its **Digitax** tab shows a Sale
   ID and a status, and that **Print Digitax Invoice** shows a QR code.
6. When you're happy, change the Base URL and API Key to your **LIVE** ones.

---

## 4. Digitax Company Settings: every field explained

One record per company (**Desk → Digitax Company Settings**). Everything DigiTax needs for
that company lives here. No company ever uses another company's values.

### Tab: General Settings

| Field | What it means | What to put |
|---|---|---|
| **Company** | The ERPNext company these settings belong to. | Pick the company. It can't be changed after saving. |
| **Enabled** | Master switch for this company. When off, **new sales are not sent** and the Digitax buttons disappear from its invoices. Credit notes and corrections for sales already filed still go through, so nothing filed is left stranded. | Tick when the company is ready to file. |
| **Block Automatic Invoice Sending** | When ticked, invoices and credit notes are **not** sent on submit, and the hourly retry job skips them. Staff send them by hand instead, one at a time or by date range, after confirming "send anyway" (see [section 6](#6-sending-invoices)). | Leave unticked to file everything automatically. Tick it if someone should review invoices before they go to KRA. |

**Invoice Status Codes.** DigiTax (KRA) needs these codes on every filing. Three are fixed by
DigiTax's own code lists. They're shown for reference but **can't be edited**, and they're
the same for every company.

| Field | Value | Meaning |
|---|---|---|
| **Submitted Invoice Status Code** | `02` *(fixed)* | "Approved": a final sale. It's the only valid status for a new sale. DigiTax can't change a sale's status afterwards; corrections are made with credit notes. |
| **Receipt Type Code** | `S` *(fixed)* | "Sale". DigiTax marks this field as deprecated and ignores it. It tells sales and credit notes apart by the request itself. |
| **Cancelled Invoice Status Code** | `04` *(fixed)* | "Canceled". Only used for an invoice that isn't submitted, which the normal sending paths never send. |
| **Default Payment Type Code** | `01` by default (editable) | How customers pay, as KRA records it. It applies to **all** of this company's sales. |

Payment Type Code values ([DigiTax reference](https://ke.docs.digitax.tech/docs/invoice-attributes.md)):

| Code | Meaning | Typical use |
|---|---|---|
| `01` | Cash | Paid in cash at the time of sale |
| `02` | Credit | Invoice issued now, paid later (common when you invoice in advance) |
| `03` | Cash/Credit | Part paid now, the rest later |
| `04` | Bank cheque | Paid by cheque |
| `05` | Debit/credit card | Card payments |
| `06` | Mobile money | M-Pesa and similar |
| `07` | Other | Anything else, such as a bank transfer |

> Which code KRA expects from your business is a tax-reporting decision. Confirm it with your
> accountant or tax advisor.

### Tab: Credentials

| Field | What it means | Example |
|---|---|---|
| **Base URL** | The DigiTax API address for this company's environment. | LIVE: `https://api.digitax.tech/ke/v2`. For the sandbox address, check your DigiTax dashboard or ask DigiTax support. |
| **API Key** | The company's DigiTax API key (sent as the `X-API-Key` header). Stored encrypted. | Paste the key from **DigiTax → Integrations**. Sandbox and LIVE have different keys. |
| **API Key Fingerprint** | *Read-only.* The last 4 characters of the stored key. | Lets you spot two companies accidentally sharing one key. |
| **Callback Token** | A secret added to this company's callback URL, so a callback can only update this company's invoices. | Leave blank. It's generated automatically. |

### Tab: Item Settings

**Default Item Settings.** These are **optional, and empty by default**. They only help when
most of the company's Digitax Items share the same codes, for example a school where every
item is a fee. A value here is **copied into a Digitax Item's empty fields** when the item is
created or saved. It's copied, not linked: the value is stored on the item, and you can still
change it there. Sends always use what's stored on the item.

**If you leave them empty** (the right choice for most businesses), each Digitax Item must be
given its own codes. The Digitax Item form won't save until they're filled in.

| Field | Fills this Digitax Item field | Meaning | Example |
|---|---|---|---|
| **Default Item Class Code** | Item Class Code | KRA classification of what you sell ([how to choose](https://ke.docs.digitax.tech/docs/which-item-class-code-should-i-use.md), [full table](https://ke.docs.digitax.tech/docs/items-item-classification-table.md)). | `99020000` Services, `99010000` Goods, `86000000` Education and training services, `85000000` Healthcare services |
| **Default Item Type Code** | Item Type Code | `1` Raw material, `2` Finished product, `3` Service ([reference](https://ke.docs.digitax.tech/docs/item-attributes-items.md)). | `3` |
| **Default Item Tax Type Code** | Tax Type Code | VAT treatment: `A` Exempt, `B` 16% VAT, `C` 0% (zero-rated), `D` Non-VAT, `E` 8%. | `B` for standard-rated goods |
| **Default Item Bar Code** | Item Bar Code | A barcode/identifier sent on every invoice line (see [section 5](#5-digitax-items-what-digitax-sells-under)). | `SCHOOL_FEES`, `CONSULTING` |
| **Default Origin Nation Code** | Origin Nation Code | Where the item comes from: an ISO 3166 two-letter country code. | `KE` |
| **Default Package Unit Code** | Package Unit Code | KRA packaging unit. For services, DigiTax recommends `NT` ("NET") ([reference](https://ke.docs.digitax.tech/docs/which-units-should-i-use-for-services.md)). | `NT` |
| **Default Quantity Unit Code** | Quantity Unit Code | KRA quantity unit. For services, DigiTax recommends `U` (pieces/item). | `U` |

The class and tax type defaults are also used for invoice lines sent **without** a Digitax
Item. That's only possible when *Require Digitax Item Name* is off (below). Such a line is
**blocked** if these two are empty.

**Item Sync Overrides.** These control how strictly invoice lines must be tied to DigiTax.

| Field | Default | What it means |
|---|---|---|
| **Require Digitax Item Name** | On | Every invoice line's Item must be **linked to a Digitax Item** of this company. DigiTax only ever sees the Digitax Item's name, never your internal ERPNext item name. Lines are grouped by Digitax Item, so an invoice with 5 fee items all linked to "School Fees" is filed as **one** line. Turn it off only if you want DigiTax to see your ERPNext item names directly. |
| **Require Manual Item Sync** | On | The linked Digitax Item must already be **registered with DigiTax** (it has a Digitax ID; see *Sync to Digitax*). Otherwise the send is blocked. |
| **Auto Sync Items to Digitax** | Off | Lets a companion app register Digitax Items with DigiTax automatically. **On its own this app doesn't use it.** Without such an app, register items with the **Sync to Digitax** button. |
| **Auto Match Items by Name** | Off | Lets a companion app link newly created ERPNext Items to an existing Digitax Item automatically. **On its own this app doesn't use it.** Without such an app, link Items by hand. |

### Tab: Role Settings

These decide **who may start** each DigiTax action. Once a user is allowed, the action itself
always **runs as the Digitax Sync User**, so every DigiTax write in the system is recorded
against that one account.

| Field | What it gates | Example |
|---|---|---|
| **Send Role** | The **Send to Digitax** button on a Sales Invoice, and seeing the amendments panel. | `Accounts Manager` |
| **Item Sync Role** | The **Sync to Digitax** button on a Digitax Item. | `Accounts Manager` or `Item Manager` |
| **Virtual Amendment Role** | **Send Virtual Reversal** / **Send Corrected Virtual Sale** (section 7). | `Accounts Manager`. Give this to few people. |
| **Digitax Sync User** | The user every DigiTax action runs as, both automatic and manual. | `digitaxsyncuser@example.com` (created on install with System Manager). Change it only if you create your own service user with enough permissions. |

If a role field is empty, the matching action shows a "Digitax Role Not Configured" message
instead of running. Automatic sending on submit doesn't check roles. It always runs as the
Digitax Sync User, so ordinary sales users can submit invoices normally.

The **Digitax Sync** page and the bulk retry action need **System Manager**.

### Tab: Other Settings

| Field | Default | What it means |
|---|---|---|
| **Target Country** | Kenya | DigiTax only files for companies in this country. The Company's own Country must match, or saving fails. |
| **API Request Timeout (seconds)** | 30 | How long to wait for DigiTax to answer one request before giving up. That attempt is then retried later. |
| **Max Retry Attempts** | 5 | How many times the hourly job retries a failing invoice. After that it stops and raises an alert (see section 9). Manual sends ignore this limit. |
| **Retry Batch Size** | 100 | How many invoices are processed per batch, both for the hourly retry job and for manual date-range sends. At about 5 seconds each, 100 invoices take around 8 minutes. |
| **Background Job Timeout (seconds)** | 600 | The upper time limit for this company's background DigiTax jobs. Raise it only if a very large backlog times out. |

---

## 5. Digitax Items: what DigiTax sells under

DigiTax keeps a **catalogue of items per business**, and every invoice line must point to one.
A **Digitax Item** (**Desk → Digitax Item**) is your company's record of one catalogue entry.
Each company has its own, even if two share a name ("Consulting(ABC)", "Consulting(XYZ)").

**Create one per kind of thing you sell for tax purposes.** It doesn't need to be one per
ERPNext Item: many ERPNext Items can link to the same Digitax Item. A school can file every fee
as a single "School Fees" item; a shop might have "Groceries" (tax type C) and "Electronics"
(tax type B).

| Field | Meaning | Example |
|---|---|---|
| **Item Name** | The name DigiTax/KRA will show. | `School Fees`, `Consulting Services` |
| **Company** | The company that owns it. Picking it fills any empty codes from that company's defaults. | |
| **Enabled** | Item-level on/off switch. | |
| **Item Class / Type / Tax Type / Origin Nation / Package Unit / Quantity Unit Code** | KRA catalogue codes. Same meanings as the [Item Settings table](#tab-item-settings). **Required.** | `99020000` / `3` / `B` / `KE` / `NT` / `U` |
| **Default Unit Price** | Required by DigiTax to create the item. **Never used for invoice amounts**, which always come from the real invoice line. | Your usual price, or `1` |
| **Item Bar Code** | Sent on **every invoice line** using this item. DigiTax requires one. An invoice **won't send** while it's blank. It isn't part of DigiTax's catalogue, so *Sync to Digitax* doesn't send or check it. | `SCHOOL_FEES`, `CONSULTING` |
| **Is Stockable** | Whether the item is stock-tracked, as reported to DigiTax on sales. | Off for services |
| **Synced / Digitax ID / eTIMS Item Code / Last Sync** | *Read-only.* Filled in by **Sync to Digitax**. | |

**Then:**

1. Click **Sync to Digitax** (needs the Item Sync Role). If DigiTax already has an item with
   this name, the two are linked, provided every code matches. If any code differs, the sync
   is **refused** and an alert lists the differences, so you can fix one side. Otherwise the
   item is created at DigiTax.
2. On each ERPNext **Item → Digitax tab**, set **Digitax Item**. An Item linked to another
   company's Digitax Item is treated as not linked when invoicing this company, so it can't
   be filed under the wrong account.

The **Digitax Sync** page (**From DigiTax → Items**) pulls your existing DigiTax catalogue into
Digitax Items. An hourly job does the same for items changed in the last hour. DigiTax's
catalogue has no barcodes, so pulled items get the company's Default Item Bar Code if one is
set, or need one entered.

---

## 6. Sending invoices

**What gets sent.** For each submitted Sales Invoice:

- a **sale** (`sales-with-items`), or for a return, a **credit note**
  (`credit-notes-with-barcode`) against the original sale;
- one line per Digitax Item, with amounts from the real invoice lines;
- the customer's KRA PIN, if known (the Customer's **Tax ID**, else the invoice's
  **Tax ID**). Without a PIN, the sale is filed without customer details.

**When it's sent.**

| Situation | What happens |
|---|---|
| Invoice submitted, automatic sending allowed | Queued right after the submit finishes, and sent within moments. The submit itself never waits on DigiTax. |
| Invoice submitted, **Block Automatic Invoice Sending** ticked | Not sent. The hourly retry job skips it too. |
| **Sales Invoice → Digitax Actions → Send to Digitax** | Sends that one invoice now (needs the Send Role). If automatic sending is blocked, you're asked *"Automatic sending to Digitax is blocked for {company}. Do you want to send {invoice} anyway?"* A comment on the invoice records who confirmed it. |
| **Digitax Sync page → To DigiTax → Invoices** | Sends every unsent invoice for a company, optionally only those with a **posting date between From Date and To Date**. It works through them in batches, oldest first, for up to about 50 minutes, then reports what's left. If automatic sending is blocked, you're asked to confirm "send anyway" first. Needs System Manager. |

The "send anyway" confirmation exists only for these two manual actions. No scheduled or
background job can override the block.

**What stops a send.** The reason is saved in the invoice's **Digitax → Error message**, and
the invoice appears in **Failed Digitax Invoices**:

- `sync_with_digitax` isn't set, the company has no settings, or **Enabled** is off (new sales only);
- the Company's Country doesn't match **Target Country**;
- a line's Item isn't linked to a Digitax Item (*Require Digitax Item Name*);
- the linked Digitax Item isn't synced yet (*Require Manual Item Sync*);
- a line's Digitax Item has no **Item Bar Code**;
- a credit note whose original sale was never filed.

**Retries.** A send that fails, for example because DigiTax is unreachable or returns an error,
is retried by the hourly job: first on the next run, then with growing gaps (1 hour, 4 hours,
then every 24 hours), up to **Max Retry Attempts**. After that the invoice stops being retried
and an alert is raised. A manual send still works.

**Duplicates are safe.** If DigiTax says an invoice number was already filed, the existing
DigiTax sale is adopted rather than filed twice.

---

## 7. Correcting a filed invoice

KRA doesn't allow a filed sale to be edited or cancelled. So once an invoice has been sent:

- **Cancelling it in ERPNext is blocked.** Create a **Credit Note** (Sales Invoice return)
  against it instead. The credit note is filed with DigiTax like any invoice.
- **Virtual amendments** (Sales Invoice → Digitax Actions, needs the Virtual Amendment Role)
  correct what KRA holds **without** changing ERPNext. You give a reason, then:
  1. **Send Virtual Reversal** files a full credit note at DigiTax for the current sale,
     numbered `<invoice>-R` the first time, then `-R1`, `-R2`, …
  2. **Send Corrected Virtual Sale** files a new sale built from the invoice as it is now,
     numbered `<invoice>-S1`, `-S2`, …

  Use this when something KRA holds is wrong but the ERPNext invoice is right, for example
  a customer PIN added after filing. Every step is recorded in the invoice's **Digitax
  Amendments** table.

---

## 8. Printing

**Sales Invoice → Digitax Actions → Print Digitax Invoice** appears once an invoice has been
filed. It prints the **Digitax Tax Invoice** format:

- company details, the DigiTax/KRA serial and invoice numbers, and status;
- the lines exactly as filed, with a per-tax-class breakdown;
- a **QR code** to verify the receipt on eTIMS.

A companion app can replace this with its own print format (see section 11). Company details
come from the **Company** form: name, address, phone, email, Tax ID and logo.

---

## 9. Monitoring and scheduled jobs

**Where to look:**

| Where | What you'll see |
|---|---|
| **Sales Invoice → Digitax tab** | Sent flag, Sale ID, status, receipt links, the last error, retry count |
| **Report: Failed Digitax Invoices** | Every submitted invoice not yet filed that has an error message |
| **Digitax Sync page** | Manual sync runs and their live progress |
| **Error Log** (Desk) | Unexpected errors, titled "Digitax …" |
| `logs/digitax_integration.log` (bench) | A detailed log of every request and response |
| **Actionable Items** | Alerts that need a person: retries used up, sync code mismatches, stuck corrections, unrecognised DigiTax responses. **This app has no Actionable Items list of its own.** A companion app has to provide one (see section 11). Without one, these alerts only go to `digitax_integration.log`. |

**Scheduled jobs** (need the Frappe scheduler running):

| When | Job | What it does |
|---|---|---|
| Hourly | Retry unsent invoices | Resends failed invoices with backoff, company by company, in batches, for up to about 50 minutes. Skips companies with **Block Automatic Invoice Sending** ticked. Also flags corrections stuck halfway. |
| Hourly | Pull items from DigiTax | Brings items changed at DigiTax in the last hour into Digitax Items, for every enabled company. |
| Hourly | Reconcile missing callbacks | For invoices DigiTax accepted but whose callback never arrived (over 2 hours ago), asks DigiTax for the result directly and fills it in. |

---

## 10. Troubleshooting

**"Digitax Actions" doesn't appear on an invoice.** Check:

1. The invoice is **submitted**.
2. The site has `sync_with_digitax` set to `1`.
3. The company has **Digitax Company Settings** with **Enabled** ticked.
4. For **Print Digitax Invoice** only: the invoice has actually been filed.

**"Digitax Role Not Configured" / "Not Permitted".** Set the role in **Role Settings** and
give it to the user.

**"N item(s) with no linked Digitax Item".** Link each listed ERPNext Item to a Digitax Item
(Item → Digitax tab), then use **Send to Digitax**.

**"…linked to a Digitax Item that isn't synced yet".** Open the Digitax Item and click
**Sync to Digitax**.

**"N item(s) with no Item Bar Code".** Enter an **Item Bar Code** on the Digitax Item, or set
the company's Default Item Bar Code and save the item again.

**Digitax Item won't save ("Mandatory fields required").** Fill in the six codes, or set the
company's [Item Settings](#tab-item-settings) defaults if they all apply.

**Sync to Digitax is refused with a mismatch.** DigiTax already has an item with that name but
different codes. Make the two match, on the Digitax Item or in DigiTax, and sync again.

**An invoice was sent but shows no status for hours.** The callback probably couldn't reach
your site: check it's reachable from the internet. The hourly reconciliation job fills it in
after 2 hours.

---

## 11. For developers: extending this app

Companion apps plug in through `hooks.py`. This app never names or imports them.

| Hook | Purpose |
|---|---|
| `digitax_actionable_item_handler` | Dotted path to a function `create_actionable_item(**kwargs)` that records alerts (title, item_type, description, action_required, reference_doctype, reference_name, priority, company…). Without it, alerts are only logged. |
| `digitax_discount_redistribution_handler` | Dotted path to `redistribute_discount(...)`, for businesses that put discounts on invoices as negative-amount lines. It spreads the discount across the real items. Without it, such invoices are blocked with an alert rather than guessed at. |
| `digitax_invoice_print_format` | Name of a Sales Invoice print format that **Print Digitax Invoice** should use instead of `Digitax Tax Invoice`. |

Reusable helpers for your own print formats are in `titan_digitax.titan_digitax.utils.print_format`:
`get_active_digitax_details(doc)` (sale ID, serial, receipt URL), `get_company_print_details(company)`,
`get_qr_code_data_uri(text)` (registered as a Jinja method), and
`titan_digitax.titan_digitax.utils.sales_items.build_digitax_items_payload(..., dry_run=True)`
(the lines exactly as they would be filed).

---

## 12. Reference links

- DigiTax Kenya API docs: <https://ke.docs.digitax.tech>
- Getting an API key: <https://ke.docs.digitax.tech/docs/start-using-the-api.md>
- Invoice codes (payment types, status codes): <https://ke.docs.digitax.tech/docs/invoice-attributes.md>
- Item codes (type, tax type, units, origin): <https://ke.docs.digitax.tech/docs/item-attributes-items.md>
- Choosing an item class code: <https://ke.docs.digitax.tech/docs/which-item-class-code-should-i-use.md>
- Item classification table: <https://ke.docs.digitax.tech/docs/items-item-classification-table.md>
- Units for services: <https://ke.docs.digitax.tech/docs/which-units-should-i-use-for-services.md>
- UNSPSC code search (to find class codes): <https://www.ungm.org/Public/UNSPSC>

---

### Contributing

This app uses `pre-commit` for code formatting and linting. Please [install pre-commit](https://pre-commit.com/#installation) and enable it for this repository:

```bash
cd apps/titan_digitax
pre-commit install
```

Pre-commit is configured to use the following tools for checking and formatting your code:

- ruff
- eslint
- prettier
- pyupgrade

### CI

This app can use GitHub Actions for CI. The following workflows are configured:

- CI: Installs this app and runs unit tests on every push to `develop` branch.
- Linters: Runs [Frappe Semgrep Rules](https://github.com/frappe/semgrep-rules) and [pip-audit](https://pypi.org/project/pip-audit/) on every pull request.

### License

gpl-3.0
