# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Digitax Item <-> DigiTax catalogue reconciliation (manual sync).

Replaces the retired company-scoped `Item Digitax Registration` child table
and the string-matching `item_registry` module with a first-class per-company
`Digitax Item` record. See digitax_item.py for the identity model
("{item_name}(company_abbr)").

Option B (confirmed): DigiTax's API only lets us update item_name, tax_type_code
and default_unit_price after creation — item_class_code, item_type_code,
origin_nation_code, package_unit_code and quantity_unit_code cannot be changed
via the API once an item exists. Rather than silently accept that split, this
module writes NOTHING back to DigiTax, ever, for any field. On any mismatch
between the local Digitax Item and what DigiTax already has under that
item_name, sync BLOCKS and raises an Actionable Item; a human resolves it by
hand (either edit DigiTax directly, or edit the local Digitax Item to match)
and re-runs the sync. A future phase may choose to auto-reconcile the three
updatable fields via PUT — if so, add DigitaxClient.update_item(); it does not
exist today because nothing calls it.
"""

from __future__ import annotations

import frappe
from frappe import _
from frappe.utils import now_datetime

from titan_digitax.titan_digitax.utils.actionable import create_actionable_item
from titan_digitax.titan_digitax.utils.digitax_client import DigitaxClient

# (local Digitax Item fieldname, DigiTax API fieldname) — compared field-for-field
# against whatever DigiTax already has when an item_name match is found.
_COMPARE_FIELDS = [
	("item_name", "item_name"),
	("tax_type_code", "tax_type_code"),
	("default_unit_price", "default_unit_price"),
	("item_class_code", "item_class_code"),
	("item_type_code", "item_type_code"),
	("origin_nation_code", "origin_nation_code"),
	("package_unit_code", "package_unit_code"),
	("quantity_unit_code", "quantity_unit_code"),
]


def is_item_usable(digitax_item_name):
	"""True only when both the Digitax Item and its company are enabled."""
	from titan_digitax.titan_digitax.utils.company_config import is_digitax_enabled_for_company

	if not digitax_item_name:
		return False

	row = frappe.db.get_value(
		"Digitax Item", digitax_item_name, ["company", "enabled"], as_dict=True
	)
	if not row:
		return False
	return bool(row.enabled) and is_digitax_enabled_for_company(row.company)


def get_digitax_item_for_invoice_item(item_code, company):
	"""Resolve the Digitax Item linked from an ERP Item, for use while building a
	Sales Invoice's DigiTax payload.

	Item has no `company` field (it's a global master), so nothing stops someone
	from linking an Item to another company's Digitax Item by mistake on the Item
	form. This is the actual safety net: a linked Digitax Item belonging to a
	different company than the invoice being sent is treated exactly like no
	link at all, rather than ever being sent under the wrong company's
	credentials.

	Returns the Digitax Item name (docname), or None if there's no usable link.
	"""
	if not item_code or not company:
		return None

	link = frappe.db.get_value("Item", item_code, "custom_digitax_item")
	if not link:
		return None

	digitax_item_company = frappe.db.get_value("Digitax Item", link, "company")
	if digitax_item_company != company:
		if digitax_item_company:
			frappe.logger("digitax_integration").warning(
				f"Item {item_code} links to Digitax Item {link} (company "
				f"{digitax_item_company}), but this send is for {company} — "
				"treating as unlinked."
			)
		return None

	if not is_item_usable(link):
		return None

	return link


@frappe.whitelist()
def sync_digitax_item(digitax_item_name):
	"""Interactive entrypoint (the "Sync to Digitax" button on the Digitax Item /
	Item forms). The initiating user must hold this company's configured
	item_sync_role; once confirmed, the actual sync always runs as the
	company's Digitax Sync User (see company_config.run_as_digitax_sync_user)
	so every Digitax write is consistently attributed to that one account.
	"""
	from titan_digitax.titan_digitax.utils.company_config import require_digitax_role, run_as_digitax_sync_user

	company = frappe.db.get_value("Digitax Item", digitax_item_name, "company")
	if not company:
		frappe.throw(_("Digitax Item {0} not found.").format(digitax_item_name))
	require_digitax_role(company, "item_sync_role", "sync a Digitax Item")
	return run_as_digitax_sync_user(company, lambda: _sync_digitax_item_impl(digitax_item_name))


def sync_digitax_item_automatic(digitax_item_name):
	"""Automatic/background entrypoint - st_austins's auto-sync-on-item-create
	(gated by that company's own auto_sync_items_to_digitax toggle) calls this
	directly, never the whitelisted wrapper above. Not an arbitrary user action,
	so no role is checked, but the sync still always runs as the company's
	Digitax Sync User, same as the manual path.
	"""
	from titan_digitax.titan_digitax.utils.company_config import run_as_digitax_sync_user

	company = frappe.db.get_value("Digitax Item", digitax_item_name, "company")
	if not company:
		frappe.throw(_("Digitax Item {0} not found.").format(digitax_item_name))
	return run_as_digitax_sync_user(company, lambda: _sync_digitax_item_impl(digitax_item_name))


def _sync_digitax_item_impl(digitax_item_name):
	"""Manual "Sync to Digitax" action for one Digitax Item record.

	Algorithm:
	  1. Paginate GET /items for this company, look for an exact item_name match.
	  2. Not found -> POST /items to create; store id/etims_item_code, synced=1.
	  3. Found -> compare every catalogue attribute. Any mismatch blocks the sync
	     (Actionable Item, no writes anywhere). Full match -> link, synced=1.
	"""
	from titan_digitax.titan_digitax.utils.company_config import (
		get_digitax_settings,
		is_digitax_enabled_for_company,
	)

	doc = frappe.get_doc("Digitax Item", digitax_item_name)

	if not is_digitax_enabled_for_company(doc.company):
		frappe.throw(_("Digitax integration is not enabled for {0}.").format(doc.company))
	if not doc.enabled:
		frappe.throw(_("{0} is disabled (Digitax Item.enabled is off).").format(doc.name))

	# Ensures this company actually has usable credentials before we call out.
	get_digitax_settings(doc.company)

	missing = [
		field for field, _remote in _COMPARE_FIELDS
		if field != "default_unit_price" and not doc.get(field)
	]
	if not doc.default_unit_price or doc.default_unit_price <= 0:
		missing.append("default_unit_price")
	if missing:
		frappe.throw(
			_("Missing required fields on {0}: {1}").format(doc.name, ", ".join(missing))
		)

	client = DigitaxClient(doc.company)
	found = client.find_item_by_name(doc.item_name)

	if not found:
		payload = {
			"item_class_code": doc.item_class_code,
			"item_type_code": doc.item_type_code,
			"item_name": doc.item_name,
			"origin_nation_code": doc.origin_nation_code,
			"package_unit_code": doc.package_unit_code,
			"quantity_unit_code": doc.quantity_unit_code,
			"tax_type_code": doc.tax_type_code,
			"default_unit_price": float(doc.default_unit_price),
		}
		response = client.create_item(payload)
		if response.get("http_status_code") != 201:
			frappe.throw(
				_("Failed to create item in Digitax. HTTP {0} returned.").format(
					response.get("http_status_code")
				)
			)

		doc.digitax_id = response.get("id")
		doc.etims_item_code = response.get("etims_item_code")
		doc.synced = 1
		doc.last_sync = now_datetime()
		doc.flags.ignore_permissions = True
		doc.save()
		return {"status": "created", "digitax_id": doc.digitax_id}

	mismatches = _find_mismatches(doc, found)

	if mismatches:
		_raise_mismatch_actionable_item(doc, found, mismatches)
		frappe.throw(
			_(
				"{0} field(s) disagree with DigiTax's existing '{1}' catalogue entry. "
				"An actionable item has been raised — resolve it, then retry."
			).format(len(mismatches), doc.item_name)
		)

	doc.digitax_id = found.get("id")
	doc.etims_item_code = found.get("etims_item_code")
	doc.synced = 1
	doc.last_sync = now_datetime()
	doc.flags.ignore_permissions = True
	doc.save()
	return {"status": "linked", "digitax_id": doc.digitax_id}


def _find_mismatches(doc, remote):
	mismatches = []
	for local_field, remote_field in _COMPARE_FIELDS:
		local_val = doc.get(local_field)
		remote_val = remote.get(remote_field)
		if local_field == "default_unit_price":
			if round(float(local_val or 0), 2) != round(float(remote_val or 0), 2):
				mismatches.append((local_field, local_val, remote_val))
		elif str(local_val or "").strip() != str(remote_val or "").strip():
			mismatches.append((local_field, local_val, remote_val))
	return mismatches


def _raise_mismatch_actionable_item(doc, remote, mismatches):
	rows = "".join(
		f"<tr><td>{field}</td><td>{local_val}</td><td>{remote_val}</td></tr>"
		for field, local_val, remote_val in mismatches
	)
	create_actionable_item(
		title=f"Digitax Item Mismatch: {doc.name}",
		item_type="Data Sync Issue",
		description=(
			f"<p>Digitax Item <strong>{doc.name}</strong> matches an existing DigiTax "
			f"catalogue entry by name (id {remote.get('id')}), but the following fields "
			f"disagree. Sync is blocked until this is resolved by hand — either edit "
			f"DigiTax's catalogue entry directly, or edit this Digitax Item to match.</p>"
			f"<table><tr><th>Field</th><th>Local</th><th>DigiTax</th></tr>{rows}</table>"
		),
		action_required=(
			"Edit DigiTax's catalogue entry directly, or edit this Digitax Item to match, "
			"then re-run Sync to Digitax."
		),
		reference_doctype="Digitax Item",
		reference_name=doc.name,
		related_data=frappe.as_json({"mismatches": mismatches, "digitax_id": remote.get("id")}),
		priority="Medium",
		company=doc.company,
	)
