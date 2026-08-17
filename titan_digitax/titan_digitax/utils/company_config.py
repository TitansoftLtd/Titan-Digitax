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

# Reverse index (token -> company) for the guest-accessible callback endpoint. Without
# this, attributing a callback to a company meant decrypting and comparing every
# company's token in a loop on every single callback request - fine for a couple of
# companies, not something that should scale linearly with tenant count on an
# unauthenticated endpoint. Invalidated explicitly by
# clear_callback_token_index_cache() whenever a company's settings are saved/deleted.
CALLBACK_TOKEN_INDEX_CACHE_KEY = "titan_digitax:callback_token_index"


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


def _build_callback_token_index():
    """token -> company, decrypting every company's token once to build the index."""
    index = {}
    for company in frappe.get_all(COMPANY_DOCTYPE, pluck="company"):
        token = get_digitax_callback_token(company)
        if token:
            index[token] = company
    return index


def clear_callback_token_index_cache():
    """Call whenever a Digitax Company Settings row is saved or deleted - the cached
    index would otherwise keep mapping a rotated/removed token to the wrong (or a
    no-longer-configured) company until it next happened to be rebuilt.
    """
    frappe.cache().delete_value(CALLBACK_TOKEN_INDEX_CACHE_KEY)


def get_company_for_callback_token(token):
    """Reverse the callback token back to a company, for attributing a callback.

    Backed by a cached index instead of decrypting and comparing every company's
    token in a loop on every call - this runs on every hit to the guest-accessible
    callback endpoint.
    """
    if not token:
        return None
    index = frappe.cache().get_value(CALLBACK_TOKEN_INDEX_CACHE_KEY)
    if index is None:
        index = _build_callback_token_index()
        frappe.cache().set_value(CALLBACK_TOKEN_INDEX_CACHE_KEY, index)
    return index.get(token)


def get_digitax_sync_user(company):
    """The service account that automatic/background Digitax work runs as, and
    that manual actions switch to after the initiating user's role is checked -
    so the actor recorded against every Digitax write is always this one
    consistent account, never whichever human or background-job identity
    happened to trigger it.
    """
    user = frappe.db.get_value(COMPANY_DOCTYPE, company, "digitax_sync_user")
    if not user:
        frappe.throw(
            _("Set Digitax Sync User in {0}'s Digitax Company Settings before Digitax actions can run.").format(
                frappe.bold(company)
            ),
            title=_("Digitax Sync User Not Configured"),
        )
    return user


def require_digitax_role(company, role_fieldname, action_description):
    """Require the calling user to hold the role configured for role_fieldname on
    this company's Digitax Company Settings, before letting them initiate an
    action. Only gates who may START an action - see run_as_digitax_sync_user
    for who actually performs it once started.
    """
    role = frappe.db.get_value(COMPANY_DOCTYPE, company, role_fieldname)
    if not role:
        frappe.throw(
            _("Set {0} in {1}'s Digitax Company Settings before you can {2}.").format(
                frappe.bold(role_fieldname), frappe.bold(company), action_description
            ),
            title=_("Digitax Role Not Configured"),
        )
    if role not in (frappe.get_roles() or []):
        frappe.throw(
            _("You need the {0} role to {1}.").format(frappe.bold(role), action_description),
            title=_("Not Permitted"),
        )


def run_as_digitax_sync_user(company, fn):
    """Run fn() with the session temporarily switched to this company's Digitax
    Sync User, then always switch back - used both for manual actions (after
    require_digitax_role has already confirmed the human may initiate) and for
    automatic/background paths (which skip that check entirely, since they're
    the system's own pipeline reacting to a legitimate event, not an arbitrary
    user action).
    """
    sync_user = get_digitax_sync_user(company)
    original_user = frappe.session.user
    frappe.set_user(sync_user)
    try:
        return fn()
    finally:
        frappe.set_user(original_user)


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
