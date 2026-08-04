# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class ItemDigitaxRegistration(Document):
	"""One row per (Item, Company): a company's registration of an item in its own
	DigiTax catalogue. Uniqueness per company is enforced in the Item validate hook —
	a child table cannot carry a DB-level composite unique."""

	pass
