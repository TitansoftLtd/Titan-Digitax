# Copyright (c) 2026, Titan and contributors
# For license information, please see license.txt

"""Retired — Digitax Settings and its company_configurations child table are gone.

This used to seed Digitax Settings.company_configurations from
Company.custom_enable_company. That child table was retired in favour of
Digitax Company Configuration, which was itself retired in favour of the
standalone Digitax Company Settings doctype (retire_digitax_company_configuration),
and Digitax Settings itself was later deleted outright (retire_digitax_settings) —
this patch's job has been fully superseded for two releases running.

Kept as a no-op rather than deleted: a site that reaches this patch for the first
time after Digitax Settings' controller module was deleted must not crash trying to
import it — same failure mode fixed in seed_digitax_company_settings.py and
create_engage_settings_from_single.py. The original version also crashed on a BRAND
NEW site with a different exception (frappe.get_meta("Digitax Settings") raising
DoesNotExistError, since the DocType row never existed there either) — a no-op
sidesteps both failure modes at once.
"""


def execute():
	return
