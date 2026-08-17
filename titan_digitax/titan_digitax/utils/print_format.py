import frappe
from urllib.parse import urlparse

from frappe import _
from frappe.utils import fmt_money, get_url
from frappe.www.printview import validate_print_permission

from titan_digitax.titan_digitax.utils.sales import (
	_get_trader_invoice_base,
	resolve_digitax_customer_pin,
)

TAX_CLASS_LABELS = {
	"A": "Tax ClassA(EX)",
	"B": "Tax ClassB(16%)",
	"C": "Tax ClassC(0)",
	"D": "Tax ClassD(Non-VAT)",
	"E": "Tax ClassE(8%)",
}

TAX_CLASS_RATES = {
	"A": 0,
	"B": 16,
	"C": 0,
	"D": 0,
	"E": 8,
}

ALLOWED_DIGITAX_RECEIPT_HOSTS = frozenset({"receipt.dg.tax"})
DIGITAX_RECEIPT_VIEWPORT = {"width": 1280, "height": 900}
DIGITAX_RECEIPT_LOAD_TIMEOUT_MS = 60000
DIGITAX_RECEIPT_RENDER_WAIT_MS = 2000

# Digitax receipt pages trap content in h-screen / overflow:auto containers.
# Expand them before PDF capture so the full receipt is included.
EXPAND_SCROLL_CONTAINERS_JS = """() => {
	[...document.querySelectorAll("*")]
		.filter((el) => el.scrollHeight > el.clientHeight + 20)
		.forEach((el) => {
			el.style.height = `${el.scrollHeight}px`;
			el.style.maxHeight = "none";
			el.style.overflow = "visible";
			el.style.overflowY = "visible";
		});

	document.querySelectorAll(".h-screen, .h-full").forEach((el) => {
		el.style.height = "auto";
	});

	return Math.ceil(
		Math.max(
			...[...document.querySelectorAll("*")].map((el) => el.getBoundingClientRect().bottom),
			document.documentElement.scrollHeight,
		)
	);
}"""


def _validate_digitax_receipt_url(url):
	parsed = urlparse(url or "")
	if parsed.scheme not in ("http", "https") or parsed.netloc not in ALLOWED_DIGITAX_RECEIPT_HOSTS:
		frappe.throw(_("Invalid Digitax receipt URL."))


def _launch_playwright_browser(playwright):
	"""Launch headless Chromium, preferring the system Chrome install when available."""
	launch_attempts = []

	chrome_path = frappe.conf.get("chrome_path")
	if chrome_path:
		launch_attempts.append(
			lambda: playwright.chromium.launch(headless=True, executable_path=chrome_path)
		)

	launch_attempts.extend(
		[
			lambda: playwright.chromium.launch(channel="chrome", headless=True),
			lambda: playwright.chromium.launch(channel="chromium", headless=True),
			lambda: playwright.chromium.launch(headless=True),
		]
	)

	last_error = None
	for launch in launch_attempts:
		try:
			return launch()
		except Exception as exc:
			last_error = exc

	frappe.log_error(
		message=f"Could not launch headless browser for Digitax receipt PDF: {last_error}",
		title="Digitax Receipt PDF Error",
	)
	frappe.throw(
		_(
			"Could not generate a styled Digitax receipt PDF because headless Chrome/Chromium "
			"is not available. Install Google Chrome or Chromium, run "
			"'playwright install chromium', or set 'chrome_path' in site config."
		)
	)


