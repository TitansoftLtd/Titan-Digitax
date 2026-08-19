### Titan Digitax

Titan Digitax

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app titan_digitax
```

### Printing a Digitax invoice (`Print Digitax Invoice`)

**Digitax Actions → Print Digitax Invoice** currently opens the in-app
**`Sales Invoice(Digitax)`** print format (`frappe.utils.print(...)` in
`public/js/sales_invoice.js`) — a normal Frappe/wkhtmltopdf print format built from real
Desk data (Company, Digitax Company Settings, the actual Digitax-sent line items), no
external service or headless browser involved.

There was previously a second mechanism, `download_digitax_receipt_pdf`, which rendered
the live [receipt.dg.tax](https://receipt.dg.tax) page for an invoice's `custom_offline_url`
as a styled PDF server-side via a headless browser (Playwright). It was never wired up to
the UI (its only caller was commented out) and Playwright was never actually installed in
any environment running this app, so it was removed entirely rather than kept around
disabled. The in-app print format above is the only receipt/print path now.

#### When `Digitax Actions` does not appear

On a submitted Sales Invoice, the menu requires:

1. **`sync_with_digitax: true`** set in `site_config.json`/`common_site_config.json` —
   this gates every send/action across the whole app; nothing works without it.
2. A **`Digitax Company Settings`** record for the invoice's Company, with **Enabled**
   checked (Desk → Digitax Company Settings — one record per company, holds the
   `base_url`/`api_key` for that company's Digitax/KRA account).
3. Invoice already sent to Digitax (for **Print Digitax Invoice** only):
   `custom_sent_to_digitax` or `custom_sale_id` set.

(The doctypes `Digitax Settings` and `Digitax Company Configuration` referenced by older
versions of this README no longer exist — collapsed entirely into
`Digitax Company Settings`, one self-contained record per company.)

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
