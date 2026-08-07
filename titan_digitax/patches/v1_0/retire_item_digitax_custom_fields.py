# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Delete the legacy Item custom_digitax_* / custom_item_class_code /
custom_tax_type_code Custom Field rows, once migrate_item_digitax_fields_to_digitax_item
has moved anything worth keeping onto Digitax Item.

Must run strictly after migrate_item_digitax_fields_to_digitax_item in patches.txt —
and REFUSES to run if that migration left any Item with legacy DigiTax data still
unlinked (e.g. because attribution was ambiguous: more than one company enabled, no
per-company registration data to disambiguate). Deleting the fields regardless of
whether migration actually succeeded would silently strand that data — visible
nowhere, on no doctype, recoverable only by hand-restoring Custom Field rows. Ops
must resolve the ambiguity (temporarily disable all but one company, or manually
create the right Digitax Item(s) and link affected Items) and re-run migrate before
this patch will proceed.

frappe.delete_doc("Custom Field", ...) drops the DocField definition AND the
underlying DB column together — this is the one genuinely destructive, irreversible
step in the whole migration, gated entirely on the earlier patch having already
extracted everything worth keeping.

Idempotent: deleting an already-deleted Custom Field / DocType is a no-op.
"""

import frappe

_RETIRED_ITEM_FIELDS = [
	"custom_digitax_section",
	"custom_digitax_item_name",
	"custom_digitax_id",
	"custom_digitax_synced",
	"custom_digitax_etims_item_code",
	"custom_digitax_last_sync",
	"custom_digitax_registrations",
	"custom_digitax_column_break",
	"custom_item_class_code",
	"custom_tax_type_code",
	"custom_digitax_item_type_code",
	"custom_digitax_origin_nation_code",
	"custom_digitax_package_unit_code",
	"custom_digitax_quantity_unit_code",
	"custom_digitax_active",
	"custom_digitax_status",
]


def execute():
	logger = frappe.logger("digitax_integration")

	if frappe.db.has_column("Item", "custom_digitax_item_name") and frappe.db.has_column(
		"Item", "custom_digitax_item"
	):
		unresolved = frappe.db.sql(
			"""select count(*) from `tabItem`
			   where ifnull(custom_digitax_item_name, '') != ''
			     and ifnull(custom_digitax_item, '') = ''""",
		)[0][0]
		if unresolved:
			logger.warning(
				f"retire_item_digitax_custom_fields: {unresolved} Item(s) still have unmigrated "
				"legacy Digitax data (migrate_item_digitax_fields_to_digitax_item could not "
				"attribute them unambiguously). Refusing to delete the legacy fields — resolve "
				"the ambiguity (e.g. temporarily disable all but one enabled company, or link "
				"affected Items to a Digitax Item by hand) and re-run bench migrate."
			)
			return

	deleted = 0

	for fieldname in _RETIRED_ITEM_FIELDS:
		name = f"Item-{fieldname}"
		if frappe.db.exists("Custom Field", name):
			frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
			deleted += 1

	frappe.db.commit()

	if frappe.db.exists("DocType", "Item Digitax Registration"):
		frappe.delete_doc("DocType", "Item Digitax Registration", ignore_permissions=True, force=True)
		frappe.db.commit()

	# delete_doc("DocType", ...) removes the doctype record but does not drop the
	# child table itself, so the empty shell survives with no meta pointing at it.
	if frappe.db.table_exists("Item Digitax Registration"):
		frappe.db.sql_ddl("drop table if exists `tabItem Digitax Registration`")
		frappe.db.commit()

	logger.info(f"retire_item_digitax_custom_fields: deleted {deleted} legacy Item Custom Field(s)")