def _get_pdf_from_receipt_url(url):
	"""Render the official Digitax receipt page as PDF via headless browser."""
	_validate_digitax_receipt_url(url)

	try:
		from playwright.sync_api import sync_playwright
	except ImportError:
		frappe.throw(
			_(
				"Playwright is required to generate Digitax receipt PDFs. "
				"Install it with: bench pip install playwright"
			)
		)

	with sync_playwright() as playwright:
		browser = _launch_playwright_browser(playwright)
		try:
			page = browser.new_page(viewport=DIGITAX_RECEIPT_VIEWPORT)
			page.goto(url, wait_until="networkidle", timeout=DIGITAX_RECEIPT_LOAD_TIMEOUT_MS)
			page.wait_for_timeout(DIGITAX_RECEIPT_RENDER_WAIT_MS)

			content_height_px = page.evaluate(EXPAND_SCROLL_CONTAINERS_JS)
			page.set_viewport_size(
				{
					"width": DIGITAX_RECEIPT_VIEWPORT["width"],
					"height": max(content_height_px + 50, DIGITAX_RECEIPT_VIEWPORT["height"]),
				}
			)
			page.wait_for_timeout(300)

			paper_height_in = (content_height_px / 96) + 0.5
			return page.pdf(
				width="8.27in",
				height=f"{paper_height_in:.2f}in",
				print_background=True,
				margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
			)
		finally:
			browser.close()


@frappe.whitelist()
def download_digitax_receipt_pdf(invoice_name):
	"""Download the official Digitax receipt page (offline URL) as PDF."""
	doc = frappe.get_doc("Sales Invoice", invoice_name)
	validate_print_permission(doc)

	digitax_details = get_active_digitax_details(doc)
	offline_url = (digitax_details or {}).get("offline_url")
	if not offline_url:
		frappe.throw(_("This invoice has no Digitax receipt URL available to download."))

	pdf_file = _get_pdf_from_receipt_url(offline_url)
	trader_invoice_number = (digitax_details or {}).get("trader_invoice_number") or doc.name
	filename = trader_invoice_number.replace(" ", "-").replace("/", "-")

	frappe.local.response.filename = f"{filename}.pdf"
	frappe.local.response.filecontent = pdf_file
	frappe.local.response.type = "pdf"


def get_qr_code_data_uri(text):
	"""Return a PNG data URI for embedding a QR code in print formats."""
	if not text:
		return ""

	from base64 import b64encode
	from io import BytesIO

	from pyqrcode import create as qrcreate

	stream = BytesIO()
	try:
		qrcreate(str(text)).png(stream, scale=5, quiet_zone=2)
		encoded = b64encode(stream.getvalue()).decode()
	finally:
		stream.close()

	return f"data:image/png;base64,{encoded}"


def get_digitax_tax_breakdown(items):
	"""Build the five tax-class rows shown on Digitax receipts, aggregated across
	every item - each item may carry a different tax_type_code (a real invoice can
	mix VATable and exempt items), so this sums each class's taxable amount across
	all items instead of assuming a single class for the whole invoice.

	Args:
		items: list of dicts, each with "tax_type_code" and "total_amount".
	"""
	totals = {code: 0.0 for code in ("A", "B", "C", "D", "E")}

	for entry in items:
		code = (entry.get("tax_type_code") or "D").upper()
		if code not in totals:
			code = "D"
		totals[code] += abs(float(entry.get("total_amount") or 0))

	rows = []
	for code in ("A", "B", "C", "D", "E"):
		rate = TAX_CLASS_RATES[code]
		taxable_amount = round(totals[code], 2)
		tax_amount = round(taxable_amount * rate / 100, 2) if rate else 0

		rows.append(
			{
				"code": code,
				"label": TAX_CLASS_LABELS[code],
				"taxable_amount": taxable_amount,
				"tax_rate": rate,
				"tax_amount": tax_amount,
			}
		)

	return rows


