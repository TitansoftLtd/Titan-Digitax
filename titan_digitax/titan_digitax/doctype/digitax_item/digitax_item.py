# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document


class DigitaxItem(Document):
	"""One row per (company, catalogue item). Docname is
	"{item_name}({company_abbr})" — item_name is the literal string sent to
	DigiTax and is deliberately NOT unique across companies; two companies can
	each have their own row titled "Consulting Fees".
	"""

	def autoname(self):
		if not self.company:
			frappe.throw(frappe._("Company is required to name a Digitax Item."))
		if not self.item_name:
			frappe.throw(frappe._("Item Name is required to name a Digitax Item."))

		abbr = frappe.get_cached_value("Company", self.company, "abbr")
		if not abbr:
			frappe.throw(frappe._("Company {0} has no abbreviation set.").format(self.company))

		self.name = f"{self.item_name.strip()}({abbr})"
