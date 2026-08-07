"""
Digitax per-company eligibility and credential resolution.

Call is_digitax_enabled_for_company() anywhere you need to decide whether to
send a Sales Invoice to Digitax.  Do NOT use Company.custom_enable_company
for this decision – that field is shared with Sage, MIS, and bulk banking.

Every DigiTax setting — credentials, timeouts, item defaults, invoice status
codes — lives on this company's own Digitax Company Settings row. There is no
shared/global tier: sending with another company's key would file this
company's invoices into another school's KRA account, so nothing here ever
falls back to a different company's values.
"""

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

COMPANY_DOCTYPE = "Digitax Company Settings"


def get_enabled_digitax_companies():
    """Return company names enabled for DigiTax, per Digitax Company Settings."""
    if not frappe.db.table_exists(COMPANY_DOCTYPE):
        return []
    return sorted(frappe.get_all(COMPANY_DOCTYPE, filters={"enabled": 1}, pluck="company"))


def is_digitax_enabled_for_company(company):
    """Return True only if the company is enabled for DigiTax."""
    if not company:
        return False
    return company in set(get_enabled_digitax_companies())


def get_digitax_settings(company):
    """Return this company's Digitax Company Settings doc. Throws if none exists."""
    if not company:
        frappe.throw(_("A company is required to resolve DigiTax settings."))
    if not frappe.db.exists(COMPANY_DOCTYPE, company):
        frappe.throw(
            _("No Digitax Company Settings exist for {0}.").format(frappe.bold(company)),
            title=_("DigiTax Not Configured"),
        )
    return frappe.get_cached_doc(COMPANY_DOCTYPE, company)


def get_digitax_company_config(company) -> frappe._dict:
    """Resolve base_url and api_key for a company, with no fallback tier.

    Returns base_url, api_key, and fingerprint straight off the company's own row.
    """
    row = get_digitax_settings(company)

    api_key = get_decrypted_password(COMPANY_DOCTYPE, company, "api_key", raise_exception=False)
    base_url = (row.base_url or "").strip().rstrip("/") or None

    return frappe._dict(
        base_url=base_url,
        api_key=api_key,
        fingerprint=row.api_key_fingerprint,
        enabled=bool(row.enabled),
    )


def get_digitax_callback_token(company=None):
    """Per-company token appended to the callback URL, when one is configured."""
    if not company or not frappe.db.exists(COMPANY_DOCTYPE, company):
        return None
    return get_decrypted_password(COMPANY_DOCTYPE, company, "callback_token", raise_exception=False)


def get_company_for_callback_token(token):
    """Reverse the callback token back to a company, for attributing a callback."""
    if not token:
        return None
    for company in frappe.get_all(COMPANY_DOCTYPE, pluck="company"):
        if get_digitax_callback_token(company) == token:
            return company
    return None


@frappe.whitelist()
def get_company_digitax_status(company):
    """
    Lightweight eligibility check called by the Sales Invoice form to decide
    whether to show the Digitax action buttons.

    Returns {"eligible": true/false} so the frontend never has to guess.
    """
    if not company:
        return {"eligible": False}

    return {"eligible": is_digitax_enabled_for_company(company)}