def get_digitax_print_items(doc, digitax_settings=None):
	"""Return the real Digitax item lines for print display - the same aggregation
	actually POSTed to Digitax, not a single synthetic whole-invoice line.

	dry_run=True: this is a read-only print action, never write/commit/raise an
	Actionable Item just because someone opened Print. If gates fail (e.g. missing
	item links), falls back to the raw invoice lines so the printout isn't empty,
	same pattern as get_school_invoice_print_context.
	"""
	if digitax_settings is None:
		from titan_digitax.titan_digitax.utils.company_config import get_digitax_settings

		digitax_settings = get_digitax_settings(doc.company)

	from titan_digitax.titan_digitax.utils.sales_items import build_digitax_items_payload

	currency = doc.currency or frappe.db.get_value("Company", doc.company, "default_currency")
	logger = frappe.logger("digitax_integration", allow_site=True, file_count=10)
	built = build_digitax_items_payload(doc, digitax_settings, logger, dry_run=True)

	if built.get("ok"):
		raw_items = built["items"]
		return [
			{
				"item_name": entry.get("item_name") or entry.get("item_description") or "Item",
				"quantity": entry.get("quantity") or 1,
				"unit_price": entry.get("unit_price") or 0,
				"total_amount": entry.get("total_amount") or 0,
				# Only set for sales, never for credit notes - no per-item tax type
				# exists to prorate against on the return side either way.
				"tax_type_code": entry.get("item_tax_type_code") or "D",
				"currency": currency,
				"formatted_unit_price": fmt_money(entry.get("unit_price") or 0, currency=currency),
				"formatted_total_amount": fmt_money(entry.get("total_amount") or 0, currency=currency),
			}
			for entry in raw_items
		]

	# Nothing valid was built for Digitax (e.g. item master data drifted since the
	# original send) — fall back to the raw invoice lines so the printout isn't
	# empty. No real tax_type_code available here, defaults to "D" in the tax
	# breakdown below like any other fallback item.
	return [
		{
			"item_name": row.item_name or row.description or row.item_code,
			"quantity": abs(row.qty or 1),
			"unit_price": abs(row.rate or 0),
			"total_amount": abs(row.amount or 0),
			"tax_type_code": "D",
			"currency": currency,
			"formatted_unit_price": fmt_money(abs(row.rate or 0), currency=currency),
			"formatted_total_amount": fmt_money(abs(row.amount or 0), currency=currency),
		}
		for row in doc.items
	]


def _normalize_header_digitax_details(doc):
	if not getattr(doc, "custom_sale_id", None) and not getattr(doc, "custom_offline_url", None):
		return None

	return {
		"source": "header",
		"trader_invoice_number": getattr(doc, "custom_trader_invoice_number", None) or _get_trader_invoice_base(doc),
		"sale_id": getattr(doc, "custom_sale_id", None) or "",
		"offline_url": getattr(doc, "custom_offline_url", None) or "",
		"etims_url": getattr(doc, "custom_etims_url", None) or "",
		"serial_number": getattr(doc, "custom_serial_number", None) or "",
		"invoice_number": getattr(doc, "custom_invoice_number", None) or "",
		"status": getattr(doc, "custom_digitax_status", None) or "",
		"date": getattr(doc, "custom_date", None) or "",
		"time": getattr(doc, "custom_time", None) or "",
		"receipt_type_code": getattr(doc, "custom_receipt_type_code", None) or "",
	}


def _normalize_amendment_digitax_details(row):
	if row.get("status") != "Sent":
		return None

	sale_id = row.get("digitax_sale_id") or ""
	offline_url = row.get("offline_url") or ""
	if not sale_id and not offline_url:
		return None

	return {
		"source": "amendment",
		"trader_invoice_number": row.get("trader_invoice_number") or "",
		"sale_id": sale_id,
		"offline_url": offline_url,
		"etims_url": row.get("etims_url") or "",
		"serial_number": row.get("serial_number") or "",
		"invoice_number": row.get("invoice_number") or "",
		"status": row.get("digitax_status") or row.get("status") or "",
		"date": row.get("digitax_date") or "",
		"time": row.get("digitax_time") or "",
		"receipt_type_code": row.get("receipt_type_code") or "",
		"amendment_type": row.get("amendment_type") or "",
	}


def get_active_digitax_details(doc):
	"""Resolve the latest completed Digitax sale for printing."""
	header_details = _normalize_header_digitax_details(doc)
	if header_details and header_details.get("offline_url"):
		return header_details

	latest_row = None
	for row in reversed(doc.get("custom_digitax_amendments") or []):
		if row.amendment_type not in ("Original Sale", "Virtual Sale"):
			continue

		normalized = _normalize_amendment_digitax_details(row.as_dict())
		if normalized:
			latest_row = normalized
			break

	return latest_row or header_details


