# Copyright (c) 2026, Titansoft Ltd. and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document
from frappe.utils import now_datetime


class ActionableItems(Document):
	def before_save(self):
		if self.status == "Resolved" and not self.resolved_at:
			self.resolved_at = now_datetime()
			self.resolved_by = frappe.session.user


def create_actionable_item(
	title,
	item_type,
	description=None,
	action_required=None,
	reference_doctype=None,
	reference_name=None,
	reference_number=None,
	related_data=None,
	priority="Medium",
	assigned_to=None,
	company=None,
):
	"""Create or update an open Actionable Item (dedupe by company/title/type/reference).

	Server-side only (not whitelisted): it saves with permissions bypassed, so it must
	never be reachable by an arbitrary API caller. Company is part of the dedupe key:
	two companies hitting the same external reference would otherwise collapse into one
	item and the second company's problem would be invisible.
	"""
	filters = {
		"title": title,
		"item_type": item_type,
		"status": ["in", ["Open", "In Progress"]],
	}
	if company:
		filters["company"] = company
	if reference_doctype and reference_name:
		filters["reference_doctype"] = reference_doctype
		filters["reference_name"] = reference_name

	existing_item = frappe.db.get_value("Actionable Items", filters, "name")
	if existing_item:
		doc = frappe.get_doc("Actionable Items", existing_item)
		doc.description = description or doc.description
		doc.action_required = action_required or doc.action_required
		doc.related_data = related_data or doc.related_data
		doc.priority = priority
		doc.save(ignore_permissions=True)
		return {"created": False, "updated": True, "name": existing_item}

	doc = frappe.get_doc(
		{
			"doctype": "Actionable Items",
			"title": title,
			"company": company,
			"item_type": item_type,
			"description": description,
			"action_required": action_required,
			"reference_doctype": reference_doctype,
			"reference_name": reference_name,
			"reference_number": reference_number,
			"related_data": related_data,
			"priority": priority,
			"assigned_to": assigned_to,
			"status": "Open",
		}
	)
	doc.insert(ignore_permissions=True)
	return {"created": True, "name": doc.name}
