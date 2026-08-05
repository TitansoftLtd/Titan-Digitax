# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Create per-company Digitax Item records from the legacy custom_* fields on Item,
and link every legacy Item to the right one, before those fields are deleted by
retire_item_digitax_custom_fields.py.

Reads legacy data via raw SQL, not frappe.get_doc()/frappe.get_cached_doc(), because
retire_item_digitax_custom_fields.py (later in this same release) deletes the Item
Custom Field rows outright — a site that reaches this patch for the first time after
that later commit already exists on disk must not try to read/write DocFields that no
longer exist as real doctype fields. Same failure mode fixed in
seed_digitax_company_settings.py for Digitax Settings' controller module being
deleted before a lagging site's migrate reached the patch that read it — here it's
Custom Field rows rather than a controller module, but the raw-SQL fix is the same
idiom.

Non-breaking / idempotent: no-ops cleanly on a fresh install (Digitax Item doctype
missing, or no legacy Item columns/data at all). Re-running after Digitax Item rows
already exist for a given (company, item_name) just skips them.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Digitax Item"):
		return
	if not frappe.db.has_column("Item", "custom_digitax_item_name"):
		# Legacy fields already gone (fresh install, or already migrated+cleaned).
		return

	logger = frappe.logger("digitax_integration")

	legacy_rows = frappe.db.sql(
		"""
		select name as item_code,
		       custom_digitax_item_name, custom_digitax_id, custom_digitax_synced,
		       custom_digitax_etims_item_code, custom_digitax_last_sync,
		       custom_item_class_code, custom_tax_type_code,
		       custom_digitax_item_type_code, custom_digitax_origin_nation_code,
		       custom_digitax_package_unit_code, custom_digitax_quantity_unit_code
		from `tabItem`
		where ifnull(custom_digitax_item_name, '') != ''
		   or ifnull(custom_digitax_id, '') != ''
		""",
		as_dict=True,
	)
	if not legacy_rows:
		logger.info("migrate_item_digitax_fields_to_digitax_item: no legacy DigiTax data on Item, no-op")
		return

	has_registration_table = frappe.db.table_exists("Item Digitax Registration")

	created = linked = skipped = 0

	for row in legacy_rows:
		display_name = (row.custom_digitax_item_name or "").strip()
		if not display_name:
			continue

		# Determine which company/companies this legacy item's registration applies to.
		companies = []
		if has_registration_table:
			companies = frappe.db.sql(
				"""select company, digitax_id, digitax_etims_item_code, synced, last_sync
				   from `tabItem Digitax Registration`
				   where parent=%s and parenttype='Item'""",
				(row.item_code,), as_dict=True,
			)

		if not companies:
			# No per-company registration rows exist for this item (confirmed empty on
			# both live companies at the time this was written). Fall back to: exactly
			# one enabled company means unambiguous attribution.
			from titan_digitax.titan_digitax.utils.company_config import get_enabled_digitax_companies

			enabled = get_enabled_digitax_companies()
			if len(enabled) != 1:
				logger.warning(
					f"migrate_item_digitax_fields_to_digitax_item: {row.item_code} has no "
					f"per-company registration and {len(enabled)} companies are enabled — "
					"cannot attribute unambiguously, skipping. Link it manually via the Item form."
				)
				skipped += 1
				continue
			companies = [frappe._dict(
				company=enabled[0],
				digitax_id=row.custom_digitax_id,
				digitax_etims_item_code=row.custom_digitax_etims_item_code,
				synced=row.custom_digitax_synced,
				last_sync=row.custom_digitax_last_sync,
			)]

		linked_this_row = None
		for reg in companies:
			company = reg.company
			if not company or not frappe.db.exists("Company", company):
				continue
			abbr = frappe.get_cached_value("Company", company, "abbr")
			digitax_item_name = f"{display_name}({abbr})"

			if not frappe.db.exists("Digitax Item", digitax_item_name):
				di = frappe.new_doc("Digitax Item")
				di.item_name = display_name
				di.company = company
				di.item_class_code = row.custom_item_class_code or "99020000"
				di.item_type_code = row.custom_digitax_item_type_code or "3"
				di.tax_type_code = row.custom_tax_type_code or "D"
				di.origin_nation_code = row.custom_digitax_origin_nation_code or "KE"
				di.package_unit_code = row.custom_digitax_package_unit_code or "NT"
				di.quantity_unit_code = row.custom_digitax_quantity_unit_code or "U"
				# No legacy source stored a per-company default price (it was derived
				# from this Item's own Item Price at sync time, not stored). Best
				# effort: reuse this item's own Standard Selling rate; else 0 — a 0
				# doesn't block this migration (ignore_mandatory) but does block a
				# future re-sync until an accountant fills in the real value.
				price = frappe.db.get_value(
					"Item Price",
					{"item_code": row.item_code, "price_list": "Standard Selling"},
					"price_list_rate",
				)
				di.default_unit_price = price or 0
				di.digitax_id = reg.digitax_id
				di.etims_item_code = reg.digitax_etims_item_code
				di.synced = 1 if reg.digitax_id else 0
				di.last_sync = reg.last_sync
				di.enabled = 1
				di.flags.ignore_permissions = True
				di.flags.ignore_mandatory = True
				di.insert()
				created += 1
				digitax_item_name = di.name

			# An Item registered against >1 company (possible via the old per-company
			# child table, though confirmed empty on both live sites at the time this
			# was written) can only keep ONE company's link — custom_digitax_item is a
			# single Link. The last company processed wins; loudly logged so it's
			# never a silent surprise.
			if linked_this_row and linked_this_row != digitax_item_name:
				logger.warning(
					f"migrate_item_digitax_fields_to_digitax_item: {row.item_code} has "
					f"registrations for multiple companies; only linking to "
					f"{digitax_item_name} (was {linked_this_row}). Link the others "
					"manually via the Item form if needed."
				)
			linked_this_row = digitax_item_name

		if linked_this_row:
			frappe.db.set_value(
				"Item", row.item_code, "custom_digitax_item", linked_this_row, update_modified=False
			)
			linked += 1

	frappe.db.commit()
	logger.info(
		f"migrate_item_digitax_fields_to_digitax_item: created={created} digitax_item(s), "
		f"linked={linked} item(s), skipped={skipped}"
	)
