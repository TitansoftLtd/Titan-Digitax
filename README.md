### Titan Digitax

Titan Digitax

### Installation

You can install this app using the [bench](https://github.com/frappe/bench) CLI:

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch develop
bench install-app titan_digitax
```

### Official Digitax receipt PDF (`Print Digitax Invoice`)

**Digitax Actions → Print Digitax Invoice** downloads the live [receipt.dg.tax](https://receipt.dg.tax) page for the invoice's `custom_offline_url` as a styled PDF (server-side via Playwright).

#### Production setup (required once per bench/server)

```bash
bench pip install playwright
playwright install chromium
```

Use the second command when Google Chrome / Chromium is **not** already installed on the server. Alternatively, set a custom browser path in `site_config.json`:

```json
{
  "chrome_path": "/usr/bin/chromium-browser"
}
```

Then rebuild assets and restart:

```bash
bench build --app titan_digitax
bench restart
```

#### Common PDF issues (for future troubleshooting)

| Symptom | Cause | Fix |
|--------|--------|-----|
| **PDF has no styles** — plain unstyled text, missing colours/layout | `receipt.dg.tax` is a JavaScript app. **wkhtmltopdf** (used for normal Frappe print formats) does not execute that JS/CSS, so it only captures bare HTML. | Ensure **Playwright** is installed (`bench pip install playwright`) and a headless browser is available (system Chrome/Chromium or `playwright install chromium`). PDF generation is implemented in `titan_digitax/titan_digitax/utils/print_format.py`. |
| **PDF is cut off at the bottom** — footer, tax rows, signature, or internal data missing; looks like only the first screen was printed | The Digitax receipt page traps content in **`h-screen` / `overflow: auto`** containers (~900px viewport, ~2000px content). A naive headless print only captures the visible viewport. | The integration expands those scroll containers and sizes the PDF page to the full content height before printing. If this regresses, check `EXPAND_SCROLL_CONTAINERS_JS` in `print_format.py`. |

#### Reverting to the local print format

To use the in-app **Digitax Tax Invoice** print format instead of the live receipt URL, in `titan_digitax/public/js/sales_invoice.js` replace the `window.open(download_url)` call with:

```javascript
frappe.utils.print(frm.doctype, frm.doc.name, "Digitax Tax Invoice");
```

#### When `Digitax Actions` does not appear

On a submitted Sales Invoice, the menu requires:

1. **Digitax Settings → Enable** checked
2. The invoice **Company** listed under **Company Configurations** with **Enabled** checked
3. Invoice already sent to Digitax (for **Print Digitax Invoice** only): `custom_sent_to_digitax` or `custom_sale_id` set

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
