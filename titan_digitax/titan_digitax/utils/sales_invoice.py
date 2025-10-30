from .sales import send_sales_invoice_to_digitax

def on_submit(doc, method):
    send_sales_invoice_to_digitax(doc)