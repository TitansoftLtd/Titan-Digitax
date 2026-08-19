# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Create Actionable Items without hard-coding a client app import.

`titan_digitax` is shared across clients, so it must not name any of them.
A client app is discovered in one of two ways:

1. It declares a handler in its ``hooks.py`` (preferred, explicit)::

       digitax_actionable_item_handler = (
           "my_client_app.my_client_app.doctype.actionable_items"
           ".actionable_items.create_actionable_item"
       )

2. Otherwise every installed app is probed for the conventional path
   ``<app>.<app>.doctype.actionable_items.actionable_items.create_actionable_item``.

If neither resolves, the call is a no-op and the reason is logged — DigiTax
sync paths must never fail because a client app is absent.
"""

from __future__ import annotations

import frappe

HANDLER_HOOK = "digitax_actionable_item_handler"

_CONVENTIONAL_PATH = "{app}.{app}.doctype.actionable_items.actionable_items.create_actionable_item"


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


def resolve_handler():
	"""Return the first importable Actionable Item creator, or None."""
	for path in _candidate_paths():
		try:
			return frappe.get_attr(path)
		except Exception:
			continue
	return None


def create_actionable_item(**kwargs):
	"""Best-effort Actionable Item create. Never raises."""
	handler = resolve_handler()
	if handler is None:
		frappe.logger("digitax_integration").warning(
			"No Actionable Items handler found in any installed app; skipped create: %s",
			kwargs.get("title"),
		)
		return None

	try:
		return handler(**kwargs)
	except Exception:
		frappe.logger("digitax_integration").warning(
			"Actionable Item create failed: %s", kwargs.get("title"), exc_info=True
		)
		return None
