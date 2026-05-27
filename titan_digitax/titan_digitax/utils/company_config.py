"""
Digitax per-company eligibility helpers.

Call is_digitax_enabled_for_company() anywhere you need to decide whether to
send a Sales Invoice to Digitax.  Do NOT use Company.custom_enable_company
for this decision – that field is shared with Sage, MIS, and bulk banking.
"""

import frappe


def _load_settings(digitax_settings=None):
    return digitax_settings or frappe.get_single("Digitax Settings")


def get_enabled_digitax_companies(digitax_settings=None):
    """Return a list of company names that have an enabled row in Digitax Settings."""
    s = _load_settings(digitax_settings)
    return [
        row.company
        for row in (s.get("company_configurations") or [])
        if row.company and row.enabled
    ]


def is_digitax_enabled_for_company(company, digitax_settings=None):
    """Return True only if the company has an enabled row in Digitax Settings."""
    if not company:
        return False
    return company in set(get_enabled_digitax_companies(digitax_settings))
