# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Raise an Actionable Item (an alert that needs a person) from DigiTax code.

Actionable Items is this app's own doctype (D26), so every install has somewhere
visible for these alerts. This wrapper never raises: a DigiTax send, sync or
callback must not fail just because recording its alert did.
"""

from __future__ import annotations

import frappe


def create_actionable_item(**kwargs):
	"""Best-effort Actionable Item create. Never raises."""
	from titan_digitax.titan_digitax.doctype.actionable_items.actionable_items import (
		create_actionable_item as _create,
	)

	try:
		return _create(**kwargs)
	except Exception:
		frappe.logger("digitax_integration").warning(
			"Actionable Item create failed: %s", kwargs.get("title"), exc_info=True
		)
		return None
