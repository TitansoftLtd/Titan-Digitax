# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Move existing Item DigiTax ids into per-company registrations (D18).

The legacy `Item.custom_digitax_id` holds one id, issued by whichever DigiTax account
was configured when the item was synced. Attributing it to a company is only
unambiguous when exactly one company is enabled — with several, the patch refuses
rather than guessing, because a wrong attribution means invoices filed under another
school's KRA registration.

The legacy fields are left in place; they are still mirrored during the transition.
Idempotent: an item that already has a row for the company is skipped.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Item Digitax Registration"):
		return
	if not frappe.db.has_column("Item", "custom_digitax_id"):
		return

	logger = frappe.logger("digitax_integration")

	from titan_digitax.titan_digitax.utils.company_config import get_enabled_digitax_companies

	companies = get_enabled_digitax_companies()
	if len(companies) != 1:
		logger.warning(
			f"backfill_item_digitax_registrations: {len(companies)} enabled companies "
			"— cannot attribute existing DigiTax ids unambiguously, skipping. Register "
			"items per company from the Item form instead."
		)
		return

	company = companies[0]

	items = frappe.db.sql(
		"""select name, custom_digitax_id, custom_digitax_etims_item_code,
		          custom_digitax_synced, custom_digitax_last_sync, custom_digitax_item_name
		   from `tabItem` where ifnull(custom_digitax_id, '') != ''""",
		as_dict=True,
	)
	if not items:
		logger.info("backfill_item_digitax_registrations: no items carry a DigiTax id")
		return

	from titan_digitax.titan_digitax.utils import item_registry

	created = skipped = 0
	for item in items:
		if item_registry.get_registration(item.name, company):
			skipped += 1
			continue
		item_registry.upsert_registration(
			item.name,
			company,
			digitax_item_name=item.custom_digitax_item_name,
			digitax_id=item.custom_digitax_id,
			digitax_etims_item_code=item.custom_digitax_etims_item_code,
			synced=item.custom_digitax_synced or 1,
			last_sync=item.custom_digitax_last_sync,
		)
		created += 1

	frappe.db.commit()
	logger.info(
		f"backfill_item_digitax_registrations: company={company} created={created} skipped={skipped}"
	)