def _get_company_logo_url(company_name):
	logo = frappe.db.get_value("Company", company_name, "company_logo")
	if not logo:
		return ""

	if logo.startswith(("http://", "https://")):
		return logo

	return get_url(logo)


def _get_company_details(company_name):
	company = frappe.get_cached_doc("Company", company_name)
	address = ""

	if company.get("company_address"):
		address = frappe.db.get_value("Address", company.company_address, "display") or ""

	if not address:
		address = frappe.db.get_value(
			"Dynamic Link",
			{"link_doctype": "Company", "link_name": company_name, "parenttype": "Address"},
			"parent",
		)
		if address:
			address = frappe.db.get_value("Address", address, "display") or ""

	return {
		"name": company.company_name or company_name,
		"address": address,
		"phone": company.phone_no or "",
		"email": company.email or "",
		"tax_id": company.tax_id or "",
		"logo_url": _get_company_logo_url(company_name),
	}


def get_digitax_print_context(doc):
	"""Build the print context used by the Digitax Tax Invoice print format.

	Shows the real item(s) build_digitax_items_payload produces - the same
	aggregation actually POSTed to Digitax - instead of a single synthetic
	whole-invoice line, so what's printed always matches what was sent. This
	format prints what was actually filed, so (like Sales Invoice(Digitax))
	it requires custom_sent_to_digitax first.
	"""
	if isinstance(doc, str):
		doc = frappe.get_doc("Sales Invoice", doc)

	if not doc.custom_sent_to_digitax:
		frappe.throw(_("This invoice has not been sent to Digitax yet."))

	from titan_digitax.titan_digitax.utils.company_config import get_digitax_settings

	digitax_settings = get_digitax_settings(doc.company)
	digitax_details = get_active_digitax_details(doc)
	customer_pin = resolve_digitax_customer_pin(doc)
	items = get_digitax_print_items(doc, digitax_settings)
	tax_rows = get_digitax_tax_breakdown(items)
	tax_rows = [row for row in tax_rows if row.get("taxable_amount") or row.get("tax_amount")]
	currency = (items[0].get("currency") if items else None) or doc.currency
	grand_total = sum(entry.get("total_amount") or 0 for entry in items)

	document_title = "Credit Note" if doc.is_return else "Sale Invoice"
	status_text = (digitax_details or {}).get("status") or ""
	if status_text:
		status_text = status_text.upper()

	scu_invoice_no = ""
	if digitax_details:
		serial_number = digitax_details.get("serial_number") or ""
		invoice_number = digitax_details.get("invoice_number") or ""
		if serial_number and invoice_number:
			scu_invoice_no = f"{serial_number}/{invoice_number}"
		else:
			scu_invoice_no = invoice_number or serial_number

	return {
		"is_available": bool(digitax_details and digitax_details.get("offline_url")),
		"document_title": document_title,
		"status_text": status_text,
		"digitax": digitax_details or {},
		"company": _get_company_details(doc.company),
		"customer": {
			"name": doc.customer_name or doc.customer or "",
			"pin": customer_pin.get("pin") or "",
			"pin_source": customer_pin.get("source") or "",
		},
		# Named line_items, not items - ctx is a plain dict, and Jinja's attribute
		# resolution tries getattr(ctx, "items") before falling back to
		# ctx["items"], so `ctx.items` in the template would silently resolve to
		# the dict's own built-in .items() method instead of this list.
		"line_items": items,
		"tax_rows": tax_rows,
		"currency": currency,
		"formatted_grand_total": fmt_money(grand_total, currency=currency),
		"scu_invoice_no": scu_invoice_no,
		"invoice_name": doc.name,
		"posting_date": doc.posting_date,
	}


