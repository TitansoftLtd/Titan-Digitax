# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Per-(Item, Company) DigiTax registration (D18).

An Item is a global ERPNext master, but registering it with DigiTax is a per-tenant
act: the same fee item gets a different catalogue id in each company's DigiTax
account. `Item.custom_digitax_id` holds only one value, so with two companies the
second one would file its invoices under the first one's catalogue entry.

Registrations live in the `custom_digitax_registrations` child table. The legacy
Item fields are still written during the transition so a rollback stays possible;
readers go through this module.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

CHILD_DOCTYPE = "Item Digitax Registration"
CHILD_FIELD = "custom_digitax_registrations"


def get_registration(item_code: str, company: str) -> frappe._dict | None:
	"""Return this company's registration row for an item, or None."""
	if not item_code or not company:
		return None

	rows = frappe.get_all(
		CHILD_DOCTYPE,
		filters={"parent": item_code, "parenttype": "Item", "company": company},
		fields=[
			"name",
			"company",
			"digitax_item_name",
			"digitax_id",
			"digitax_etims_item_code",
			"synced",
			"last_sync",
			"digitax_status",
		],
		limit=1,
	)
	return frappe._dict(rows[0]) if rows else None


def get_digitax_id(item_code: str, company: str) -> str | None:
	"""This company's DigiTax catalogue id for an item.

	Falls back to the legacy Item field while registrations are still being
	backfilled, so a partially migrated site keeps sending.
	"""
	reg = get_registration(item_code, company)
	if reg and (reg.digitax_id or "").strip():
		return reg.digitax_id

	legacy = frappe.db.get_value("Item", item_code, "custom_digitax_id")
	return legacy or None


def get_display_name(item_code: str, company: str | None = None, digitax_settings=None) -> str | None:
	"""Name sent to DigiTax for an item, honouring D14's require_digitax_item_name.

	Resolution order: this company's registration override, then the Item's own
	Digitax Item Name, then (only when the name is not required) item_name/item_code.
	"""
	if not item_code:
		return None

	settings = digitax_settings or frappe.get_cached_doc("Digitax Settings")
	require_name = bool(settings.get("require_digitax_item_name"))

	if company:
		reg = get_registration(item_code, company)
		if reg and (reg.digitax_item_name or "").strip():
			return reg.digitax_item_name.strip()

	item = frappe.db.get_value(
		"Item", item_code, ["custom_digitax_item_name", "item_name"], as_dict=True
	)
	if not item:
		return None

	own = (item.custom_digitax_item_name or "").strip()
	if own:
		return own

	if require_name:
		# The caller must block the send rather than silently sending an ERP name.
		return None

	return (item.item_name or "").strip() or item_code


def upsert_registration(item_code: str, company: str, **values) -> None:
	"""Create or update this company's registration row for an item."""
	if not item_code or not company:
		return

	item = frappe.get_doc("Item", item_code)
	row = next(
		(r for r in (item.get(CHILD_FIELD) or []) if r.company == company),
		None,
	)
	if row is None:
		row = item.append(CHILD_FIELD, {"company": company})

	for field, value in values.items():
		row.set(field, value)
	if "last_sync" not in values:
		row.last_sync = now_datetime()

	item.flags.ignore_permissions = True
	item.flags.ignore_validate_update_after_submit = True
	item.save()


def find_item_with_digitax_id(company: str, display_name: str) -> frappe._dict | None:
	"""Find another item already registered under the SAME display name for THIS company.

	The company predicate is the entire point. The previous implementations matched on
	display name alone, so one company's DigiTax id was copied onto another company's
	item — and every later invoice for that item was filed under the wrong KRA
	registration.
	"""
	if not company or not display_name:
		return None

	rows = frappe.db.sql(
		"""
		select r.parent as item_code, r.digitax_id, r.digitax_etims_item_code
		from `tabItem Digitax Registration` r
		inner join `tabItem` i on i.name = r.parent
		where r.parenttype = 'Item'
		  and r.company = %(company)s
		  and ifnull(r.digitax_id, '') != ''
		  and coalesce(
		        nullif(trim(r.digitax_item_name), ''),
		        nullif(trim(i.custom_digitax_item_name), ''),
		        i.item_name
		      ) = %(display_name)s
		limit 1
		""",
		{"company": company, "display_name": display_name},
		as_dict=True,
	)
	return frappe._dict(rows[0]) if rows else None


def validate_item_registrations(doc, method=None):
	"""Item doc_event: at most one registration per company, and no two items sharing
	a DigiTax id within one company."""
	seen = set()
	for row in doc.get(CHILD_FIELD) or []:
		if not row.company:
			frappe.throw(frappe._("Digitax registration rows must name a Company."))

		if row.company in seen:
			frappe.throw(
				frappe._("Item {0} has more than one Digitax registration for {1}.").format(
					frappe.bold(doc.name), frappe.bold(row.company)
				)
			)
		seen.add(row.company)

		if not (row.digitax_id or "").strip():
			continue

		clash = frappe.db.sql(
			"""select r.parent from `tabItem Digitax Registration` r
			   where r.parenttype='Item' and r.parent != %(item)s
			     and r.company = %(company)s and r.digitax_id = %(digitax_id)s
			   limit 1""",
			{"item": doc.name, "company": row.company, "digitax_id": row.digitax_id},
		)
		if clash:
			frappe.throw(
				frappe._("Digitax ID {0} is already registered to Item {1} for {2}.").format(
					frappe.bold(row.digitax_id), frappe.bold(clash[0][0]), frappe.bold(row.company)
				)
			)
