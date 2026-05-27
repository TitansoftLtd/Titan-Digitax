import frappe
from .sales import send_sales_invoice_to_digitax


def on_submit(doc, method):
    if not frappe.conf.get("sync_with_digitax"):
        return

    # Delegate all gating (global enable, target_country, company eligibility,
    # and send-block/amend-allow) to send_sales_invoice_to_digitax.
    send_sales_invoice_to_digitax(doc.name)