def get_school_invoice_print_context(doc):
	"""Build the print context used by the Sales Invoice(Digitax) print format.

	The Fees table shows the actual item(s) built by build_digitax_items_payload —
	the same aggregation that is literally POSTed to DigiTax — not the raw Sales
	Invoice lines, so what's printed always matches what was sent.

	This format prints what was actually filed, so it only makes sense once the
	invoice has actually been sent - printing before that would either be blank
	or misleadingly show what a hypothetical future send might look like.
	"""
	if isinstance(doc, str):
		doc = frappe.get_doc("Sales Invoice", doc)

	if not doc.custom_sent_to_digitax:
		frappe.throw(_("This invoice has not been sent to Digitax yet."))

	from titan_digitax.titan_digitax.utils.company_config import get_digitax_settings
	from titan_digitax.titan_digitax.utils.sales_items import build_digitax_items_payload

	digitax_settings = get_digitax_settings(doc.company)
	currency = doc.currency or frappe.db.get_value("Company", doc.company, "default_currency")

	logger = frappe.logger("digitax_integration", allow_site=True, file_count=10)
	# dry_run=True: this is a read-only print action. If gates fail here despite
	# custom_sent_to_digitax already being set (item master data changed since the
	# original send), fall back to the raw invoice lines below rather than writing
	# an error/Actionable Item just because someone opened Print.
	built = build_digitax_items_payload(doc, digitax_settings, logger, dry_run=True)

	if built.get("ok"):
		raw_items = built["items"]
		items = [
			{
				"description": entry.get("item_name") or entry.get("item_description") or "School Fees",
				"amount": entry.get("total_amount") or 0,
				"formatted_amount": fmt_money(entry.get("total_amount") or 0, currency=currency),
			}
			for entry in raw_items
		]
		invoice_total = sum(entry.get("total_amount") or 0 for entry in raw_items)
	else:
		# Nothing valid was built for Digitax (e.g. missing item links) — fall back to
		# the raw invoice lines so the printout isn't empty.
		items = [
			{
				"description": row.item_name or row.description or row.item_code,
				"amount": abs(row.amount or 0),
				"formatted_amount": fmt_money(abs(row.amount or 0), currency=currency),
			}
			for row in doc.items
		]
		invoice_total = abs(doc.grand_total or 0)

	company = _get_company_details(doc.company)
	company["tagline"] = digitax_settings.get("print_tagline") or ""
	company["po_box"] = digitax_settings.get("po_box") or ""

	# customer_code is an st_austins-owned custom field (Engage's own account code
	# for this customer) - titan_digitax ships no such field itself, so on a
	# generic install without st_austins the column doesn't exist in the database
	# at all. Reading it unconditionally raises OperationalError: Unknown column,
	# crashing the print button outright. The template already hides this row
	# entirely when blank ({% if ctx.customer.account_code %}), so falling back
	# to "" here is a complete fix, not a partial one.
	customer_code = (
		frappe.db.get_value("Customer", doc.customer, "customer_code")
		if frappe.get_meta("Customer").has_field("customer_code")
		else None
	) or ""
	reference_no = getattr(doc, "custom_engage_invoice_number", None) or doc.name
	digitax_details = get_active_digitax_details(doc)

	return {
		"document_title": "Credit Note" if doc.is_return else "Invoice",
		"offline_url": (digitax_details or {}).get("offline_url") or "",
		"company": company,
		"reference_no": reference_no,
		"posting_date": doc.posting_date,
		"due_date": doc.due_date,
		"customer": {
			"name": doc.customer_name or doc.customer or "",
			"account_code": customer_code,
		},
		"fee_items": items,
		"currency": currency,
		"formatted_invoice_total": fmt_money(invoice_total, currency=currency),
		"formatted_total_due": fmt_money(abs(doc.outstanding_amount or 0), currency=currency),
		"payment": {
			"bank_name": digitax_settings.get("bank_name") or "",
			"bank_account_name": digitax_settings.get("bank_account_name") or "",
			"bank_account_no": digitax_settings.get("bank_account_no") or "",
			"bank_branch": digitax_settings.get("bank_branch") or "",
			"bank_branch_code": digitax_settings.get("bank_branch_code") or "",
			"bank_swift_code": digitax_settings.get("bank_swift_code") or "",
			"mpesa_paybill": digitax_settings.get("mpesa_paybill") or "",
			"mpesa_paybill_account": digitax_settings.get("mpesa_paybill_account") or "",
		},
		"notes": digitax_settings.get("print_notes") or "",
		"term_opening_date": digitax_settings.get("term_opening_date") or "",
	}
