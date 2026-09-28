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

	def before_validate(self):
		# Runs before Frappe's mandatory-field check, so a company default can
		# satisfy a required code - and with no default set, the user must enter it.
		for field, value in get_company_item_defaults(self.company).items():
			if not (self.get(field) or "").strip():
				self.set(field, value)


# Digitax Company Settings default -> the Digitax Item field it fills. The defaults
# are optional and blank unless a company sets them (D23): the codes always end up
# stored on the item itself, never applied behind the scenes at send time.
COMPANY_DEFAULT_FIELDS = {
	"default_item_class_code": "item_class_code",
	"default_item_type_code": "item_type_code",
	"default_item_tax_type_code": "tax_type_code",
	"default_origin_nation_code": "origin_nation_code",
	"default_package_unit_code": "package_unit_code",
	"default_quantity_unit_code": "quantity_unit_code",
	"default_item_bar_code": "item_bar_code",
}


@frappe.whitelist()
def get_company_item_defaults_for_form(company):
	"""The form fills a new Digitax Item's empty fields from these when a company is
	picked - the browser checks required fields before the server sees the save, so
	before_validate alone would never get the chance."""
	frappe.has_permission("Digitax Item", "create", throw=True)
	return get_company_item_defaults(company)


def get_company_item_defaults(company):
	"""{Digitax Item field: value} for every default this company has set."""
	if not company or not frappe.db.exists("Digitax Company Settings", company):
		return {}
	values = frappe.db.get_value("Digitax Company Settings", company, list(COMPANY_DEFAULT_FIELDS), as_dict=True)
	return {
		item_field: values[default_field].strip()
		for default_field, item_field in COMPANY_DEFAULT_FIELDS.items()
		if (values.get(default_field) or "").strip()
	}
