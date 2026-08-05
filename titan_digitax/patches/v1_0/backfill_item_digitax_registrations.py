# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Retired — superseded by migrate_item_digitax_fields_to_digitax_item (D-new).

This used to move Item.custom_digitax_id into per-company Item Digitax Registration
rows. Both the legacy Item fields and Item Digitax Registration are being retired in
the same release in favour of the standalone Digitax Item doctype, and
migrate_item_digitax_fields_to_digitax_item.py migrates directly from the legacy Item
fields (reading Item Digitax Registration too, if present) straight into Digitax Item
— this patch's job is fully superseded.

Kept as a no-op rather than deleted: a site that reaches this patch for the first time
after item_registry.py and Item Digitax Registration have already been deleted in a
later commit must not crash trying to import a module or reference a doctype that no
longer exists on disk — the same failure mode fixed in seed_digitax_company_settings.py.
"""


def execute():
	return
