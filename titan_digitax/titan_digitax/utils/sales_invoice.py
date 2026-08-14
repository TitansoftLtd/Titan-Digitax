import frappe
from frappe import _
from .sales import send_sales_invoice_to_digitax


def on_submit(doc, method):
    if not frappe.conf.get("sync_with_digitax"):
        return

    # Delegate all gating (global enable, target_country, company eligibility,
    # and send-block/amend-allow) to send_sales_invoice_to_digitax.
    send_sales_invoice_to_digitax(doc.name)


def on_cancel(doc, method):
    """Once a sale is filed with DigiTax/KRA, cancelling it in ERPNext alone would
    leave the two records permanently out of sync - KRA has no idea the sale was
    voided. Block the cancel and point the user at the correct correction path
    (a credit note against this invoice) instead of silently letting them diverge.
    """
    if not doc.custom_sent_to_digitax:
        return

    if doc.is_return:
        frappe.throw(
            _(
                "This credit note was already filed with Digitax/KRA and cannot be cancelled. "
                "If it was raised in error, create a new Sales Invoice for the client instead."
            ),
            title=_("Cannot Cancel"),
        )

    frappe.throw(
        _(
            "This invoice was already filed with Digitax/KRA and cannot be cancelled directly. "
            "Create a Credit Note against it instead, so the correction is also filed with KRA."
        ),
        title=_("Cannot Cancel"),
    )
