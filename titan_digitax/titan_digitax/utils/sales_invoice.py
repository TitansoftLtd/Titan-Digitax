import frappe
import requests
import json
from frappe.utils.password import get_decrypted_password

digitax_base_url = frappe.get_single("Digitax Settings").base_url
digitax_api_key = get_decrypted_password(
    "Digitax Settings", "Digitax Settings", "api_key"
)


def on_submit(doc, method):
    headers = {"accept": "application/json", "X-API-Key": digitax_api_key}
    url = ""
    payload = {
        "items": [],
    }

    if not doc.is_return:
        url = f"{digitax_base_url.rstrip('/')}/sales-with-items"
        payload["sale_date"] = str(doc.posting_date)
        payload["trader_invoice_number"] = str(doc.name)
        payload["receipt_type_code"] = "S"
        payload["payment_type_code"] = "01"  # Cash, to be dynamic later. TODO: We need to revisit this.
        payload["invoice_status_code"] = "02" # Stands for "Approved"
        for item in doc.items:
            payload["items"].append(
                {
                    "item_name": item.item_name,
                    # the item_class_code used here is for "services" as per Digitax documentation
                    "item_class_code": item.custom_item_class_code or "99020000",
                    "item_bar_code": item.item_code,
                    "item_tax_type_code": item.custom_tax_type_code or "D",
                    "quantity": item.qty,
                    "unit_price": item.rate,
                    "total_amount": item.amount,
                    "package_unit_quantity": item.qty, #TODO: Confirm with Digitax if this is correct
                    # TODO: Confirm with Digitax if discount_rate is percentage or amount
                    "discount_rate": item.discount_amount,
                    "discount_amount": item.discount_amount,
                    "item_description": item.description,
                    "is_stockable": True if item.custom_is_stockable else False,
                }
            )
    else:
        url = f"{digitax_base_url.rstrip('/')}/credit-notes-with-barcode"

    payload = json.dumps(payload)
    # print(f"Payload to be sent to Digitax: \n{payload}\n")

    try:
        response = requests.post(url, headers=headers, data=payload)
        print(f"Response from Digitax: \n{response.json()}\n")
        response.raise_for_status()
    except requests.exceptions.RequestException as e:
        frappe.log_error(
            message=f"Error while sending Sales Invoice {doc.name} to Digitax: {str(e)}",
            title="Digitax Sales Invoice Sync Error",
        )
