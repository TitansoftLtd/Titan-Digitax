"""Set the DigiTax codes that never vary by company on every existing Digitax
Company Settings row.

submitted_invoice_status_code (02), cancelled_invoice_status_code (04) and
default_receipt_type_code (S) are fixed by DigiTax's own code lists. They're
read-only on the form now, and new rows get them from the field defaults - this
brings existing rows in line, including any that were changed or left blank.
The values come from the field defaults, the same source the controller re-applies
on every save. Plain SQL, no Document instantiated, per the recurring
migrate-crash rule in docs/HANDOFF.md.
"""

import frappe

DOCTYPE = "Digitax Company Settings"

# Listed here rather than imported from the controller, so this patch never breaks
# if that module is renamed or retired later (the recurring-bug rule).
FIXED_CODE_FIELDS = (
	"submitted_invoice_status_code",
	"cancelled_invoice_status_code",
	"default_receipt_type_code",
)


def execute():
	if not frappe.db.table_exists(DOCTYPE):
		return

	meta = frappe.get_meta(DOCTYPE)
	for fieldname in FIXED_CODE_FIELDS:
		field = meta.get_field(fieldname)
		if not field or not field.default:
			continue
		value = field.default
		frappe.db.sql(
			f"update `tab{DOCTYPE}` set `{fieldname}` = %s where ifnull(`{fieldname}`, '') != %s",
			(value, value),
		)
