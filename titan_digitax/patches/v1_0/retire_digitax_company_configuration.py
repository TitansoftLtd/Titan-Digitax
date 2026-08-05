# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Retire the legacy `Digitax Company Configuration` child table (D15).

`Digitax Company Settings` (a standalone doctype, one record per company — the same
shape as `Engage Settings`) is now the sole source of per-company DigiTax
configuration, mirroring the decision already made for Engage: a dedicated doctype,
not a child table, because credentials need a stable per-company address.

The legacy table held only company/enabled/description (no credentials — that never
changed), and `seed_digitax_company_settings` already migrates its rows to the new
doctype. This patch runs a final safety-net migration by raw SQL (the child rows
survive even after the parent's Table field is removed from the DocType JSON, since
Table fields carry no column on the parent — the data lives in the child's own table
under parent/parenttype/parentfield), then deletes the doctype outright.

Idempotent: a company already present in Digitax Company Settings is left untouched,
and deleting an already-deleted DocType is a no-op.
"""

import frappe

LEGACY_DOCTYPE = "Digitax Company Configuration"
NEW_DOCTYPE = "Digitax Company Settings"


def execute():
	logger = frappe.logger("digitax_integration")

	if not frappe.db.table_exists(LEGACY_DOCTYPE):
		return

	migrated = 0
	rows = frappe.db.sql(
		f"""select company, enabled, description
		from `tab{LEGACY_DOCTYPE}`
		where parenttype = 'Digitax Settings' and ifnull(company, '') != ''""",
		as_dict=True,
	)
	for row in rows:
		if not frappe.db.exists("Company", row.company):
			logger.warning(
				f"retire_digitax_company_configuration: company {row.company!r} no longer exists; skipped"
			)
			continue
		if frappe.db.exists(NEW_DOCTYPE, row.company):
			continue

		doc = frappe.new_doc(NEW_DOCTYPE)
		doc.company = row.company
		doc.enabled = row.enabled
		doc.description = row.description
		doc.flags.ignore_permissions = True
		# base_url/api_key are mandatory now (D19), but this legacy table never held
		# credentials — retire_digitax_settings backfills base_url afterwards from the
		# old Single, and api_key must be entered per company regardless.
		doc.flags.ignore_mandatory = True
		doc.insert()
		migrated += 1

	frappe.db.commit()

	if frappe.db.exists("DocType", LEGACY_DOCTYPE):
		frappe.delete_doc("DocType", LEGACY_DOCTYPE, force=True, ignore_permissions=True)
		frappe.db.commit()

	# delete_doc("DocType", ...) removes the doctype record but does not drop the
	# child table itself, so the empty shell survives with no meta pointing at it.
	if frappe.db.table_exists(LEGACY_DOCTYPE):
		frappe.db.sql_ddl(f"drop table if exists `tab{LEGACY_DOCTYPE}`")
		frappe.db.commit()

	logger.info(
		f"retire_digitax_company_configuration: migrated={migrated}, deleted {LEGACY_DOCTYPE!r} and its table"
	)
