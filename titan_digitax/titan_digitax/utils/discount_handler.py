# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Resolve a school app's discount-redistribution logic without hard-coding an import.

Same shape as actionable.py's Actionable Item resolver - `titan_digitax` is shared
across clients, so it must not assume any particular school/MIS's data model. A
negative-amount Sales Invoice line ("this line is actually a discount") is not
something standard ERPNext discount entry (Discount %/Discount Amount) ever
produces on its own - it only shows up here because a specific MIS integration
(e.g. Engage, via st_austins) maps a discount straight onto a line that way. So
redistributing that discount across the real items is that integration's own
data-model knowledge, not something titan_digitax should guess at generically.

A handler is discovered in one of two ways:

1. It declares a handler in its ``hooks.py`` (preferred, explicit)::

       digitax_discount_redistribution_handler = (
           "my_school.utils.digitax_discount.redistribute_discount"
       )

2. Otherwise every installed app is probed for the conventional path
   ``<app>.utils.digitax_discount.redistribute_discount`` — note the single
   ``<app>`` prefix (a plain utils module, not one living under the app's
   Frappe module/doctype folder, unlike actionable.py's convention).

If neither resolves, sales_items._apply_discounts falls back to its own generic
behaviour: refuse to guess, block the send, raise an Actionable Item.
"""

from __future__ import annotations

import frappe

HANDLER_HOOK = "digitax_discount_redistribution_handler"

_CONVENTIONAL_PATH = "{app}.utils.digitax_discount.redistribute_discount"


def _candidate_paths():
	"""Yield dotted paths to try, hook-declared ones first."""
	seen = set()

	for path in frappe.get_hooks(HANDLER_HOOK) or []:
		if path and path not in seen:
			seen.add(path)
			yield path

	for app in frappe.get_installed_apps():
		if app == "titan_digitax":
			continue
		path = _CONVENTIONAL_PATH.format(app=app)
		if path not in seen:
			seen.add(path)
			yield path


def resolve_discount_handler():
	"""Return the first importable discount-redistribution handler, or None."""
	for path in _candidate_paths():
		try:
			return frappe.get_attr(path)
		except Exception:
			continue
	return None
