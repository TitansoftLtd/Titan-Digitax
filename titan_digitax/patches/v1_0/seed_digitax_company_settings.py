# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Create a Digitax Company Settings record per enabled company (D15/D19).

Seeded from the legacy `Digitax Settings.company_configurations` child table so
per-company enablement carries over unchanged. The API key is deliberately left
**blank** and strict mode is seeded **off**, so every company keeps working on the
global key until ops fills in its own — flipping strict mode on before the keys exist
would block all sends.

Idempotent: existing records are left untouched.
"""

import frappe


def execute():
	if not frappe.db.exists("DocType", "Digitax Company Settings"):
		return

	logger = frappe.logger("digitax_integration")
	settings = frappe.get_single("Digitax Settings")

	# Never flip strict mode on automatically — no company has its own key yet.
	if settings.get("strict_per_company_credentials") is None:
		frappe.db.set_single_value("Digitax Settings", "strict_per_company_credentials", 0)

	global_base_url = (settings.get("base_url") or "").strip()
	created = skipped = 0

	for row in settings.get("company_configurations") or []:
		if not row.company:
			continue
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
		doc.insert()
		created += 1
		logger.warning(
			f"seed_digitax_company_settings: created {row.company!r} WITHOUT an API key — "
			"set it before enabling Strict Per-Company Credentials"
		)

	frappe.db.commit()
	logger.info(f"seed_digitax_company_settings: created={created} skipped={skipped}")
