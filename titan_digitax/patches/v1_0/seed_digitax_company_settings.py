# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Create a Digitax Company Settings record per enabled company (D15/D19).

Seeded from the legacy `Digitax Settings.company_configurations` child table so
per-company enablement carries over unchanged. The API key is deliberately left
**blank** and strict mode is seeded **off**, so every company keeps working on the
global key until ops fills in its own — flipping strict mode on before the keys exist
would block all sends.

Reads the old Single via raw SQL (frappe.db.get_singles_dict / a direct query on the
child table) rather than frappe.get_single(): the `Digitax Settings` doctype's
controller module was deleted outright by a later patch (retire_digitax_settings), so
on any site that reaches this patch for the first time after that deletion,
frappe.get_single() would try to import a module that no longer exists on disk and
crash the migrate. Raw SQL only needs the DocType's row/table to still exist, which
retire_digitax_company_configuration and retire_digitax_settings (both later in
patches.txt) are what eventually clean up.

Idempotent: existing records are left untouched.
"""

import frappe

CHILD_DOCTYPE = "Digitax Company Configuration"


def execute():
	if not frappe.db.exists("DocType", "Digitax Company Settings"):
		return
	if not frappe.db.exists("DocType", "Digitax Settings") or not frappe.db.table_exists(CHILD_DOCTYPE):
		# Nothing to seed from — either already retired on this site, or never existed.
		return

	logger = frappe.logger("digitax_integration")
	old_values = frappe.db.get_singles_dict("Digitax Settings") or {}

	# Never flip strict mode on automatically — no company has its own key yet.
	if old_values.get("strict_per_company_credentials") in (None, ""):
		frappe.db.set_single_value("Digitax Settings", "strict_per_company_credentials", 0)

	global_base_url = (old_values.get("base_url") or "").strip()
	created = skipped = 0

	rows = frappe.db.sql(
		f"""select company, enabled, description
		from `tab{CHILD_DOCTYPE}`
		where parenttype = 'Digitax Settings' and ifnull(company, '') != ''""",
		as_dict=True,
	)

	for row in rows:
		if frappe.db.exists("Digitax Company Settings", row.company):
			skipped += 1
			continue
		if not frappe.db.exists("Company", row.company):
			logger.warning(f"seed_digitax_company_settings: company {row.company!r} missing; skipped")
			continue

		doc = frappe.new_doc("Digitax Company Settings")
		doc.company = row.company
		doc.enabled = row.enabled
		doc.base_url = global_base_url or None
		doc.description = row.description
		# api_key intentionally blank — it must be entered per company.
		doc.flags.ignore_permissions = True
		doc.flags.ignore_mandatory = True
		doc.insert()
		created += 1
		logger.warning(
			f"seed_digitax_company_settings: created {row.company!r} WITHOUT an API key — "
			"set it before enabling Strict Per-Company Credentials"
		)

	frappe.db.commit()
	logger.info(f"seed_digitax_company_settings: created={created} skipped={skipped}")
