import frappe
from .sales import send_sales_invoice_to_digitax

def on_submit(doc, method):
    if frappe.db.get_value("Company", doc.company, "country") == "Kenya":
        send_sales_invoice_to_digitax(doc.name)