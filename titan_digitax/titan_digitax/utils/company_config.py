"""
Digitax per-company eligibility and credential resolution.

Call is_digitax_enabled_for_company() anywhere you need to decide whether to
send a Sales Invoice to Digitax.  Do NOT use Company.custom_enable_company
for this decision – that field is shared with Sage, MIS, and bulk banking.

Credentials resolve per company via get_digitax_company_config(). Each company
files under its own KRA account, so sending with another company's key is a
tax-compliance incident rather than a bug — see strict mode below.
"""

import frappe
from frappe import _
from frappe.utils.password import get_decrypted_password

COMPANY_DOCTYPE = "Digitax Company Settings"


def _load_settings(digitax_settings=None):
    return digitax_settings or frappe.get_single("Digitax Settings")


def get_enabled_digitax_companies(digitax_settings=None):
    """Return company names enabled for DigiTax.

    Union of the per-company doctype and the legacy child table on Digitax Settings,
    so the list never empties mid-deploy while sites are still being migrated.
    """
    names = set()

    if frappe.db.table_exists(COMPANY_DOCTYPE):
        names.update(frappe.get_all(COMPANY_DOCTYPE, filters={"enabled": 1}, pluck="company"))

    s = _load_settings(digitax_settings)
    names.update(
        row.company for row in (s.get("company_configurations") or []) if row.company and row.enabled
    )

    return sorted(names)


def is_digitax_enabled_for_company(company, digitax_settings=None):
    """Return True only if the company is enabled for DigiTax."""
    if not company:
        return False
    return company in set(get_enabled_digitax_companies(digitax_settings))


def get_digitax_company_config(company=None, digitax_settings=None) -> frappe._dict:
    """Resolve base_url and api_key for a company.

    ``base_url`` may safely fall back to the global value: every company talks to the
    same DigiTax host. ``api_key`` identifies the KRA account and must not fall back
    once strict mode is on — a mis-configured company would otherwise file its invoices
    into another school's account, which is not reversible from ERPNext.

    Returns base_url, api_key, source ("company" | "global") and fingerprint.
    """
    s = _load_settings(digitax_settings)
    cfg = frappe._dict(base_url=None, api_key=None, source="global", fingerprint=None, enabled=False)

    row = None
    if company and frappe.db.exists(COMPANY_DOCTYPE, company):
        row = frappe.get_cached_doc(COMPANY_DOCTYPE, company)

    if row:
        cfg.enabled = bool(row.enabled)
        cfg.base_url = (row.base_url or "").strip() or None
        cfg.api_key = get_decrypted_password(
            COMPANY_DOCTYPE, company, "api_key", raise_exception=False
        )
        if cfg.api_key:
            cfg.source = "company"
            cfg.fingerprint = row.api_key_fingerprint

    if not cfg.base_url:
        cfg.base_url = (s.get("base_url") or "").strip() or None

    if not cfg.api_key:
        if s.get("strict_per_company_credentials"):
            frappe.throw(
                _(
                    "No DigiTax API Key is configured for {0}. Strict Per-Company Credentials "
                    "is on, so the global key will not be used — a shared key would file this "
                    "company's invoices under another company's KRA account."
                ).format(frappe.bold(company or _("this company"))),
                title=_("DigiTax Credentials Missing"),
            )
        cfg.api_key = get_decrypted_password(
            "Digitax Settings", "Digitax Settings", "api_key", raise_exception=False
        )

    if cfg.base_url:
        cfg.base_url = cfg.base_url.rstrip("/")

    return cfg


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

    settings = _load_settings()

    if not settings.get("enable"):
        return {"eligible": False}

    eligible = is_digitax_enabled_for_company(company, settings)
    return {"eligible": eligible}
