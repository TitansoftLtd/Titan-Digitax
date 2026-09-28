"""Give every existing Digitax Item the barcode / is_stockable its company was
actually sending (D23).

Both used to be applied at send time from company-wide settings on Digitax Company
Settings; they are stored per Digitax Item now. default_item_bar_code is still a
field (copied onto items saved without one); default_is_stockable was dropped, and
Frappe leaves its column in place. Read with raw SQL either way - never a Document,
per the recurring migrate-crash rule in docs/HANDOFF.md.

Only fills a blank item_bar_code. A company with no default leaves its items blank,
and sends using them are blocked until someone sets one - there is no hard-coded
fallback.
"""

import frappe

SETTINGS_TABLE = "Digitax Company Settings"


def execute():
	if not frappe.db.has_column(SETTINGS_TABLE, "default_item_bar_code"):
		return

	has_stockable = frappe.db.has_column(SETTINGS_TABLE, "default_is_stockable")
	stockable_column = "default_is_stockable" if has_stockable else "0"
	defaults = frappe.db.sql(
		f"select company, default_item_bar_code, {stockable_column} as default_is_stockable "
		f"from `tab{SETTINGS_TABLE}`",
		as_dict=True,
	)

	for row in defaults:
		bar_code = (row.default_item_bar_code or "").strip()
		if not bar_code:
			continue
		frappe.db.sql(
			"""update `tabDigitax Item`
			set item_bar_code = %s, is_stockable = %s
			where company = %s and ifnull(item_bar_code, '') = ''""",
			(bar_code, 1 if row.default_is_stockable else 0, row.company),
		)
