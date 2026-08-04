# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Retire the global `Digitax Settings` Single (D19-equivalent for Titan Digitax).

Every DigiTax setting now lives on `Digitax Company Settings`, one record per
company — the same shape already used for Engage on the school side. There is no
shared/global tier left: base_url, api_key, timeouts, item defaults, and invoice
status codes are all per-company fields now, even where that means the same value
is repeated across companies.

This patch backfills any blank field on an existing Digitax Company Settings row
from the (about-to-be-deleted) Single's values, so a site that relied on the old
global fallback (blank company base_url/api_key, or shared timeouts/item defaults)
keeps working after the Single is gone. It does not create new company rows —
only companies that already opted in by having a Digitax Company Settings record
get backfilled.

Idempotent: re-running after the old DocType is already deleted is a no-op.
"""

import frappe
from frappe.utils.password import get_decrypted_password

OLD_DOCTYPE = "Digitax Settings"
NEW_DOCTYPE = "Digitax Company Settings"

# Fields carried over 1:1 from the old Single when the company row leaves them blank.
_PLAIN_FIELDS = [
	"target_country",
	"api_request_timeout",
	"max_retry_attempts",
	"background_job_timeout",
	"require_digitax_item_name",
	"require_manual_item_sync",
	"auto_sync_items_to_digitax",
	"virtual_amendment_role",
	"submitted_invoice_status_code",
	"default_receipt_type_code",
	"cancelled_invoice_status_code",
	"default_payment_type_code",
	"default_item_class_code",
	"default_item_type_code",
	"default_item_tax_type_code",
	"default_origin_nation_code",
	"default_package_unit_code",
	"default_quantity_unit_code",
	"default_item_bar_code",
	"default_item_name",
	"default_item_description",
	"default_is_stockable",
]


def execute():
	logger = frappe.logger("digitax_integration")

	if not frappe.db.exists("DocType", OLD_DOCTYPE):
		return

	old_values = frappe.db.get_singles_dict(OLD_DOCTYPE) or {}
	old_api_key = get_decrypted_password(OLD_DOCTYPE, OLD_DOCTYPE, "api_key", raise_exception=False)

	backfilled = 0
	for company in frappe.get_all(NEW_DOCTYPE, pluck="name"):
		doc = frappe.get_doc(NEW_DOCTYPE, company)
		changed = False

		if not (doc.get("base_url") or "").strip() and old_values.get("base_url"):
			doc.base_url = old_values.get("base_url")
			changed = True

		if not doc.get("api_key") and old_api_key:
			doc.api_key = old_api_key
			changed = True

		for field in _PLAIN_FIELDS:
			if doc.get(field) in (None, "") and old_values.get(field) not in (None, ""):
				doc.set(field, old_values.get(field))
				changed = True

		if changed:
			doc.flags.ignore_permissions = True
			doc.flags.ignore_mandatory = True
			doc.save()
			backfilled += 1

	frappe.db.commit()

	frappe.delete_doc("DocType", OLD_DOCTYPE, force=True, ignore_permissions=True)
	frappe.db.commit()

	# delete_doc() removes the DocType record but not its Singles values or the
	# encrypted password's __Auth row, so nothing orphaned lingers once it's gone.
	frappe.db.delete("Singles", {"doctype": OLD_DOCTYPE})
	frappe.db.delete("__Auth", {"doctype": OLD_DOCTYPE})
	frappe.db.commit()

	logger.info(
		f"retire_digitax_settings: backfilled {backfilled} {NEW_DOCTYPE!r} row(s), deleted {OLD_DOCTYPE!r}"
	)
