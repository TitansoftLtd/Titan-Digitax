# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Create Actionable Items without hard-coding a school app import."""

from __future__ import annotations

import frappe


_ACTIONABLE_IMPORT_PATHS = (
	"st_austins.st_austins.doctype.actionable_items.actionable_items.create_actionable_item",
	"rusinga_school.rusinga_school.doctype.actionable_items.actionable_items.create_actionable_item",
)


def create_actionable_item(**kwargs):
	"""
	Best-effort Actionable Item create.

	Prefer st_austins; fall back to rusinga_school if installed; otherwise log.
	"""
	for path in _ACTIONABLE_IMPORT_PATHS:
		try:
			fn = frappe.get_attr(path)
			return fn(**kwargs)
		except Exception:
			continue

	frappe.logger("digitax_integration").warning(
		"Actionable Items module not available; skipped create: %s",
		kwargs.get("title"),
	)
	return None
