# Copyright (c) 2025, Titansoft Limited and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class DigitaxSettings(Document):
	"""Global DigiTax defaults.

	Per-company enablement and credentials live on `Digitax Company Settings`
	(one record per company, mirroring `Engage Settings`) — see
	`titan_digitax.utils.company_config`. That doctype validates its own
	company/country/group-company eligibility on save; this Single no longer
	carries a company table.
	"""

	pass
