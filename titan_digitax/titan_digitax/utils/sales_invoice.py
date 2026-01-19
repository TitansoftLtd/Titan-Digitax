import frappe
from .sales import send_sales_invoice_to_digitax

def on_submit(doc, method):
    if not frappe.conf.get("sync_with_digitax"):
        return 
    
    if frappe.db.get_value("Company", doc.company, "country") == "Kenya":
        if frappe.db.get_value("Company", doc.company, "custom_enable_company"):
            send_sales_invoice_to_digitax(doc.name)